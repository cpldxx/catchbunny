// The landing page shows the product by running it. Everything it draws comes
// from one real deep scan of the bundled demo shop:
//
//   demo-run.json          the run's event log, its product map, one finding with
//                          the recorded path behind it, and what the MCP tools return
//   public/demo/shots/     screenshots of the shop's pages, as each persona sees them
//   public/demo/replay.webm  the recording of the replay that confirmed that finding
//
// The event log is replayed through the console's own reducer, so the page and
// the console cannot drift apart.

import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import type { Run } from "../api";
import { liveFrom, type Live, type Raw } from "../stream";
import recording from "./demo-run.json";

export interface DemoFinding {
  id: string; severity: "P0" | "P1" | "P2" | "P3"; kind: string; title: string; expected: string; actual: string;
  persona: string; device: string; url: string; verification: { runs: number; reproduced: number };
  occurrences: number; testers: number; original_steps: number; steps: string[];
  recorded: { text: string; kept: boolean }[];
  timeline: { index: number; action: string; t_ms: number; ms: number; ok: boolean }[];
  network: { t_ms: number; method: string; url: string; status: number | string; type: string }[];
  console: { t_ms: number; level: string; text: string }[];
  fired_at_ms: number; duration_ms: number; video_offset_ms: number;
}
export interface Screen { id: string; path: string; title: string; controls: number; exercised: number; visits: number; at: number; shot: string }

const data = recording as unknown as {
  run: Run;
  events: { seq: number; type: string; ts: number; data: Record<string, any> }[];
  finding: DemoFinding;
  scenario: string;
  map: { screens: Screen[]; links: { from: string; to: string; via: string }[] };
  agent: Record<"scan" | "get_finding" | "verify_fix" | "keep_as_scenario", unknown>;
  shots: Record<string, string>;
};

const events: Raw[] = data.events.map((e) => ({ type: e.type, seq: e.seq, data: { ...e.data, ts: e.ts } }));
const stamps = data.events.map((e) => e.ts);

export const demo = { run: data.run, finding: data.finding, scenario: data.scenario, map: data.map, agent: data.agent };

/** What the run added up to: the figures the landing page says out loud. */
export const totals = {
  testers: data.run.spec.testers,
  steps: data.events.filter((e) => e.type === "step").length,
  sightings: data.events.filter((e) => e.type === "step" && (e.data.signals as unknown[]).length > 0).length,
  confirmed: data.events.filter((e) => e.type === "finding").length,
  screens: data.map.screens.length,
  mappedIn: data.map.screens.at(-1)!.at,
};

/** The screenshot of a page as a persona sees it. */
export function shot(persona: string, url: string): string {
  const path = url.split("?")[0] || "/";
  const name = data.shots[`${persona}:${path}`] ?? data.shots[`visitor:${path}`] ?? data.shots["visitor:/"];
  return `/demo/shots/${name}`;
}

/** How many times each detector fired during the run. */
export const fired: Record<string, number> = {};
for (const e of data.events) {
  if (e.type === "step") for (const s of e.data.signals as { kind: string }[]) fired[s.kind] = (fired[s.kind] ?? 0) + 1;
}

// ---------------------------------------------------------------- the run's clock

const START = data.run.started_at;
const VERIFY = data.events.find((e) => e.type === "run.phase" && e.data.phase === "triage")?.ts ?? data.run.finished_at;
const END = data.run.finished_at;

/** The hunt plays at the speed it was recorded. Verification is mostly waiting on replays, so it is sped up. */
export const VERIFY_SPEED = 4;
const HOLD = 5; // seconds on the finished run before it starts again
const huntWall = VERIFY - START;
const verifyWall = (END - VERIFY) / VERIFY_SPEED;
const PLAY = huntWall + verifyWall;

function runClock(wall: number): number {
  if (wall < huntWall) return START + wall;
  if (wall < PLAY) return VERIFY + (wall - huntWall) * VERIFY_SPEED;
  return END;
}
function wallOf(ts: number): number {
  return ts <= VERIFY ? ts - START : huntWall + (ts - VERIFY) / VERIFY_SPEED;
}

