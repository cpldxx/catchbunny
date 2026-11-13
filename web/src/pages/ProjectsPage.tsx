import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Skeleton } from "antd";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { ProjectForm } from "../components/ProjectForm";
import { Note, Stamp } from "../components/marks";
import { ago, KIND_LABEL, plural, verdictLine } from "../format";

export function ProjectsPage() {
  const [adding, setAdding] = useState(false);
  const navigate = useNavigate();
  const client = useQueryClient();
  const demo = useMutation({
    mutationFn: api.scanDemo,
    onSuccess: ({ run_id }) => {
      client.invalidateQueries({ queryKey: ["projects"] });  // the demo shop is an app now
      navigate(`/app/runs/${run_id}`);
    },
  });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!projects.isLoading) return setSlow(false);
    const id = setTimeout(() => setSlow(true), 15_000);
    return () => clearTimeout(id);
  }, [projects.isLoading]);
  const runs = useQueries({ queries: (projects.data ?? []).map((p) => ({ queryKey: ["runs", p.id], queryFn: () => api.runs(p.id) })) });

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1 className="title">Apps under test</h1>
          <p className="subtitle">Point Catchbunny at a running web app. A swarm of browser testers explores it, and every defect is replayed in a fresh browser before you hear about it.</p>
        </div>
        {!!projects.data?.length && <Button onClick={() => setAdding(true)}>Add an app</Button>}
      </div>

      {projects.isError && <Note type="error" title={<>{(projects.error as Error).message} <button className="link" onClick={() => projects.refetch()}>Try again</button></>} />}
      {projects.isLoading && <div className="sheet sheet-body"><Skeleton active paragraph={{ rows: 3 }} /></div>}
      {slow && <Note type="warning" style={{ marginTop: 12 }} title={<>This is taking longer than it should. Check that <code>catchbunny start</code> is still running.</>} />}
      {projects.data?.length === 0 && (
        <div className="sheet blank">
          <h2>Nothing to test yet</h2>
          <p>Add the address of an app that is running, locally or on a staging server. Catchbunny needs nothing installed in it.</p>
          <div className="blank-actions">
            <Button type="primary" onClick={() => setAdding(true)}>Add an app</Button>
            <Button onClick={() => demo.mutate()} loading={demo.isPending}>Scan the demo shop</Button>
          </div>
          <p className="faint">No app running yet? The demo shop is a small store with bugs planted in it. It starts beside Catchbunny, and six testers scan it while you watch.</p>
          {demo.error && <Note type="error" title={demo.error.message} style={{ maxWidth: 520, margin: "0 auto" }} />}
        </div>
      )}
      {!!projects.data?.length && (
        <div className="sheet">
          <ul className="list">
            {projects.data.map((p, i) => {
              const last = runs[i]?.data?.[0];
              return (
                <li key={p.id}>
                  <Link to={`/app/p/${p.id}/runs`} className="item app">
                    <span>
                      <span className="name">{p.name}</span>
                      <br />
                      <code className="faint">{p.environments[0]?.base_url}</code>
                    </span>
                    <span className="muted">
                      {last ? (
                        <span className="row wrap" style={{ gap: 12 }}><Stamp status={last.status} /><span>{KIND_LABEL[last.spec.kind]}, {ago(last.created_at)}. {verdictLine(last)}</span></span>
                      ) : "No runs yet"}
                    </span>
                    <span className="tag">{plural(runs[i]?.data?.length ?? 0, "run")}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <ProjectForm open={adding} onClose={() => setAdding(false)} />
    </main>
  );
}
