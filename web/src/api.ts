// The HTTP API of `catchbunny start`, typed. Shapes mirror src/catchbunny/domain.py.

export type Severity = "P0" | "P1" | "P2" | "P3";
export type RunStatus = "queued" | "running" | "passed" | "failed" | "blocked" | "canceled" | "error";
export type Verdict = "passed" | "failed" | "blocked" | "inconclusive";
export type RunKind = "deep_scan" | "discovery" | "scenarios";
export type Brain = "auto" | "heuristic" | "claude" | "ollama";
export type FindingStatus = "confirmed" | "flaky" | "not_reproduced" | "unverified";
export type BrowserName = "chromium" | "firefox" | "webkit";

/** A person on a team server. A server on your own machine has none. */
export interface Me { id: string; email: string; name: string; role: "owner" | "member"; created_at: number }
/** What this server is and how it is offered. A copy of the site on a static host has no server to answer this. */
export interface Meta {
  product: "catchbunny"; version: string; mode: "local" | "team"; needs_owner: boolean; user: Me | null; team: string;
  url: string; mcp_url: string; signs_connectors_in: boolean; browsers: BrowserName[];
}
export interface Team { name: string; code: string; members: (Me & { you: boolean })[] }
/** A key for a coding agent or a CI job. `secret` is present once, in the answer that creates it. */
export interface Key { id: string; label: string; hint: string; created_at: number; last_used_at: number; secret?: string }
export interface ConnectorRequest { client_name: string; client_uri: string; redirect_host: string }
export interface Account { code: string; name: string; email: string; password: string }

export interface Persona { name: string; role: string; goals: string; has_credential: boolean }
export interface Project {
  id: string; name: string; knowledge: string; policy: string; created_at: number;
  environments: { name: string; base_url: string; login: { path: string }; reset_path: string }[];
  personas: Persona[];
}
export interface RunSpec {
  kind: RunKind; goal: string; areas: string[]; personas: string[]; devices: string[]; browser: BrowserName;
  testers: number; max_steps: number; max_minutes: number; max_usd: number;
  brain: Brain; model: string; scenario_ids: string[]; verify_runs: number;
  minimize: boolean; record: boolean; gate: Severity;
}
export interface Usage { requests: number; input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number; cache_hit_rate: number; usd: number }
export interface Budget { steps: number; max_steps: number; seconds: number; max_seconds: number; browser_seconds: number; llm: Usage; max_usd: number }
export interface Coverage { screens: number; templates: number; transitions: number; controls_found: number; controls_exercised: number; control_coverage: number; scopes: string[] }
export interface ScenarioReport {
  scenario_id: string; name: string; verdict: Verdict; reason: string;
  steps: { index: number; text: string; mode: string; passed: boolean; detail: string }[];
  checks: { check: Check; passed: boolean; detail: string }[];
  outcomes: { outcome: string; verdict: string; reason: string }[];
  signals: { kind: string; message: string }[];
  learned_steps: number; model_steps: number;
}
export interface Summary {
  verdict?: string; gate?: Severity; blocking_findings?: string[]; missions?: number; brains?: string[]; model?: string; browser?: BrowserName;
  blocked_missions?: { title: string; reason: string }[];
  findings?: { candidates: number; confirmed: number; flaky: number; not_reproduced: number; unverified: number; by_severity: Record<Severity, number>; repro_steps_before: number; repro_steps_after: number };
  coverage?: Coverage; budget?: Budget; stop_reason?: string;
  hunt_seconds?: number; triage_seconds?: number; steps_per_second?: number; verification_replays?: number;
  scenarios?: ScenarioReport[]; drafted?: number;
}
export interface Run { id: string; project_id: string; spec: RunSpec; status: RunStatus; phase: string; created_at: number; started_at: number; finished_at: number; summary: Summary; error: string }
export interface Mission {
  id: string; run_id: string; kind: string; title: string; charter: string; persona: string; device: string; start_path: string;
  max_steps: number; scenario_id: string; status: string; verdict: Verdict | null; summary: string; brain: string;
  steps_taken: number; signals: number; started_at: number; finished_at: number;
}
export interface ElementSig { role: string; name: string; tag: string; css: string }
export interface Action { kind: string; target: ElementSig | null; value: string; secret: string; note: string }
export interface Check { kind: string; value: string; role: string; name: string }
export interface Signal { kind: string; message: string; url?: string; signature: string; step?: number; detail?: Record<string, unknown> }
export interface Step {
  index: number; action: Action; ok: boolean; error: string; healed: boolean; url_before: string; url_after: string;
  duration_ms: number; screenshot: string; thought: string; signals: Signal[];
}
export interface Evidence {
  signal?: Signal; screenshot?: string; video?: string; trace?: string;
  timeline?: { index: number; action: string; t_ms: number; ms: number; ok: boolean }[];
  network?: { t_ms: number; method: string; url: string; status: number | string; type: string }[];
  console?: { t_ms: number; level: string; text: string }[];
  fired_at_ms?: number | null; video_offset_ms?: number; duration_ms?: number;
  note?: string; // why a finding could not be verified
}
export interface Finding {
  id: string; run_id: string; project_id: string; kind: string; severity: Severity; title: string; expected: string; actual: string;
  url: string; persona: string; device: string; browser: BrowserName; status: FindingStatus; verification: { runs: number; reproduced: number };
  repro: Action[]; original_steps: number; minimized: boolean; check: Check | null; occurrences: number; missions: string[];
  evidence: Evidence; created_at: number; repro_text?: string[];
}
export interface Scenario {
  id: string; project_id: string; name: string; persona: string; device: string; start: string;
  steps: string[]; outcomes: string[]; checks: Check[]; learned: { step_hash: string; learned_at: number }[];
  tags: string[]; source_finding: string; created_at: number;
}
export interface MapState {
  id: string; template: string; url: string; path: string; title: string; headings: string[];
  controls: number; visits: number; explorable: boolean; controls_total: number; controls_done: number;
}
export interface ProductMap { origin?: string; states: MapState[]; edges: Record<string, Record<string, string>> }
export interface Provider { id: Brain; available: boolean; reason: string; default_model: string; models: { name: string; parameters?: string; size_gb?: number; thinking?: boolean }[] }
export interface Models { auto: Brain; providers: Provider[] }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** Called when the server says this browser is not signed in. The console sets it to send the person to the sign-in page. */
export const session = { onSignedOut: () => {} };

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  } catch {
    throw new ApiError(0, "The Catchbunny server is not answering. Start it with `catchbunny start`.");
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      message = body.message ?? body.detail ?? message;
    } catch { /* not JSON: keep the status line */ }
    if (res.status === 401 && !path.startsWith("/v1/auth/")) session.onSignedOut();
    throw new ApiError(res.status, String(message));
  }
  if (res.status === 204) return undefined as T;
  return (res.headers.get("content-type") ?? "").includes("json") ? res.json() : (res.text() as Promise<T>);
}

