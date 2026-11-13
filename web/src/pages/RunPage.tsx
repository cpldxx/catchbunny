import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button } from "antd";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type Severity } from "../api";
import { FindingPanel } from "../components/FindingPanel";
import { FindingRow } from "../components/FindingRow";
import { Lanes } from "../components/Lanes";
import { Note, Stamp } from "../components/marks";
import { ScenarioPanel, SummaryPanel, TesterPanel } from "../components/RunPanels";
import { useShell } from "../components/Shell";
import { clock, elapsed, KIND_LABEL, LIVE, plural, usd, verdictLine, count } from "../format";
import { useRunStream } from "../stream";

const PHASES: Record<string, string[]> = {
  deep_scan: ["preparing", "mapping", "planning", "hunting", "triage", "finished"],
  discovery: ["preparing", "exploring", "triage", "drafting scenarios", "finished"],
  scenarios: ["preparing", "running scenarios", "finished"],
};
const PHASE_NAME: Record<string, string> = {
  preparing: "Preparing", exploring: "Exploring", mapping: "Mapping the app", planning: "Planning", hunting: "Hunting", triage: "Verifying in fresh browsers",
  "drafting scenarios": "Drafting scenarios", "running scenarios": "Running scenarios", finished: "Finished",
};
const ORDER: Record<string, number> = { confirmed: 0, flaky: 1, unverified: 2, not_reproduced: 3 };
const SEV: Record<Severity, number> = { P0: 0, P1: 1, P2: 2, P3: 3 };

