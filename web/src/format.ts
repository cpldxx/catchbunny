import type { Mission, Run, RunKind, RunStatus } from "./api";

export const KIND_LABEL: Record<RunKind, string> = { deep_scan: "Deep scan", discovery: "Discovery", scenarios: "Scenario run" };
export const STATUS_LABEL: Record<RunStatus, string> = {
  queued: "Queued", running: "Running", passed: "Passed", failed: "Failed", blocked: "Blocked", canceled: "Canceled", error: "Error",
};
export const LIVE: RunStatus[] = ["queued", "running"];

/** 83 -> "1:23", 3723 -> "1:02:03" */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

// Numbers and dates go through Intl, so a reader gets their own separators and month names.
const whole = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const tenth = new Intl.NumberFormat(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const money = new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2 });
const day = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

export const count = (n: number): string => whole.format(n);
export const tenths = (n: number): string => tenth.format(n);

/** Offset into a run with tenths: 3.24 -> "0:03.2" */
export function offset(seconds: number): string {
  const s = Math.max(0, seconds);
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`; // a clock reading, not a number: the colon and the padding are the format
}

/** A duration with its unit, never split from it across a line: "240 ms", "1.4 s". */
export function ms(value: number): string {
  return value >= 1000 ? `${value >= 10000 ? whole.format(value / 1000) : tenth.format(value / 1000)}\u00a0s` : `${whole.format(value)}\u00a0ms`;
}

export function pathOf(url: string): string {
  try {
    const u = new URL(url);
    return u.pathname + u.search;
  } catch {
    return url;
  }
}

export function ago(ts: number): string {
  const d = Date.now() / 1000 - ts;
  if (d < 45) return "just now";
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  return day.format(new Date(ts * 1000));
}

export function elapsed(run: Run, now = Date.now() / 1000): number {
  if (!run.started_at) return 0;
  return (run.finished_at || now) - run.started_at;
}

export function who(m: Pick<Mission, "persona" | "device">): string {
  return `${m.persona} on ${m.device}`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${whole.format(n)} ${n === 1 ? one : many}`;
}

export function usd(value: number): string {
  return value === 0 ? "$0" : value < 0.01 ? "<$0.01" : money.format(value);
}

/** One sentence that says what a run's status means, in the user's terms. */
export function verdictLine(run: Run): string {
  const s = run.summary;
  switch (run.status) {
    case "queued": return "Waiting for a free slot";
    case "running": return "Testers are working";
    case "canceled": return "Canceled before it finished";
    case "error": return run.error || "The run stopped on an internal error";
    case "blocked": return run.error || "Nothing could be tested";
    case "failed": {
      if (s.scenarios) return `${s.scenarios.filter((x) => x.verdict !== "passed").length} of ${plural(s.scenarios.length, "scenario")} did not pass`;
      const n = s.blocking_findings?.length ?? 0;
      return `${plural(n, "finding")} at or above the ${s.gate ?? "P1"} gate`;
    }
    case "passed": {
      if (s.scenarios) return `All ${plural(s.scenarios.length, "scenario")} passed`;
      const c = s.findings?.confirmed ?? 0;
      return c ? `${plural(c, "finding")} confirmed, none at or above the ${s.gate ?? "P1"} gate` : "No confirmed findings";
    }
  }
}
