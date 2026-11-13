import type { Finding, Severity } from "../api";
import { pathOf, plural } from "../format";
import { FINDING_STATUS, Pips, SeverityMark } from "./marks";

/** One finding in a list. Without `onClick` it is a plain row, for showing a recording. */
export function FindingRow({ f, gate, on = false, onClick }: { f: Finding; gate: Severity; on?: boolean; onClick?: () => void }) {
  const dim = f.status !== "confirmed";
  const body = (
    <>
      <SeverityMark level={f.severity} gate={dim ? "P0" : gate} />
      <span className="name">{f.title}</span>
      <span className="meta">
        {f.verification.runs > 0 && <Pips {...f.verification} />}
        {dim && <span>{FINDING_STATUS[f.status]}</span>}
        <span>{f.persona} on {f.device}</span>
        <code className="ellipsis" style={{ maxWidth: 152 }}>{pathOf(f.url)}</code>
        <span className="nowrap">{plural(f.repro.length, "step")}</span>
      </span>
    </>
  );
  return onClick
    ? <button className={`item ${on ? "on" : ""} ${dim ? "dim" : ""}`} onClick={onClick} aria-current={on}>{body}</button>
    : <div className={`item static ${dim ? "dim" : ""}`}>{body}</div>;
}
