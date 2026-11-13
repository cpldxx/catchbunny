// One run's event stream, reduced to the state the run page draws.
//
// The server replays a run's history and then stays live (SSE, resumable by
// Last-Event-ID), so the same code shows a run in progress and a finished one.
// A swarm emits ~60 events a second; they are buffered and applied once per
// animation frame, so the page renders per frame, not per event. The state is
// a mutable store read through useSyncExternalStore: a frame of step events
// appends to arrays in place instead of copying them, which a reducer may not do.

import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { Budget, Finding, Mission, Run, ScenarioReport } from "./api";

export interface Tick { index: number; ts: number; ms: number; ok: boolean; action: string; url: string; error: string; signals: { kind: string; message: string }[]; screenshot: string }
export interface ScenarioLive { id: string; name: string; steps: { index: number; mode: string; passed?: boolean; detail?: string }[]; report?: ScenarioReport; startedAt: number }

export interface Live {
  run?: Run;
  phases: { phase: string; ts: number }[];
  budget?: Budget;
  missions: Map<string, Mission>;
  ticks: Map<string, Tick[]>;
  findings: Map<string, Finding>;
  foundAt: Map<string, number>;
  scenarios: Map<string, ScenarioLive>;
  drafted: { scenario_id: string; name: string }[];
  logins: { persona: string; ok: boolean; error?: string }[];
  warnings: string[];
  last?: { missionId: string; tick: Tick };
  done: boolean;
  lost: boolean;
  version: number;
}

export type Raw = { type: string; seq: number; data: any };

const TYPES = [
  "run.queued", "run.phase", "login", "mission.started", "step", "mission.finished", "finding",
  "scenario.started", "scenario.step", "scenario.finished", "scenario.drafted", "run.warning", "run.finished",
];

const MODES: Record<string, string> = { learned: "replayed", healed: "healed and replayed", model: "by a model", replay_failed: "replay failed" };

function lane(id: string, title: string, ts: number): Mission {
  return { id, run_id: "", kind: "scenario", title, charter: "", persona: "", device: "", start_path: "", max_steps: 0, scenario_id: id, status: "running", verdict: null, summary: "", brain: "", steps_taken: 0, signals: 0, started_at: ts, finished_at: 0 };
}

const empty = (): Live => ({
  phases: [], missions: new Map(), ticks: new Map(), findings: new Map(), foundAt: new Map(), scenarios: new Map(),
  drafted: [], logins: [], warnings: [], done: false, lost: false, version: 0,
});

