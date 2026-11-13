// The ways into a team server: setting it up, joining with the team's code, signing
// in, and answering a connector that wants to act as you. A server on your own
// machine has none of these: it has no accounts.

import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Form, Input, Skeleton } from "antd";
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Account, type Meta } from "../api";
import { Note } from "../components/marks";
import { forgetInstall } from "../install";
import { Code, SiteFoot, SiteNav, Wordmark } from "../site/Site";

/** Only a path on this site: a link that arrives with `next=https://elsewhere` must not be followed. */
function safe(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/app";
}

/** Signed in, or out: what the server says about this browser has changed, so load the destination afresh. */
function go(to: string): void {
  forgetInstall();
  window.location.assign(to);
}

function Door({ title, lead, children, foot }: { title: string; lead: ReactNode; children: ReactNode; foot?: ReactNode }) {
  return (
    <main className="door" id="content">
      <div className="door-card">
        <Link to="/" className="brand" aria-label="Catchbunny, home"><Wordmark /></Link>
        <h1 className="display">{title}</h1>
        <p className="door-lead">{lead}</p>
        {children}
        {foot && <p className="door-foot">{foot}</p>}
      </div>
    </main>
  );
}

const PASSWORD = { required: true, min: 10, message: "Choose a password of at least 10 characters" };

function AccountForm({ codeLabel, codeHelp, code, submit, onSubmit, pending, error }: { codeLabel: string; codeHelp: string; code: string; submit: string; onSubmit: (a: Account) => void; pending: boolean; error?: string }) {
  return (
    <Form layout="vertical" requiredMark={false} onFinish={onSubmit} initialValues={{ code, name: "", email: "", password: "" }}>
      <Form.Item name="code" label={codeLabel} extra={codeHelp} rules={[{ required: true, message: `Enter the ${codeLabel.toLowerCase()}` }]}>
        <Input autoComplete="off" spellCheck={false} autoCapitalize="characters" placeholder="ABCD-EFGH-JKLM" className="mono" />
      </Form.Item>
      <Form.Item name="name" label="Your name" rules={[{ required: true, message: "Enter your name" }]}><Input autoComplete="name" /></Form.Item>
      <Form.Item name="email" label="Email" rules={[{ required: true, type: "email", message: "Enter an email address, like you@company.com" }]}>
        <Input type="email" inputMode="email" autoComplete="email" spellCheck={false} autoCapitalize="none" />
      </Form.Item>
      <Form.Item name="password" label="Password" extra="At least 10 characters. It is stored only as a hash." rules={[PASSWORD]}><Input.Password autoComplete="new-password" /></Form.Item>
      {error && <Note type="error" title={error} style={{ marginBottom: 16 }} />}
      <Button type="primary" htmlType="submit" block size="large" loading={pending}>{submit}</Button>
    </Form>
  );
}

export function SetUpPage({ meta }: { meta: Meta }) {
  const [params] = useSearchParams();
  const make = useMutation({ mutationFn: api.setUp, onSuccess: () => go("/app/team") });
  if (!meta.needs_owner) {
    return <Door title="Already set up" lead="This server has an owner. Sign in, or ask the owner for the team code and join with it." foot={<><Link to="/app/sign-in">Sign in</Link> or <Link to="/app/join">join with a team code</Link></>}>{null}</Door>;
  }
  return (
    <Door title="Set up this server" lead="You become its owner: the person who hands out the team code and can remove people. The setup code is printed in the terminal where the server was started.">
      <AccountForm codeLabel="Setup code" codeHelp="It proves you are the one who started this server, and it works once." code={params.get("code") ?? ""} submit="Create the owner account"
        onSubmit={make.mutate} pending={make.isPending} error={make.error?.message} />
    </Door>
  );
}

export function JoinPage({ meta }: { meta: Meta }) {
  const [params] = useSearchParams();
  const join = useMutation({ mutationFn: api.join, onSuccess: () => go(safe(params.get("next"))) });
  if (meta.needs_owner) {
    return <Door title="Not set up yet" lead="Whoever started this server sets it up first. If that is you, the link to do it is printed in the terminal." foot={<Link to="/app/setup">Set up this server</Link>}>{null}</Door>;
  }
  return (
    <Door title="Join your team" lead="Create your account with the team code. The owner of this server has it." foot={<>Already have an account? <Link to="/app/sign-in">Sign in</Link></>}>
      <AccountForm codeLabel="Team code" codeHelp="Twelve letters and digits. Capitals or not, it is the same code." code={params.get("code") ?? ""} submit="Create my account"
        onSubmit={join.mutate} pending={join.isPending} error={join.error?.message} />
    </Door>
  );
}

