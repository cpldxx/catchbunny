// What happened in the replay that confirmed a finding: the recording, and one
// reel under it with the steps, requests and console lines on the same clock.
// A checked disc stands where the defect fired; click anywhere to jump there.

import { Tabs } from "antd";
import { useRef, useState } from "react";
import { api, type Evidence as EvidenceData } from "../api";
import { ms } from "../format";
import { Catch } from "./marks";

const bad = (status: number | string) => status === "failed" || (typeof status === "number" && status >= 400);

export function Evidence({ evidence }: { evidence: EvidenceData }) {
  const video = useRef<HTMLVideoElement>(null);
  const [at, setAt] = useState(0); // ms on the replay clock
  const duration = Math.max(evidence.duration_ms ?? 0, 1);
  const lead = evidence.video_offset_ms ?? 0; // the recording starts before the replay does (sign-in)
  const fired = evidence.fired_at_ms ?? null;
  const timeline = evidence.timeline ?? [], network = evidence.network ?? [], logs = evidence.console ?? [];
  const pct = (t: number) => `${Math.min(100, Math.max(0, (t / duration) * 100))}%`;

  const seek = (t: number) => {
    setAt(t);
    if (video.current) video.current.currentTime = (t + lead) / 1000;
  };

  if (!evidence.video && !evidence.screenshot && !timeline.length) {
    return <p className="muted">No recording was kept for this finding</p>;
  }

  return (
    <div className="evidence">
      <div>
        {evidence.video ? (
          <video ref={video} src={api.artifact(evidence.video)} controls muted playsInline preload="metadata" aria-label="Recording of the replay that confirmed this finding"
            onLoadedMetadata={() => fired !== null && seek(Math.max(0, fired - 400))}
            onTimeUpdate={(e) => setAt(e.currentTarget.currentTime * 1000 - lead)} />
        ) : evidence.screenshot ? (
          <img className="shot" src={api.artifact(evidence.screenshot)} alt="The page when the defect fired" decoding="async" />
        ) : null}
        {!!timeline.length && (
          <div className="reel" role="slider" aria-label="Replay timeline" aria-valuemin={0} aria-valuemax={duration} aria-valuenow={Math.round(at)} tabIndex={0}
            onClick={(e) => { const box = e.currentTarget.getBoundingClientRect(); seek(((e.clientX - box.left) / box.width) * duration); }}
            onKeyDown={(e) => { if (e.key === "ArrowRight") seek(Math.min(duration, at + 100)); if (e.key === "ArrowLeft") seek(Math.max(0, at - 100)); }}>
            <span className="lbl" style={{ top: 4 }}>steps</span>
            <span className="rowline" style={{ top: 24 }} />
            <span className="lbl" style={{ top: 28 }}>requests</span>
            <span className="rowline" style={{ top: 48 }} />
            <span className="lbl" style={{ top: 52 }}>console</span>
            {timeline.map((s) => <i key={s.index} className={`blk ${s.ok ? "" : "bad"}`} style={{ left: pct(s.t_ms), width: pct(s.ms) }} title={`${s.index + 1}. ${s.action} (${ms(s.ms)})`} />)}
            {network.map((n, i) => <i key={i} className={`req ${bad(n.status) ? "bad" : ""}`} style={{ left: pct(n.t_ms) }} title={`${n.method} ${n.url} ${n.status}`} />)}
            {logs.map((c, i) => <i key={i} className={`con ${c.level === "error" || c.level === "exception" ? "bad" : ""}`} style={{ left: pct(c.t_ms) }} title={c.text} />)}
            {fired !== null && <span className="fired" style={{ left: pct(fired) }}><Catch size={16} /></span>}
            <span className="head" style={{ left: pct(at) }} />
          </div>
        )}
        <p className="caption">
          {fired !== null ? <>The defect fired {ms(fired)} into the replay, at the marker. </> : null}
          {evidence.trace && <a href={api.artifact(evidence.trace)} download>Download the Playwright trace</a>}
        </p>
      </div>
      <Tabs size="small" defaultActiveKey={network.some((n) => bad(n.status)) ? "network" : logs.length ? "console" : "steps"} items={[
        {
          key: "steps", label: <>Steps <span className="tag">{timeline.length}</span></>,
          children: <div className="log"><table><tbody>{timeline.map((s) => (
            <tr key={s.index} className={`click ${fired !== null && s.t_ms <= fired && fired <= s.t_ms + s.ms + 400 ? "at" : ""}`} onClick={() => seek(s.t_ms)}>
              <td className="t"><button type="button" className="rowlink" onClick={(e) => { e.stopPropagation(); seek(s.t_ms); }} aria-label={`Jump to ${ms(s.t_ms)}: ${s.action}`}>{ms(s.t_ms)}</button></td><td>{s.action}</td><td className="t">{ms(s.ms)}</td>
            </tr>))}</tbody></table></div>,
        },
        {
          key: "network", label: <>Requests <span className="tag">{network.length}</span></>,
          children: <div className="log"><table><tbody>{network.map((n, i) => (
            <tr key={i} className={`click ${bad(n.status) ? "bad" : ""}`} onClick={() => seek(n.t_ms)}>
              <td className="t"><button type="button" className="rowlink" onClick={(e) => { e.stopPropagation(); seek(n.t_ms); }} aria-label={`Jump to ${ms(n.t_ms)}: ${n.method} ${n.url}`}>{ms(n.t_ms)}</button></td><td className="s">{n.status}</td><td className="url one"><code title={`${n.method} ${n.url}`}>{n.method} {n.url}</code></td>
            </tr>))}</tbody></table></div>,
        },
        {
          key: "console", label: <>Console <span className="tag">{logs.length}</span></>,
          children: logs.length ? <div className="log"><table><tbody>{logs.map((c, i) => (
            <tr key={i} className={`click ${c.level === "error" || c.level === "exception" ? "bad" : ""}`} onClick={() => seek(c.t_ms)}>
              <td className="t"><button type="button" className="rowlink" onClick={(e) => { e.stopPropagation(); seek(c.t_ms); }} aria-label={`Jump to ${ms(c.t_ms)} in the recording`}>{ms(c.t_ms)}</button></td><td className="s">{c.level}</td><td className="url"><code>{c.text}</code></td>
            </tr>))}</tbody></table></div> : <p className="muted">The console stayed quiet</p>,
        },
      ]} />
    </div>
  );
}
