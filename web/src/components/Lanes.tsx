// The lanes: one lane per tester, time running left to right.
//
// A block is one step, as wide as it took. A diamond above a block is a sighting:
// a detector fired on that step. The last lane is verification: each disc is a
// finding at the moment its fresh-browser replays finished, checked if it replayed. Gaps in a lane are
// time the tester spent waiting - on the page, or on its model.
//
// Drawn on a canvas because a run is thousands of marks whose positions all
// change every frame while the time axis is still growing. The tester table
// below the lanes carries the same information as text.

import { useEffect, useMemo, useRef, useState } from "react";
import type { Finding, Mission, Run } from "../api";
import { ms, offset, pathOf, plural, who } from "../format";
import type { Live, Tick } from "../stream";
import { fonts, palette } from "../theme";
import { Seg } from "./controls";

const AXIS = 40;
const LANE = 32;
const PHASE_LABEL: Record<string, string> = {
  preparing: "Preparing", exploring: "Exploring", mapping: "Mapping", planning: "Planning", hunting: "Hunting", triage: "Verifying",
  "running scenarios": "Running scenarios", "drafting scenarios": "Drafting scenarios", finished: "Finished",
};

type Hover = { kind: "tick"; mission: Mission; tick: Tick; x: number } | { kind: "finding"; finding: Finding; x: number };

interface Props {
  live: Live;
  run: Run;
  selectedMission?: string;
  selectedFinding?: string;
  onMission: (id: string) => void;
  onFinding: (id: string) => void;
  /** The current time on the run's own clock. Given when a recording is being replayed; otherwise the wall clock is used. */
  clock?: number;
  /** Draw without the card around it, for placing inside another surface. */
  bare?: boolean;
}

function gridStep(span: number): number {
  return [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800].find((s) => span / s <= 10) ?? 3600;
}