export function SignInPage({ meta }: { meta: Meta }) {
  const [params] = useSearchParams();
  const next = safe(params.get("next"));
  const signIn = useMutation({ mutationFn: (v: { email: string; password: string }) => api.signIn(v.email, v.password), onSuccess: () => go(next) });
  if (meta.needs_owner) {
    return <Door title="Not set up yet" lead="This server has no owner yet. The link to set it up is printed in the terminal where it was started." foot={<Link to="/app/setup">Set up this server</Link>}>{null}</Door>;
  }
  return (
    <Door title="Sign in" lead={<>To the Catchbunny server at <code translate="no">{new URL(meta.url).host}</code>.</>} foot={<>New here? <Link to={`/app/join${next === "/app" ? "" : `?next=${encodeURIComponent(next)}`}`}>Join with a team code</Link></>}>
      <Form layout="vertical" requiredMark={false} onFinish={signIn.mutate}>
        <Form.Item name="email" label="Email" rules={[{ required: true, message: "Enter your email" }]}>
          <Input type="email" inputMode="email" autoComplete="username" spellCheck={false} autoCapitalize="none" autoFocus />
        </Form.Item>
        <Form.Item name="password" label="Password" rules={[{ required: true, message: "Enter your password" }]}><Input.Password autoComplete="current-password" /></Form.Item>
        {signIn.error && <Note type="error" title={signIn.error.message} style={{ marginBottom: 16 }} />}
        <Button type="primary" htmlType="submit" block size="large" loading={signIn.isPending}>Sign in</Button>
      </Form>
    </Door>
  );
}

/** A connector (Claude, ChatGPT, any MCP client that signs people in) is asking to act as this person. */
export function AuthorizePage({ meta }: { meta: Meta }) {
  const [params] = useSearchParams();
  const id = params.get("request") ?? "";
  const request = useQuery({ queryKey: ["connector", id], queryFn: () => api.connectorRequest(id), enabled: !!id && !!meta.user, retry: false });
  const answer = useMutation({ mutationFn: (approve: boolean) => api.answerConnector(id, approve), onSuccess: ({ redirect_to }) => window.location.assign(redirect_to) });
  if (!id || request.isError) {
    return <Door title="This request has ended" lead="It expired, or it was already answered. Go back to the app you were connecting and start again.">{null}</Door>;
  }
  if (!request.data) return <Door title="Connect to Catchbunny" lead=" "><Skeleton active paragraph={{ rows: 3 }} /></Door>;
  const who = request.data;
  return (
    <Door title={`Let ${who.client_name} use Catchbunny?`} lead={<>It is asking to act as you, <strong>{meta.user?.name}</strong>, on this server. You will be sent back to <code translate="no">{who.redirect_host}</code>.</>}>
      <ul className="door-list">
        <li>It can start scans from this server, against any address this server can reach</li>
        <li>It can read findings, replay them, and turn them into regression tests</li>
        <li>It never sees your password, and you can remove it by signing it out where you connected it</li>
      </ul>
      {answer.error && <Note type="error" title={answer.error.message} style={{ marginBottom: 16 }} />}
      <div className="door-actions">
        <Button size="large" onClick={() => answer.mutate(false)} disabled={answer.isPending}>Do not allow</Button>
        <Button size="large" type="primary" onClick={() => answer.mutate(true)} loading={answer.isPending}>Allow</Button>
      </div>
    </Door>
  );
}

/** The site without a server behind it: a static copy. There is no console here to open. */
export function NoServerPage() {
  return (
    <div className="site">
      <SiteNav />
      <main id="main" className="lost">
        <div className="frame">
          <h1 className="display">The console runs where Catchbunny runs</h1>
          <p>This is a copy of the site with no Catchbunny server behind it. Install it, start it, and the console opens on your own machine, with your own runs in it.</p>
          <div className="no-server">
            <Code label="Install and start" text={"uv sync && uv run playwright install chromium --only-shell\n(cd web && npm install && npm run build)\nuv run catchbunny serve"} />
          </div>
          <p>Then open <code translate="no">http://127.0.0.1:8700/app</code>.</p>
          <div className="actions">
            <Link to="/docs/quick-start" className="btn primary">Read the quick start</Link>
            <Link to="/docs/teams" className="btn">Run it for a team</Link>
          </div>
        </div>
      </main>
      <SiteFoot />
    </div>
  );
}
