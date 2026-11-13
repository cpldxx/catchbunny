import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Modal, Skeleton } from "antd";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Finding, type Severity } from "../api";
import { pathOf, plural } from "../format";
import { Evidence } from "./Evidence";
import { FINDING_STATUS, Note, Pips, SeverityMark } from "./marks";

const KIND: Record<string, string> = {
  http_error: "Server error", page_error: "Uncaught exception", console_error: "Console error", request_failed: "Failed request", broken_image: "Broken image",
  a11y: "Accessibility", layout: "Layout", dead_click: "Dead control", agent_report: "Reported by a model tester", dialog: "Unexpected dialog",
};

export function FindingPanel({ id, gate, seed }: { id: string; gate?: Severity; seed?: Finding }) {
  const { message } = App.useApp();
  const client = useQueryClient();
  const [code, setCode] = useState<{ title: string; text: string } | null>(null);
  const q = useQuery({ queryKey: ["finding", id], queryFn: () => api.finding(id), placeholderData: seed });
  const f = q.data;
  const rerun = useMutation({ mutationFn: () => api.rerunFinding(id, 2), onError: (e: Error) => message.error(e.message) });
  const keep = useMutation({
    mutationFn: () => api.keepAsScenario(id),
    onSuccess: (s) => {
      client.invalidateQueries({ queryKey: ["scenarios", s.project_id] });
      message.success("Kept as a scenario");
    },
    onError: (e: Error) => message.error(e.message),
  });
  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(await api.exportFinding(id, "prompt"));
      message.success("Fix prompt copied");
    } catch {
      setCode({ title: "Fix prompt", text: await api.exportFinding(id, "prompt") });
    }
  };

  if (!f) return <div className="sheet sheet-body"><Skeleton active paragraph={{ rows: 8 }} /></div>;
  const steps = f.repro_text ?? [];
  const confirmed = f.status === "confirmed";

  return (
    <article className="sheet" key={id}>
      <header className="detail-head">
        <div className="meta">
          <SeverityMark level={f.severity} gate={gate} />
          <span>{KIND[f.kind] ?? f.kind}</span>
          <span>{f.persona} on {f.device}{f.browser && f.browser !== "chromium" ? `, ${f.browser === "webkit" ? "WebKit" : "Firefox"}` : ""}</span>
          <code>{pathOf(f.url)}</code>
        </div>
        <h2>{f.title}</h2>
        <div className="meta">
          {f.verification.runs > 0 ? (
            <span className="row state">
              <Pips {...f.verification} />
              <strong>{FINDING_STATUS[f.status]}</strong>
              <span className="muted">reproduced in {f.verification.reproduced} of {plural(f.verification.runs, "fresh browser")}</span>
            </span>
          ) : <strong className="state">{FINDING_STATUS[f.status]}</strong>}
          {f.occurrences > 1 && <span>hit {f.occurrences} times by {plural(f.missions.length, "tester")}</span>}
        </div>
        <div className="detail-actions">
          <Button loading={rerun.isPending} disabled={!f.check || !confirmed} onClick={() => rerun.mutate()}>Rerun in a fresh browser</Button>
          <Button loading={keep.isPending} disabled={!confirmed} onClick={() => keep.mutate()}>Keep as scenario</Button>
          <Button onClick={copyPrompt}>Copy fix prompt</Button>
          <Button onClick={async () => setCode({ title: "Playwright test", text: await api.exportFinding(id, "playwright") })}>Playwright test</Button>
        </div>
        {rerun.data && (
          <Note type={rerun.data.still_present ? "error" : "success"}
            title={rerun.data.still_present
              ? `Still broken, reproduced just now in ${rerun.data.reproduced} of ${plural(rerun.data.runs, "fresh browser")}`
              : `Not reproduced in ${plural(rerun.data.runs, "fresh browser")}. If the code changed since the run, this is fixed.`} />
        )}
        {keep.data && <Note type="success" title={<>Kept as “{keep.data.name}” <Link to={`/app/p/${keep.data.project_id}/scenarios`} style={{ marginLeft: 8 }}>Open scenarios</Link></>} />}
        {f.evidence.note && <Note type="warning" title={f.evidence.note} />}
        {f.kind === "agent_report" && confirmed && (
          <p className="aside">
            A model tester reported this. Confirmed means the page showed the same thing again in fresh browsers; whether that is a defect is the model’s judgement, so read the steps before acting on it.
          </p>
        )}
      </header>

      <section className="detail-section">
        <dl className="versus">
          <div><dt>Expected</dt><dd>{f.expected}</dd></div>
          <div><dt>Actual</dt><dd>{f.actual}</dd></div>
        </dl>
      </section>

      <section className="detail-section">
        <h3>
          Steps to reproduce
          {f.minimized && f.original_steps > f.repro.length && <span>, shortened from {f.original_steps} to {f.repro.length}</span>}
        </h3>
        {f.repro_text ? <ol className="steps">{steps.map((s, i) => <li key={i}><span>{s}</span></li>)}</ol> : <Skeleton active title={false} paragraph={{ rows: Math.min(f.repro.length, 6) }} />}
      </section>

      {confirmed && (
        <section className="detail-section">
          <h3>Recording of the replay</h3>
          <Evidence evidence={f.evidence} />
        </section>
      )}

      <Modal open={!!code} title={code?.title} onCancel={() => setCode(null)} width={864}
        footer={<Button type="primary" onClick={async () => { await navigator.clipboard.writeText(code?.text ?? ""); message.success("Copied"); }}>Copy</Button>}>
        <pre className="code">{code?.text}</pre>
      </Modal>
    </article>
  );
}
