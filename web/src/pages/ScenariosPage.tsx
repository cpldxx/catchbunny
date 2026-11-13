import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Drawer, Popconfirm, Table } from "antd";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, type Scenario } from "../api";
import { Note } from "../components/marks";
import { useShell } from "../components/Shell";
import { ago, plural } from "../format";

export function ScenariosPage() {
  const { projectId } = useParams();
  const { startRun } = useShell();
  const { message } = App.useApp();
  const client = useQueryClient();
  const [picked, setPicked] = useState<string[]>([]);
  const [open, setOpen] = useState<Scenario | null>(null);
  const scenarios = useQuery({ queryKey: ["scenarios", projectId], queryFn: () => api.scenarios(projectId!) });
  const yaml = useQuery({ queryKey: ["scenario-yaml", open?.id], queryFn: () => api.scenarioYaml(open!.id), enabled: !!open });
  const remove = useMutation({
    mutationFn: api.deleteScenario,
    onSuccess: () => { client.invalidateQueries({ queryKey: ["scenarios", projectId] }); setOpen(null); message.success("Scenario deleted"); },
    onError: (e: Error) => message.error(e.message),
  });
  const count = scenarios.data?.length ?? 0;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1 className="title">Scenarios</h1>
          <p className="subtitle">A scenario is a regression test written as plain steps. Once it has passed, its steps replay from the recording with no model call; a model is asked only when the page has changed too much to replay.</p>
        </div>
        <div className="row">
          {picked.length > 0 && <Button onClick={() => startRun({ kind: "scenarios", scenario_ids: picked })}>Run {plural(picked.length, "selected scenario")}</Button>}
          <Button disabled={!count} onClick={() => startRun({ kind: "scenarios", scenario_ids: [] })}>Run all scenarios</Button>
        </div>
      </div>
      {scenarios.isError && <Note type="error" style={{ marginBottom: 12 }} title={<>The scenarios could not be loaded: {(scenarios.error as Error).message}. <button className="link" onClick={() => scenarios.refetch()}>Try again</button></>} />}
      <div className="sheet">
        <Table<Scenario>
          rowKey="id"
          loading={scenarios.isLoading}
          dataSource={scenarios.data}
          pagination={false}
          rowSelection={{ selectedRowKeys: picked, onChange: (keys) => setPicked(keys as string[]) }}
          onRow={(s) => ({ onClick: () => setOpen(s), style: { cursor: "pointer" } })}
          locale={{
            emptyText: (
              <div className="blank">
                <h2>No scenarios yet</h2>
                <p>Open a confirmed finding and choose “Keep as scenario” to turn its reproduction into a regression test, or start a discovery run to have scenarios drafted from the app’s own flows.</p>
                <Button onClick={() => startRun({ kind: "discovery" })}>Start a discovery run</Button>
              </div>
            ),
          }}
          columns={[
            { title: "Scenario", render: (_, s) => <><button type="button" className="rowlink" onClick={(e) => { e.stopPropagation(); setOpen(s); }}>{s.name}</button><div className="faint">Starts at <code>{s.start}</code> as {s.persona} on {s.device}</div></> },
            { title: "Steps", width: 96, align: "right", render: (_, s) => <span className="tag dark num">{s.steps.length}</span> },
            {
              title: "Replays without a model", width: 224, responsive: ["md"],
              render: (_, s) => s.learned.length >= s.steps.length && s.steps.length ? <span>All {s.steps.length} steps</span>
                : s.learned.length ? <span className="muted">{s.learned.length} of {s.steps.length} steps</span> : <span className="faint">Not yet, it needs one passing run</span>,
            },
            { title: "Came from", width: 152, responsive: ["lg"], render: (_, s) => <span className="muted">{s.source_finding ? "A finding" : s.tags.includes("drafted") ? "Discovery" : "Written by hand"}</span> },
            { title: "Added", width: 112, align: "right", responsive: ["sm"], render: (_, s) => <span className="faint nowrap">{ago(s.created_at)}</span> },
          ]}
        />
      </div>

      <Drawer open={!!open} onClose={() => setOpen(null)} size={640} title={open?.name}
        footer={open && (
          <div className="drawer-foot split">
            <Popconfirm title="Delete this scenario?" description="Its recorded steps are deleted with it" okText="Delete scenario" okButtonProps={{ danger: true }} onConfirm={() => remove.mutate(open.id)}>
              <Button danger>Delete scenario</Button>
            </Popconfirm>
            <div className="row">
              <Button onClick={async () => { await navigator.clipboard.writeText(yaml.data ?? ""); message.success("YAML copied"); }}>Copy YAML</Button>
              <Button type="primary" onClick={() => { startRun({ kind: "scenarios", scenario_ids: [open.id] }); setOpen(null); }}>Run this scenario</Button>
            </div>
          </div>
        )}>
        {open && (
          <>
            <ol className="steps" style={{ marginBottom: 24 }}>{open.steps.map((s, i) => <li key={i}><span>{s}</span></li>)}</ol>
            <p className="muted" style={{ marginBottom: 8 }}>The same scenario as a file. Commit it and run it with <code>catchbunny run</code>.</p>
            <pre className="code">{yaml.data ?? ""}</pre>
          </>
        )}
      </Drawer>
    </main>
  );
}