/** Where the hunt ends, and where each finding was confirmed, as fractions of the playable run. */
export const huntShare = huntWall / PLAY;
export const findingMarks = data.events.filter((e) => e.type === "finding").map((e) => wallOf(e.ts) / PLAY);
/** The run's findings in the order they were confirmed, each with when: a fraction of the verification phase. */
export const verified: { id: string; severity: "P0" | "P1" | "P2" | "P3"; title: string; occurrences: number; at: number }[] = data.events
  .filter((e) => e.type === "finding")
  .map((e) => ({ id: e.data.finding.id, severity: e.data.finding.severity, title: e.data.finding.title, occurrences: e.data.finding.occurrences, at: (e.ts - VERIFY) / (END - VERIFY) }));

function applied(clock: number): number {
  let lo = 0, hi = stamps.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (stamps[mid] <= clock) lo = mid + 1; else hi = mid;
  }
  return lo;
}

const still = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

export interface Demo {
  live: Live; run: Run; clock: number;
  /** Position in the playable run, 0 to 1. */
  progress: number;
  playing: boolean; done: boolean;
  toggle: () => void;
  seek: (fraction: number) => void;
  /** A view that shows the run calls this while it is on screen; the run only plays while someone is watching. */
  watch: (watching: boolean) => void;
}

const DemoContext = createContext<Demo | null>(null);

export function DemoProvider({ children }: { children: ReactNode }) {
  const reduced = useMemo(still, []);
  const [at, setAt] = useState(reduced ? PLAY : 0);
  const [playing, setPlaying] = useState(!reduced);
  const [watchers, setWatchers] = useState(0);
  const position = useRef(at);
  const active = playing && watchers > 0;

  useEffect(() => {
    if (!active) return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      position.current = (position.current + (now - last) / 1000) % (PLAY + HOLD);
      last = now;
      setAt(position.current);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [active]);

  const toggle = useCallback(() => setPlaying((p) => !p), []);
  const seek = useCallback((fraction: number) => {
    position.current = Math.min(1, Math.max(0, fraction)) * PLAY;
    setAt(position.current);
  }, []);
  const watch = useCallback((watching: boolean) => setWatchers((n) => Math.max(0, n + (watching ? 1 : -1))), []);

  const clock = runClock(at);
  const count = applied(clock);
  const live = useMemo(() => liveFrom(events.slice(0, count)), [count]);
  const done = at >= PLAY;
  const run = useMemo<Run>(() => ({ ...data.run, status: done ? data.run.status : "running", finished_at: done ? END : 0, summary: done ? data.run.summary : {} }), [done]);
  const value: Demo = { live, run, clock, progress: Math.min(1, at / PLAY), playing, done, toggle, seek, watch };
  return createElement(DemoContext.Provider, { value }, children);
}

/** The recorded run at the current moment of its replay. Pass whether the caller is on screen. */
export function useDemo(visible: boolean): Demo {
  const value = useContext(DemoContext);
  if (!value) throw new Error("useDemo needs a DemoProvider above it");
  const { watch } = value;
  useEffect(() => {
    if (!visible) return;
    watch(true);
    return () => watch(false);
  }, [visible, watch]);
  return value;
}

// ---------------------------------------------------------------- small helpers for the stages

export function useOnScreen(ref: RefObject<Element | null>, threshold = 0.3): boolean {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || !("IntersectionObserver" in window)) {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, threshold]);
  return visible;
}

/** A figure that plays once, from 0 to 1 over `seconds`, the first time it is seen. `replay` runs it again. */
export function useTimeline(seconds: number, visible: boolean): { t: number; replay: () => void } {
  const reduced = useMemo(still, []);
  const [t, setT] = useState(reduced ? 1 : 0);
  const [round, setRound] = useState(0);
  const finished = useRef(-1); // the round that has played to its end; scrolling back does not play it again
  useEffect(() => {
    if (reduced || !visible || finished.current === round) return;
    let frame = 0;
    const origin = performance.now();
    const tick = (now: number) => {
      const next = Math.min(1, (now - origin) / (seconds * 1000));
      setT(next);
      if (next < 1) frame = requestAnimationFrame(tick);
      else finished.current = round;
    };
    setT(0);
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [visible, round, seconds, reduced]);
  return { t, replay: () => setRound((r) => r + 1) };
}