export function Lanes({ live, run, selectedMission, selectedFinding, onMission, onFinding, clock, bare }: Props) {
  const track = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(800);
  const [wall, setWall] = useState(() => Date.now() / 1000);
  const now = clock ?? wall;
  const [hover, setHover] = useState<Hover | null>(null);
  const [fontsReady, setFontsReady] = useState(false);
  const [scope, setScope] = useState<"run" | "testing">("run");

  const missions = useMemo(() => [...live.missions.values()].sort((a, b) => a.started_at - b.started_at), [live.version]);
  const findings = useMemo(() => [...live.findings.values()], [live.version]);
  const triage = live.phases.find((p) => p.phase === "triage");
  const zoomed = scope === "testing" && !!triage; // the hunt is often a fraction of the run: let it fill the axis
  // Lanes are reserved before they fill: one per tester the run asked for, and the verification lane
  // from the start. A chart that grows a row at a time pushes everything under it down the page.
  const slots = Math.max(missions.length, live.done ? 0 : run.spec.testers ?? 0, 1);
  const verifying = !zoomed && run.spec.kind !== "scenarios";
  const rows = slots + (verifying ? 1 : 0);
  const height = AXIS + rows * LANE;

  const t0 = run.started_at || live.phases[0]?.ts || now;
  const t1 = Math.max(t0 + 1, zoomed ? triage!.ts : run.finished_at || (live.done ? live.phases.at(-1)?.ts ?? now : now));
  const x = (ts: number) => ((ts - t0) / (t1 - t0)) * width;

  useEffect(() => {
    if (live.done || clock !== undefined) return;
    const id = setInterval(() => setWall(Date.now() / 1000), 150);
    return () => clearInterval(id);
  }, [live.done, clock !== undefined]);

  useEffect(() => {
    const el = track.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(120, Math.floor(entry.contentRect.width))));
    observer.observe(el);
    document.fonts?.ready.then(() => setFontsReady(true));
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr;
    c.height = height * dpr;
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, width, height);

    // Lane rules and the selected lane.
    missions.forEach((m, i) => {
      const y = AXIS + i * LANE;
      if (m.id === selectedMission) {
        g.fillStyle = palette.hover;
        g.fillRect(0, y, width, LANE);
      }
    });
    g.strokeStyle = palette.hairline;
    g.lineWidth = 1;
    g.beginPath();
    for (let i = 0; i <= rows; i++) {
      const y = AXIS + i * LANE - 0.5;
      g.moveTo(0, y);
      g.lineTo(width, y);
    }
    g.stroke();

    // Time grid.
    const span = t1 - t0;
    const step = gridStep(span);
    g.font = `400 12px ${fonts.mono}`;
    g.textBaseline = "middle";
    for (let s = 0; s <= span; s += step) {
      const gx = Math.round(x(t0 + s)) + 0.5;
      g.strokeStyle = palette.hairline;
      g.beginPath();
      g.moveTo(gx, AXIS - 8);
      g.lineTo(gx, height);
      g.stroke();
      if (gx < width - 36) {
        g.fillStyle = palette.muted;
        g.fillText(`${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`, gx + 4, AXIS - 12);
      }
    }

    // Phases: a boundary through every lane, named on the top line.
    g.font = `500 12px ${fonts.ui}`;
    live.phases.forEach((p, i) => {
      if (p.phase === "finished" || p.ts >= t1) return;
      const px = Math.max(0, Math.round(x(p.ts))) + 0.5;
      const end = x(Math.min(t1, live.phases[i + 1]?.ts ?? t1));
      if (i > 0) {
        g.strokeStyle = palette["hairline-strong"];
        g.setLineDash([4, 4]);
        g.beginPath();
        g.moveTo(px, 0);
        g.lineTo(px, height);
        g.stroke();
        g.setLineDash([]);
      }
      const label = PHASE_LABEL[p.phase] ?? p.phase;
      if (end - px > g.measureText(label).width + 12) {
        g.fillStyle = palette.secondary;
        g.fillText(label, px + 8, 12);
      }
    });

    // Testers.
    missions.forEach((m, i) => {
      const y = AXIS + i * LANE;
      const from = x(m.started_at || t0);
      const to = x(m.finished_at || t1);
      g.fillStyle = "rgba(226, 238, 224, 0.07)";
      g.fillRect(from, y + 10, Math.max(1, to - from), 12);
      const ticks = live.ticks.get(m.id) ?? [];
      for (const t of ticks) {
        const bx = x(t.ts - t.ms / 1000);
        const bw = Math.max(1.5, x(t.ts) - bx);
        if (t.ok) {
          g.fillStyle = "rgba(226, 238, 224, 0.6)";
          g.fillRect(bx, y + 10, bw, 12);
        } else {
          g.strokeStyle = palette.muted;
          g.strokeRect(bx + 0.5, y + 10.5, Math.max(1, bw - 1), 11);
        }
        if (t.signals.length) diamond(g, bx + bw / 2, y + 5);
      }
      if (m.finished_at) {
        g.fillStyle = m.verdict === "failed" ? palette.negative : m.verdict === "passed" ? palette.positive : palette.muted;
        g.fillRect(Math.min(width - 2, to), y + 6, 2, 20);
      }
    });

    // Verification lane.
    if (verifying) {
      const y = AXIS + slots * LANE + LANE / 2;
      for (const f of findings) {
        const fx = x(live.foundAt.get(f.id) ?? t1);
        disc(g, Math.min(width - 10, Math.max(10, fx)), y, f.id === selectedFinding ? 8 : 6, f.status === "confirmed");
      }
    }

    // Cursor.
    if (hover) {
      g.strokeStyle = palette.muted;
      g.lineWidth = 1;
      g.beginPath();
      g.moveTo(Math.round(hover.x) + 0.5, AXIS - 8);
      g.lineTo(Math.round(hover.x) + 0.5, height);
      g.stroke();
      if (hover.kind === "tick") {
        const i = missions.indexOf(hover.mission);
        const bx = x(hover.tick.ts - hover.tick.ms / 1000);
        g.fillStyle = palette.text;
        g.fillRect(bx, AXIS + i * LANE + 10, Math.max(2, x(hover.tick.ts) - bx), 12);
      }
    }
  }, [live.version, width, height, now, hover, selectedMission, selectedFinding, fontsReady, t0, t1]);

  function locate(e: React.MouseEvent): Hover | null {
    const box = canvas.current!.getBoundingClientRect();
    const px = e.clientX - box.left;
    const row = Math.floor((e.clientY - box.top - AXIS) / LANE);
    if (row < 0) return null;
    if (row < missions.length) {
      const m = missions[row];
      const ticks = live.ticks.get(m.id) ?? [];
      let best: Tick | null = null;
      let gap = 10; // px
      for (const t of ticks) {
        const a = x(t.ts - t.ms / 1000), b = x(t.ts);
        const d = px < a ? a - px : px > b ? px - b : 0;
        if (d < gap) [gap, best] = [d, t];
      }
      return best ? { kind: "tick", mission: m, tick: best, x: px } : null;
    }
    if (verifying && row === slots) {
      let best: Finding | null = null;
      let gap = 10;
      for (const f of findings) {
        const d = Math.abs(x(live.foundAt.get(f.id) ?? t1) - px);
        if (d < gap) [gap, best] = [d, f];
      }
      return best ? { kind: "finding", finding: best, x: px } : null;
    }
    return null;
  }

  const shown: Hover | null = hover ?? (live.last && !live.done && live.missions.get(live.last.missionId) ? { kind: "tick", mission: live.missions.get(live.last.missionId)!, tick: live.last.tick, x: 0 } : null);

  return (
    <section className={bare ? "" : "sheet"} aria-label="Tester activity over time">
      <div className="lanes">
        <div className="lanes-labels">
          <div className="lanes-axis-label">{plural(missions.length, run.spec.kind === "scenarios" ? "scenario" : "tester")}</div>
          {missions.map((m) => (
            <button key={m.id} className={`lane-label ${m.id === selectedMission ? "on" : ""}`} onClick={() => onMission(m.id)} title={m.title}>
              <span className="ellipsis">{laneName(m)}</span>
              <span className="kind">{m.brain && m.brain !== "heuristic" ? m.brain : m.kind === "scenario" ? "" : m.kind}</span>
            </button>
          ))}
          {Array.from({ length: slots - missions.length }, (_, i) => (
            <div key={i} className="lane-label static">{i === 0 && !missions.length ? <span className="faint">Waiting for the first tester</span> : null}</div>
          ))}
          {verifying && (
            <div className="lane-label static">
              <span>Verification</span>
              <span className="kind">fresh browsers</span>
            </div>
          )}
        </div>
        <div className="lanes-track" ref={track}>
          <canvas
            ref={canvas}
            style={{ height }}
            role="img"
            aria-label="Timeline of every tester’s steps. The tester table lists the same steps as text."
            onMouseMove={(e) => setHover(locate(e))}
            onMouseLeave={() => setHover(null)}
            onClick={(e) => {
              const h = locate(e);
              if (h?.kind === "tick") onMission(h.mission.id);
              if (h?.kind === "finding") onFinding(h.finding.id);
            }}
          />
        </div>
      </div>
      <div className="readout" aria-live="off">
        {shown?.kind === "tick" && (
          <>
            <span className="t">{offset(shown.tick.ts - t0)}</span>
            <span className="ellipsis" style={{ maxWidth: 256 }}>{laneName(shown.mission)}</span>
            <span className="grow ellipsis"><strong>{shown.tick.action}</strong>{!shown.tick.ok && <span> did not complete: {shown.tick.error}</span>}</span>
            {shown.tick.signals.slice(0, 2).map((s, i) => <span key={i} className="sig ellipsis" style={{ maxWidth: 320 }}>{s.message}</span>)}
            <code className="faint ellipsis" style={{ maxWidth: 256 }}>{pathOf(shown.tick.url)}</code>
            <span className="tag num">{ms(shown.tick.ms)}</span>
          </>
        )}
        {shown?.kind === "finding" && (
          <>
            <span className="t">{offset((live.foundAt.get(shown.finding.id) ?? t0) - t0)}</span>
            <span className="grow ellipsis"><strong>{shown.finding.title}</strong></span>
            <span className="nowrap">reproduced in {shown.finding.verification.reproduced} of {shown.finding.verification.runs} fresh browsers</span>
          </>
        )}
        {!shown && <span className="grow">Each block is one step, as wide as it took. A diamond above a block marks a step where a detector fired.</span>}
        {triage && <span style={{ marginLeft: "auto", flex: "none" }}><Seg label="How much of the run to show" value={scope} onChange={setScope} options={[{ value: "run", label: "Whole run" }, { value: "testing", label: "Testing only" }]} /></span>}
      </div>
    </section>
  );
}

