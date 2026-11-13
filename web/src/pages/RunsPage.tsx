import { useQuery } from "@tanstack/react-query";
import { Button, Table } from "antd";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Run, type Severity } from "../api";
import { Note, SeverityMark, Stamp } from "../components/marks";
import { useShell } from "../components/Shell";
import { ago, clock, count, elapsed, KIND_LABEL, LIVE, plural, verdictLine } from "../format";

export function RunsPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { startRun } = useShell();
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => api.project(projectId!) });
  const runs = useQuery({
    queryKey: ["runs", projectId],
    queryFn: () => api.runs(projectId!),
    refetchInterval: (q) => (q.state.data?.some((r) => LIVE.includes(r.status)) ? 1500 : false),
  });
  const p = project.data;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1 className="title">Runs</h1>
          {p && (
            <p className="subtitle">
              Tested as {p.personas.map((x) => x.name).join(" and ")}
              {p.personas.some((x) => x.has_credential) ? "" : ". There is no test account, so only signed-out pages are reached"}
            </p>
          )}
        </div>
      </div>
      {runs.isError && <Note type="error" style={{ marginBottom: 12 }} title={<>The runs could not be loaded: {(runs.error as Error).message}. <button className="link" onClick={() => runs.refetch()}>Try again</button></>} />}
      <div className="sheet">
        <Table<Run>
          rowKey="id"
          loading={runs.isLoading}
          dataSource={runs.data}
          pagination={runs.data && runs.data.length > 20 ? { pageSize: 20, showSizeChanger: false } : false}
          onRow={(run) => ({ onClick: () => navigate(`/app/runs/${run.id}`), style: { cursor: "pointer" } })}
          locale={{
            emptyText: (
              <div className="blank">
                <h2>No runs yet</h2>
                <p>A deep scan takes about half a minute on a small app and needs no model. Start there.</p>
                <Button onClick={() => startRun()}>Start a run</Button>
              </div>
            ),
          }}
          columns={[
            { title: "Result", width: 128, render: (_, run) => <Stamp status={run.status} /> },
            {
              title: "Run",
              render: (_, run) => (
                <>
                  <Link to={`/app/runs/${run.id}`} className="rowlink" onClick={(e) => e.stopPropagation()}>{KIND_LABEL[run.spec.kind]}</Link>
                  {run.spec.goal && <span className="muted">: {run.spec.goal}</span>}
                  <div className="faint">{verdictLine(run)}</div>
                </>
              ),
            },
            {
              title: "Confirmed findings", width: 288, responsive: ["md"],
              render: (_, run) => {
                const by = run.summary.findings?.by_severity;
                const shown = by ? (Object.keys(by) as Severity[]).filter((k) => by[k] > 0) : [];
                if (run.summary.scenarios) return <span className="muted">{run.summary.scenarios.filter((s) => s.verdict === "passed").length} of {plural(run.summary.scenarios.length, "scenario")} passed</span>;
                return shown.length ? <span className="row wrap" style={{ gap: 16 }}>{shown.map((k) => <span key={k} className="row"><SeverityMark level={k} gate={run.summary.gate} /><span className="tag">× {by![k]}</span></span>)}</span> : <span className="faint">None</span>;
              },
            },
            { title: "Driven by", width: 176, responsive: ["lg"], render: (_, run) => <span className="muted">{run.summary.model || (run.summary.brains ?? [run.spec.brain]).filter((b) => b !== "heuristic" && b !== "auto").join(", ") || "No model"}</span> },
            { title: "Steps", width: 80, align: "right", responsive: ["lg"], render: (_, run) => <span className="tag dark num">{run.summary.budget?.steps != null ? count(run.summary.budget.steps) : ""}</span> },
            { title: "Took", width: 80, align: "right", responsive: ["md"], render: (_, run) => <span className="tag dark num">{run.started_at ? clock(elapsed(run)) : ""}</span> },
            { title: "Started", width: 112, align: "right", responsive: ["sm"], render: (_, run) => <span className="faint nowrap">{ago(run.created_at)}</span> },
          ]}
        />
      </div>
    </main>
  );
}
