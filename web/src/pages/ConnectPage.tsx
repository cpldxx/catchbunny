// Connecting a coding agent to this server. Catchbunny is an MCP server: the page
// starts with the endpoint itself, which any MCP client can use, then says how
// each agent is pointed at it. Everything shown is for this server's own address.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Input, Modal, Popconfirm, Skeleton } from "antd";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, type Key, type Meta } from "../api";
import { Copy, Seg } from "../components/controls";
import { Note } from "../components/marks";
import { ago } from "../format";

type Agent = "mcp" | "claude-code" | "codex" | "claude" | "chatgpt";
const AGENTS: { value: Agent; label: string }[] = [
  { value: "mcp", label: "MCP" },
  { value: "claude-code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
  { value: "claude", label: "Claude" },
  { value: "chatgpt", label: "ChatGPT" },
];

const TOOLS: [string, string][] = [
  ["scan", "Hunts for bugs at an address and returns the ones it reproduced"],
  ["run_status", "Waits for a run that outlived one tool call"],
  ["list_findings", "One line per finding of a run"],
  ["get_finding", "Expected against actual, the shortest steps, and a ready task"],
  ["verify_fix", "Replays a finding against the app as it is now"],
  ["keep_as_scenario", "Returns the regression test as a file to commit"],
  ["run_scenarios", "Runs committed scenario files"],
];

function Lines({ label, text }: { label: ReactNode; text: string }) {
  return (
    <div className="lines">
      <div className="lines-head"><span>{label}</span><Copy text={text} /></div>
      <pre className="code" tabIndex={0}>{text}</pre>
    </div>
  );
}

function How({ agent, meta }: { agent: Agent; meta: Meta }) {
  const team = meta.mode === "team";
  const url = meta.mcp_url;
  const remote = url.startsWith("https://");
  const key = "$CATCHBUNNY_KEY";
  switch (agent) {
    case "mcp":
      return (
        <>
          <p>Any MCP client can use this server. The transport is Streamable HTTP, and the endpoint is the address above.</p>
          <Lines label="As a server entry, in the form most clients read" text={JSON.stringify({ mcpServers: { catchbunny: { type: "http", url, ...(team ? { headers: { Authorization: `Bearer ${key}` } } : {}) } } }, null, 2)} />
          <p className="faint">{team ? "Send a key in the Authorization header, or let the client sign you in: the server answers an unauthenticated request with where to start." : "On this machine it needs no key: the server answers only requests addressed to localhost."}</p>
        </>
      );
    case "claude-code":
      return (
        <>
          <Lines label="Run this in the project you are working on" text={`claude mcp add --transport http catchbunny ${url}${team ? ` \\\n  --header "Authorization: Bearer ${key}"` : ""}`} />
          <p className="faint">{team ? <>Or leave the header out and run <code>/mcp</code> inside Claude Code: it opens this server’s sign-in page in your browser.</> : <>To let Claude Code start Catchbunny itself instead, with no server running, use <code>catchbunny connect claude-code</code>.</>}</p>
        </>
      );
    case "codex":
      return (
        <>
          <Lines label="Run this once" text={`codex mcp add catchbunny --url ${url}${team ? " --bearer-token-env-var CATCHBUNNY_KEY" : ""}`} />
          <p className="faint">{team ? <>Codex reads the key from the environment variable <code>CATCHBUNNY_KEY</code>, so it is never written into its config file.</> : <>To let Codex start Catchbunny itself instead, use <code>catchbunny connect codex</code>.</>}</p>
        </>
      );
    default: {
      const name = agent === "claude" ? "Claude" : "ChatGPT";
      if (!remote) {
        return (
          <Note type="warning" title={<>{name} reaches a connector over the internet, from its own servers, so it cannot reach <code>{new URL(url).host}</code>. Serve Catchbunny for a team on an address with HTTPS, and this page will show what to enter. <Link to="/docs/teams">How to do that</Link></>} />
        );
      }
      return (
        <>
          <ol className="how">
            {agent === "claude"
              ? <><li>In Claude, open <strong>Customize</strong>, then <strong>Connectors</strong></li><li>Choose <strong>Add</strong>, then <strong>Add custom connector</strong></li></>
              : <><li>In ChatGPT, turn on developer mode under <strong>Settings</strong>, <strong>Apps and connectors</strong></li><li>Choose <strong>Create</strong> and pick MCP server</li></>}
            <li>Enter the address above and continue</li>
            <li>{meta.signs_connectors_in ? "You are sent here to sign in and to allow it. It then acts as you." : "This server has no HTTPS, so it cannot sign a connector in"}</li>
          </ol>
          <p className="faint">Scans run from this server. An address like <code>localhost</code> in a scan is this server’s own machine, not yours.{agent === "chatgpt" && " This has been driven end to end with the MCP SDK’s own client, not yet inside ChatGPT itself."}</p>
        </>
      );
    }
  }
}

function Keys() {
  const { message } = App.useApp();
  const client = useQueryClient();
  const keys = useQuery({ queryKey: ["keys"], queryFn: api.keys });
  const [label, setLabel] = useState("");
  const [made, setMade] = useState<Key>();
  const refresh = () => client.invalidateQueries({ queryKey: ["keys"] });
  const create = useMutation({ mutationFn: api.createKey, onSuccess: (key) => { setMade(key); setLabel(""); refresh(); }, onError: (e: Error) => message.error(e.message) });
  const revoke = useMutation({ mutationFn: api.revokeKey, onSuccess: refresh, onError: (e: Error) => message.error(e.message) });
  return (
    <section className="sheet" aria-labelledby="keys-title">
      <div className="sheet-head"><h2 id="keys-title">Your keys</h2></div>
      <div className="sheet-body">
        <p className="muted" style={{ maxWidth: "72ch" }}>A key lets an agent or a CI job use this server as you. Make one per place you use it, so you can take one away without touching the others.</p>
        <form className="key-new" onSubmit={(e) => { e.preventDefault(); create.mutate(label); }}>
          <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Where it will be used: Claude Code on my laptop…" aria-label="What the key is for" maxLength={60} autoComplete="off" />
          <Button htmlType="submit" loading={create.isPending}>Create a key</Button>
        </form>
        {keys.isLoading && <Skeleton active paragraph={{ rows: 2 }} />}
        {keys.isError && <Note type="error" title={<>{(keys.error as Error).message} <button className="link" onClick={() => keys.refetch()}>Try again</button></>} />}
        {keys.data?.length === 0 && <p className="faint">No keys yet.</p>}
        {!!keys.data?.length && (
          <ul className="plain key-list">
            {keys.data.map((k) => (
              <li key={k.id}>
                <span className="ellipsis"><strong>{k.label}</strong></span>
                <code className="faint" aria-label={`Ends in ${k.hint}`}>cb_…{k.hint}</code>
                <span className="faint">Made {ago(k.created_at)}{k.last_used_at ? `, last used ${ago(k.last_used_at)}` : ", never used"}</span>
                <Popconfirm title="Revoke this key?" description="Whatever uses it stops working at once." okText="Revoke" okButtonProps={{ danger: true }} onConfirm={() => revoke.mutate(k.id)}>
                  <Button size="small">Revoke</Button>
                </Popconfirm>
              </li>
            ))}
          </ul>
        )}
      </div>
      <Modal title="Copy this key now" open={!!made} onCancel={() => setMade(undefined)} footer={<Button type="primary" onClick={() => setMade(undefined)}>I have copied it</Button>} width={560}>
        <p className="muted">This is the only time it is shown. The server keeps a hash of it, not the key.</p>
        <div className="cmd" style={{ margin: "16px 0" }}><code translate="no">{made?.secret}</code><Copy text={made?.secret ?? ""} /></div>
        <Lines label="To use it in the commands on this page" text={`export CATCHBUNNY_KEY=${made?.secret ?? ""}`} />
      </Modal>
    </section>
  );
}

export function ConnectPage({ meta }: { meta: Meta }) {
  const [agent, setAgent] = useState<Agent>("mcp");
  return (
    <main className="page connect-page">
      <div className="page-head">
        <div>
          <h1 className="title">Connect an agent</h1>
          <p className="subtitle">Catchbunny is an MCP server. Connect a coding agent to it and the agent can scan an app it just changed, read a finding, fix it, and ask whether it is gone, without you relaying anything.</p>
        </div>
      </div>
      <section className="sheet" aria-labelledby="endpoint-title">
        <div className="sheet-head"><h2 id="endpoint-title">MCP endpoint</h2><span className="tag">Streamable HTTP</span></div>
        <div className="sheet-body">
          <div className="cmd endpoint"><code translate="no">{meta.mcp_url}</code><Copy text={meta.mcp_url} /></div>
          <div className="connect-how">
            <Seg label="Connect it to" value={agent} onChange={setAgent} options={AGENTS} />
            <How agent={agent} meta={meta} />
          </div>
        </div>
      </section>
      {meta.mode === "team" && <Keys />}
      <section className="sheet" aria-labelledby="tools-title">
        <div className="sheet-head"><h2 id="tools-title">What a connected agent can call</h2><span className="tag">{TOOLS.length} tools</span></div>
        <ul className="plain tool-list">
          {TOOLS.map(([name, does]) => <li key={name}><code translate="no">{name}</code><span className="muted">{does}</span></li>)}
        </ul>
      </section>
    </main>
  );
}