// Collections are mutated in place and the wrapper object is replaced: a frame
// of 60 step events must not copy 60 arrays. `version` is the change signal.
function apply(state: Live, events: Raw[]): Live {
  const s = { ...state, version: state.version + 1 };
  for (const { type, data } of events) {
    switch (type) {
      case "run.queued": s.run = data.run; break;
      case "run.phase": s.phases.push({ phase: data.phase, ts: data.ts }); s.budget = data.budget; break;
      case "login": s.logins.push(data); break;
      case "mission.started":
      case "mission.finished": {
        const m: Mission = data.mission;
        s.missions.set(m.id, m);
        if (!s.ticks.has(m.id)) s.ticks.set(m.id, []);
        break;
      }
      case "step": {
        const tick: Tick = { index: data.index, ts: data.ts, ms: data.ms, ok: data.ok, action: data.action, url: data.url, error: data.error, signals: data.signals ?? [], screenshot: data.screenshot };
        let lane = s.ticks.get(data.mission_id);
        if (!lane) s.ticks.set(data.mission_id, (lane = []));
        lane.push(tick);
        s.last = { missionId: data.mission_id, tick };
        break;
      }
      case "finding": {
        const f: Finding = data.finding;
        s.findings.set(f.id, f);
        if (!s.foundAt.has(f.id)) s.foundAt.set(f.id, data.ts);
        break;
      }
      // A replayed scenario has no tester behind it, so it gets a lane of its own: one block per step.
      case "scenario.started":
        s.scenarios.set(data.scenario_id, { id: data.scenario_id, name: data.name, steps: [], startedAt: data.ts });
        s.missions.set(data.scenario_id, lane(data.scenario_id, data.name, data.ts));
        s.ticks.set(data.scenario_id, []);
        break;
      case "scenario.step": {
        s.scenarios.get(data.scenario_id)?.steps.push({ index: data.index, mode: data.mode, passed: data.passed, detail: data.detail });
        const ticks = s.ticks.get(data.scenario_id);
        if (ticks) {
          const since = ticks.at(-1)?.ts ?? s.scenarios.get(data.scenario_id)?.startedAt ?? data.ts;
          const ok = data.passed === true;
          ticks.push({ index: data.index, ts: data.ts, ms: Math.max(1, (data.ts - since) * 1000), ok, action: `Step ${data.index + 1}, ${MODES[data.mode] ?? data.mode}`, url: "", error: data.detail ?? "", signals: [], screenshot: "" });
        }
        break;
      }
      case "scenario.finished": {
        const live = s.scenarios.get(data.scenario_id) ?? { id: data.scenario_id, name: data.name, steps: [], startedAt: data.ts };
        s.scenarios.set(data.scenario_id, { ...live, report: data });
        const m = s.missions.get(data.scenario_id);
        if (m) s.missions.set(data.scenario_id, { ...m, status: "done", verdict: data.verdict, summary: data.reason, finished_at: data.ts });
        break;
      }
      case "scenario.drafted": s.drafted.push(data); break;
      case "run.warning": s.warnings.push(data.message); break;
      case "run.finished": s.run = data.run; s.done = true; break;
      case "$lost": s.lost = true; break;
      case "$open": s.lost = false; break;
    }
  }
  return s;
}

/** The state after a list of recorded events, for replaying a run without a server. */
export function liveFrom(events: Raw[]): Live {
  const state = apply(empty(), events);
  state.version = events.length; // `version` is what views memoise on, so it must differ between two replays
  return state;
}

class RunStore {
  private state = empty();
  private listeners = new Set<() => void>();
  private seq = 0;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  snapshot = () => this.state;

  reset(): void {
    this.state = empty();
    this.seq = 0;
    this.listeners.forEach((l) => l());
  }

  push(events: Raw[]): void {
    // A reconnect resumes after Last-Event-ID, but never trust that: an event is applied once.
    const fresh = events.filter((e) => e.seq === 0 || e.seq > this.seq);
    if (!fresh.length) return;
    this.seq = Math.max(this.seq, ...fresh.map((e) => e.seq));
    this.state = apply(this.state, fresh);
    this.listeners.forEach((l) => l());
  }
}

export function useRunStream(runId: string | undefined): Live {
  const store = useMemo(() => new RunStore(), [runId]);

  useEffect(() => {
    if (!runId) return;
    store.reset();
    const source = new EventSource(`/v1/runs/${runId}/events`);
    let buffer: Raw[] = [];
    let frame = 0;
    let timer = 0;
    const flush = () => {
      frame = timer = 0;
      store.push(buffer);
      buffer = [];
    };
    const push = (event: Raw) => {
      buffer.push(event);
      if (frame || timer) return;
      // A tab in the background gets no animation frames. Use a timer there, so the run is current the moment the tab is shown.
      if (document.hidden) timer = window.setTimeout(flush, 250);
      else frame = requestAnimationFrame(flush);
    };
    for (const type of TYPES) {
      source.addEventListener(type, (e) => {
        const message = e as MessageEvent;
        push({ type, seq: Number(message.lastEventId) || 0, data: JSON.parse(message.data) });
        if (type === "run.finished") source.close(); // the stream is over; do not let EventSource reconnect to it
      });
    }
    source.onopen = () => push({ type: "$open", seq: 0, data: null });
    source.onerror = () => push({ type: "$lost", seq: 0, data: null }); // EventSource retries on its own, resuming by Last-Event-ID
    return () => {
      source.close();
      cancelAnimationFrame(frame);
      clearTimeout(timer);
    };
  }, [runId, store]);

  return useSyncExternalStore(store.subscribe, store.snapshot);
}