export function RunPage() {
  const { runId } = useParams();
  const [params, setParams] = useSearchParams();
  const { message } = App.useApp();
  const { startRun } = useShell();
  const client = useQueryClient();
  const live = useRunStream(runId);
  const fetched = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId!) });
  const run = live.run && (live.done || !fetched.data || LIVE.includes(fetched.data.status)) ? { ...live.run, ...(live.done ? {} : fetched.data ? { status: fetched.data.status, started_at: fetched.data.started_at } : {}) } : fetched.data;
  const project = useQuery({ queryKey: ["project", run?.project_id], queryFn: () => api.project(run!.project_id), enabled: !!run });
  const [now, setNow] = useState(() => Date.now() / 1000);
  const running = !!run && LIVE.includes(run.status) && !live.done;

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 500);
    const poll = setInterval(() => client.invalidateQueries({ queryKey: ["run", runId] }), 2000);
    return () => { clearInterval(id); clearInterval(poll); };
  }, [running, runId, client]);
  useEffect(() => {
    if (live.done) client.invalidateQueries({ queryKey: ["runs"] });
  }, [live.done, client]);

  const cancel = useMutation({ mutationFn: () => api.cancelRun(runId!), onError: (e: Error) => message.error(e.message) });

  const findings = useMemo(
    () => [...live.findings.values()].sort((a, b) => ORDER[a.status] - ORDER[b.status] || SEV[a.severity] - SEV[b.severity] || a.created_at - b.created_at),
    [live.version],
  );
  const scenarios = useMemo(() => [...live.scenarios.values()], [live.version]);
  const selectedFinding = params.get("finding") ?? undefined;
  const selectedTester = params.get("tester") ?? undefined;
  const selectedScenario = params.get("scenario") ?? undefined;
  const select = (key: "finding" | "tester" | "scenario", id?: string) => setParams(id ? { [key]: id } : {}, { replace: true });

  // j and k walk the findings; Esc goes back to the summary.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || document.querySelector(".ant-drawer-open")) return;
      if (e.key === "Escape" && (selectedFinding || selectedTester || selectedScenario)) select("finding");
      else if ((e.key === "j" || e.key === "k") && findings.length) {
        const at = findings.findIndex((f) => f.id === selectedFinding);
        const next = e.key === "j" ? Math.min(findings.length - 1, at + 1) : Math.max(0, at - 1);
        select("finding", findings[next].id);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (fetched.isError) return <main className="page"><Note type="error" title={<>This run could not be loaded: {(fetched.error as Error).message}. <button className="link" onClick={() => fetched.refetch()}>Try again</button></>} /></main>;
  if (!run) return <main className="page"><div className="sheet sheet-body" role="status">Loading the run…</div></main>;

  const isScenarioRun = run.spec.kind === "scenarios";
  const phase = live.done ? "finished" : live.phases.at(-1)?.phase ?? run.phase;
  const order = PHASES[run.spec.kind].filter((p) => p === phase || live.phases.some((x) => x.phase === p) || !["mapping", "planning"].includes(p));
  const reached = order.indexOf(phase);
  const budget = (live.done ? run.summary.budget : live.budget) ?? run.summary.budget;
  const steps = [...live.ticks.values()].reduce((n, t) => n + t.length, 0);
  const confirmed = findings.filter((f) => f.status === "confirmed").length;
  const gate = run.summary.gate ?? run.spec.gate;
  const t0 = run.started_at || live.phases[0]?.ts || now;
  const mission = selectedTester ? live.missions.get(selectedTester) : undefined;
  const scenario = selectedScenario ? live.scenarios.get(selectedScenario) : undefined;

  return (
    <main className="page">
      <div className="run-head">
        <div>
          <h1 className="title">{KIND_LABEL[run.spec.kind]} <span className="rest">{run.spec.goal || run.id.slice(4, 10)}</span></h1>
          <div className="verdict">
            <Stamp status={run.status} big />
            <p>{verdictLine(run)}</p>
            {running ? <Button danger loading={cancel.isPending} onClick={() => cancel.mutate()}>Cancel run</Button> : <Button onClick={() => startRun(run.spec)}>Run again</Button>}
          </div>
        </div>
      </div>

      <dl className="readings">
        <div className="reading"><dt>Elapsed</dt><dd>{clock(elapsed(run, now))}<small>{run.spec.max_minutes} min allowed</small></dd></div>
        {isScenarioRun
          ? <div className="reading"><dt>Steps replayed</dt><dd>{count(steps)}<small>without a model</small></dd></div>
          : <div className="reading"><dt>Steps</dt><dd>{count(Math.max(budget?.steps ?? 0, steps))}<small>of {count(run.spec.max_steps)}</small></dd></div>}
        {isScenarioRun
          ? <div className="reading"><dt>Scenarios</dt><dd>{scenarios.filter((s) => s.report?.verdict === "passed").length}<small>of {run.spec.scenario_ids.length} passed</small></dd></div>
          : <div className="reading"><dt>Confirmed findings</dt><dd>{confirmed}<small>{findings.length > confirmed ? `of ${findings.length} checked` : "replayed in fresh browsers"}</small></dd></div>}
        {run.summary.coverage && <div className="reading"><dt>Screens</dt><dd>{run.summary.coverage.screens}<small>{Math.round(run.summary.coverage.control_coverage * 100)}% of controls exercised</small></dd></div>}
        {!!budget?.llm.requests && <div className="reading"><dt>Model</dt><dd>{usd(budget.llm.usd)}<small>{plural(budget.llm.requests, "request")}</small></dd></div>}
      </dl>

      <ol className="phases" aria-label="Progress">
        {order.map((p, i) => <li key={p} className={i < reached ? "done" : i === reached ? "now" : ""} aria-current={i === reached ? "step" : undefined}><span>{PHASE_NAME[p]}</span></li>)}
      </ol>

      {live.lost && !live.done && <Note type="warning" style={{ marginBottom: 12 }} title="Lost the live connection to the server. Reconnecting; nothing is lost and the run continues." />}

      <Lanes live={live} run={run} selectedMission={selectedTester ?? selectedScenario} selectedFinding={selectedFinding}
        onMission={(id) => (live.scenarios.has(id) ? select("scenario", id === selectedScenario ? undefined : id) : select("tester", id === selectedTester ? undefined : id))} onFinding={(id) => select("finding", id)} />

      <div className="run-body">
        <section className="sheet" aria-label={isScenarioRun ? "Scenarios" : "Findings"}>
          <div className="sheet-head">
            <h2>{isScenarioRun ? "Scenarios" : "Findings"} <span className="tag" style={{ marginLeft: 8 }}>{isScenarioRun ? scenarios.length : findings.length}</span></h2>
            {(selectedFinding || selectedTester || selectedScenario) && <button className="link" onClick={() => select("finding")}>Run summary</button>}
          </div>
          {isScenarioRun ? (
            <ul className="list">
              {scenarios.map((s) => (
                <li key={s.id}>
                  <button className={`item ${s.id === selectedScenario ? "on" : ""}`} style={{ gridTemplateColumns: "minmax(0, 1fr)" }} onClick={() => select("scenario", s.id)}>
                    <span className="name">{s.name}</span>
                    <span className="meta" style={{ gridColumn: 1 }}>{s.report ? <Stamp status={s.report.verdict} /> : <Stamp status="running" />}{s.report?.reason && <span className="ellipsis">{s.report.reason}</span>}</span>
                  </button>
                </li>
              ))}
              {!scenarios.length && <li className="blank" style={{ padding: "32px 20px" }}><p style={{ margin: 0 }}>{running ? "Scenarios appear here as they start" : "No scenario ran"}</p></li>}
            </ul>
          ) : (
            <ul className="list">
              {findings.map((f) => <li key={f.id}><FindingRow f={f} gate={gate} on={f.id === selectedFinding} onClick={() => select("finding", f.id)} /></li>)}
              {!findings.length && (
                <li className="blank" style={{ padding: "32px 20px" }}>
                  <p style={{ margin: 0 }}>
                    {running ? "Nothing reported yet. A finding appears here only after it has been replayed in a fresh browser."
                      : run.status === "blocked" || run.status === "error" ? "Nothing was tested, so there is nothing to report"
                      : live.drafted.length ? `${plural(live.drafted.length, "scenario")} drafted from what the testers found` : "No defects found"}
                  </p>
                  {!!live.drafted.length && project.data && <Link to={`/app/p/${project.data.id}/scenarios`}>Open scenarios</Link>}
                </li>
              )}
            </ul>
          )}
        </section>

        {selectedFinding && live.findings.has(selectedFinding) ? <FindingPanel id={selectedFinding} gate={gate} seed={live.findings.get(selectedFinding)} />
          : mission ? <TesterPanel mission={mission} live={live} t0={t0} />
          : scenario ? <ScenarioPanel scenario={scenario} />
          : <SummaryPanel run={run} live={live} onMission={(id) => select("tester", id)} />}
      </div>
    </main>
  );
}
