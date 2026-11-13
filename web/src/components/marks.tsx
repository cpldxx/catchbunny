import type { CSSProperties, ReactNode } from "react";
import type { FindingStatus, RunStatus, Severity, Verdict } from "../api";
import { STATUS_LABEL } from "../format";
import { palette } from "../theme";

/** The bunny is the logo, and only the logo. It never stands for a state inside the product. */
export function Bunny({ size = 16, hollow = false }: { size?: number; hollow?: boolean }) {
  const paint = hollow ? { fill: "none", stroke: palette.muted, strokeWidth: 1.75 } : { fill: palette.accent };
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" style={{ flex: "none", display: "block" }}>
      <ellipse cx="11.6" cy="9.6" rx="3.3" ry="8.6" transform="rotate(-9 11.6 9.6)" {...paint} />
      <ellipse cx="20.6" cy="9.9" rx="3.3" ry="8.6" transform="rotate(17 20.6 9.9)" {...paint} />
      <circle cx="16" cy="21.4" r="9.3" {...paint} fill={hollow ? palette.card : palette.accent} />
    </svg>
  );
}

/** A sighting: a detector fired here, and nothing has been proven yet. */
export function Sighting({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden="true" style={{ flex: "none", display: "block" }}>
      <path d="M6 0.75 11.25 6 6 11.25 0.75 6z" fill={palette.accent} />
    </svg>
  );
}

/** A catch: a defect that replayed in a fresh browser. Hollow, one that did not, or a bug that was missed. */
export function Catch({ size = 16, hollow = false, title }: { size?: number; hollow?: boolean; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true} style={{ flex: "none", display: "block" }}>
      {hollow
        ? <circle cx="8" cy="8" r="6.5" fill="none" stroke={palette.muted} strokeWidth="1.5" />
        : <><circle cx="8" cy="8" r="7.25" fill={palette.accent} /><path d="M4.6 8.3 7 10.6 11.5 5.7" fill="none" stroke={palette["accent-ink"]} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" /></>}
    </svg>
  );
}

const RANK: Record<Severity, number> = { P0: 4, P1: 3, P2: 2, P3: 1 };

/** Severity as signal strength: four bars, so it reads without colour. Red only when it blocks the run. */
export function SeverityMark({ level, gate = "P1" }: { level: Severity; gate?: Severity }) {
  const blocking = RANK[level] >= RANK[gate];
  return (
    <span className={`sev ${blocking ? "blocking" : level === "P3" ? "low" : ""}`} title={blocking ? `${level}: at or above the ${gate} gate` : level}>
      <i aria-hidden="true">{[1, 2, 3, 4].map((n) => <b key={n} className={n <= RANK[level] ? "on" : ""} />)}</i>
      {level}
    </span>
  );
}

/** A status is a small light and a word. Only the light carries colour. */
export function Stamp({ status, big = false }: { status: RunStatus | Verdict; big?: boolean }) {
  const label = status === "inconclusive" ? "Inconclusive" : STATUS_LABEL[status as RunStatus];
  return (
    <span className={`stamp ${big ? "big" : ""}`}>
      <i className={`led ${status}`} aria-hidden="true" />
      {label}
    </span>
  );
}

/** One pip per fresh-browser replay; filled when the replay reproduced the defect. */
export function Pips({ runs, reproduced }: { runs: number; reproduced: number }) {
  return (
    <span className="pips" role="img" aria-label={`reproduced in ${reproduced} of ${runs} replays`}>
      {Array.from({ length: runs }, (_, i) => <i key={i} className={i < reproduced ? "hit" : ""} />)}
    </span>
  );
}

/** Something the reader should know before going on. Neutral surface; the light says which kind. */
export function Note({ type, title, style }: { type: "warning" | "error" | "success"; title: ReactNode; style?: CSSProperties }) {
  return (
    <div className="note" role={type === "error" ? "alert" : "status"} style={style}>
      <i className={`led ${type}`} aria-hidden="true" />
      <div className="grow">{title}</div>
    </div>
  );
}

export const FINDING_STATUS: Record<FindingStatus, string> = {
  confirmed: "Confirmed", flaky: "Flaky", not_reproduced: "Not reproduced", unverified: "Not verified",
};