/** Explorers are named by who they are; charters and scenarios by what they are for. */
function laneName(m: Mission): string {
  return m.kind === "charter" || m.kind === "scenario" ? m.title : who(m);
}

/** A sighting: a diamond above the step where a detector fired. */
function diamond(g: CanvasRenderingContext2D, cx: number, cy: number) {
  g.beginPath();
  g.moveTo(cx, cy - 4);
  g.lineTo(cx + 4, cy);
  g.lineTo(cx, cy + 4);
  g.lineTo(cx - 4, cy);
  g.closePath();
  g.fillStyle = palette.accent;
  g.fill();
  g.strokeStyle = palette.card;
  g.lineWidth = 1;
  g.stroke();
}

/** A finding: a checked disc when it replayed, a ring when it did not. */
function disc(g: CanvasRenderingContext2D, cx: number, cy: number, r: number, solid: boolean) {
  g.beginPath();
  g.arc(cx, cy, r, 0, Math.PI * 2);
  if (!solid) {
    g.fillStyle = palette.card;
    g.fill();
    g.strokeStyle = palette.muted;
    g.lineWidth = 1;
    g.stroke();
    return;
  }
  g.fillStyle = palette.accent;
  g.fill();
  g.beginPath();
  g.moveTo(cx - r * 0.46, cy + r * 0.04);
  g.lineTo(cx - r * 0.12, cy + r * 0.38);
  g.lineTo(cx + r * 0.5, cy - r * 0.32);
  g.lineCap = "round";
  g.lineJoin = "round";
  g.lineWidth = Math.max(1.5, r * 0.26);
  g.strokeStyle = palette["accent-ink"];
  g.stroke();
  g.lineCap = "butt";
  g.lineJoin = "miter";
}