const post = <T,>(path: string, body?: unknown) => http<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export interface NewProject { name: string; base_url: string; login_path: string; username: string; password: string; policy: string; knowledge: string; reset_path: string }

export const api = {
  meta: () => http<Meta>("/v1/meta"),
  setUp: (a: Account) => post<Me>("/v1/auth/setup", a),
  join: (a: Account) => post<Me>("/v1/auth/join", a),
  signIn: (email: string, password: string) => post<Me>("/v1/auth/sign-in", { email, password }),
  signOut: () => post<void>("/v1/auth/sign-out"),
  team: () => http<Team>("/v1/team"),
  renameTeam: (name: string) => http<{ name: string }>("/v1/team", { method: "PATCH", body: JSON.stringify({ name }) }),
  newTeamCode: () => post<{ code: string }>("/v1/team/code"),
  removeMember: (id: string) => http<void>(`/v1/team/members/${id}`, { method: "DELETE" }),
  keys: () => http<Key[]>("/v1/tokens"),
  createKey: (label: string) => post<Key>("/v1/tokens", { label }),
  revokeKey: (id: string) => http<void>(`/v1/tokens/${id}`, { method: "DELETE" }),
  connectorRequest: (id: string) => http<ConnectorRequest>(`/v1/oauth/requests/${id}`),
  answerConnector: (id: string, approve: boolean) => post<{ redirect_to: string }>(`/v1/oauth/requests/${id}`, { approve }),
  models: () => http<Models>("/v1/models"),
  projects: () => http<Project[]>("/v1/projects"),
  scanDemo: () => post<{ project_id: string; run_id: string; url: string; planted: number }>("/v1/demo"),
  project: (id: string) => http<Project>(`/v1/projects/${id}`),
  createProject: (p: NewProject) =>
    post<Project>("/v1/projects", {
      name: p.name, base_url: p.base_url, login: { path: p.login_path || "/login" }, policy: p.policy, knowledge: p.knowledge, reset_path: p.reset_path || "",
      personas: [{ name: "visitor" }, ...(p.username ? [{ name: "member", role: "Signed-in user", credential: { username: p.username, password: p.password } }] : [])],
    }),
  runs: (projectId: string) => http<Run[]>(`/v1/projects/${projectId}/runs`),
  run: (id: string) => http<Run>(`/v1/runs/${id}`),
  startRun: (projectId: string, spec: Partial<RunSpec>) => post<Run>(`/v1/projects/${projectId}/runs`, spec),
  cancelRun: (id: string) => post<{ canceled: boolean }>(`/v1/runs/${id}/cancel`),
  missions: (runId: string) => http<Mission[]>(`/v1/runs/${runId}/missions`),
  steps: (missionId: string) => http<Step[]>(`/v1/missions/${missionId}/steps`),
  findings: (runId: string) => http<Finding[]>(`/v1/runs/${runId}/findings`),
  finding: (id: string) => http<Finding>(`/v1/findings/${id}`),
  exportFinding: (id: string, format: "scenario" | "playwright" | "prompt") => http<string>(`/v1/findings/${id}/export?format=${format}`),
  keepAsScenario: (id: string) => post<Scenario>(`/v1/findings/${id}/scenario`),
  rerunFinding: (id: string, runs = 2) => post<{ runs: number; reproduced: number; still_present: boolean }>(`/v1/findings/${id}/rerun?runs=${runs}`),
  scenarios: (projectId: string) => http<Scenario[]>(`/v1/projects/${projectId}/scenarios`),
  scenarioYaml: (id: string) => http<string>(`/v1/scenarios/${id}?format=yaml`),
  deleteScenario: (id: string) => http<void>(`/v1/scenarios/${id}`, { method: "DELETE" }),
  map: (projectId: string) => http<ProductMap>(`/v1/projects/${projectId}/map`),
  artifact: (name: string) => `/v1/artifacts/${name}`,
};
