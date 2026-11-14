# HTTP API

`catchbunny start` → `http://127.0.0.1:8700` (interactive API reference at `/v1/docs`; `/docs` is the product's own documentation).

| Method | Path | |
|---|---|---|
| GET | `/`, `/docs/...`, `/app/...` | The landing page, the documentation and the web console, when `web/dist` has been built |
| GET | `/v1/meta` | What this server is: local or team, its address, its MCP endpoint, who is signed in |
| POST | `/mcp` | The MCP endpoint (Streamable HTTP) |
| POST | `/v1/auth/setup`, `/v1/auth/join`, `/v1/auth/sign-in`, `/v1/auth/sign-out` | A team server's ways in |
| GET / PATCH / POST / DELETE | `/v1/team`, `/v1/team/code`, `/v1/team/members/{id}` | People, the team code, removing someone (owner) |
| GET / POST / DELETE | `/v1/tokens`, `/v1/tokens/{id}` | Your keys |
| GET | `/v1/models` | Which brains can run right now (Claude key present, Ollama models installed), and why not |
| POST / GET | `/v1/projects` | Create / list projects (credentials are write-only) |
| GET / PATCH | `/v1/projects/{id}` | Read / update base URL, personas, policy, product notes, the reset path |
| GET | `/v1/projects/{id}/map` | Screens, transitions, per-screen coverage |
| POST / GET | `/v1/projects/{id}/scenarios` | Upload scenario YAML / list |
| POST | `/v1/projects/{id}/relevance` | Rank scenarios against changed files and a diff |
| POST / GET | `/v1/projects/{id}/runs` | Start a run (`Idempotency-Key` supported) / list |
| GET | `/v1/runs/{id}` | Status, phase, summary |
| GET | `/v1/runs/{id}/events` | Server-Sent Events; resumes from `Last-Event-ID`; every event carries its `ts` |
| POST | `/v1/runs/{id}/cancel` | Cancel |
| GET | `/v1/runs/{id}/missions`, `/v1/missions/{id}/steps` | What each tester did |
| GET | `/v1/runs/{id}/findings`, `/v1/findings/{id}` | Ranked findings, with reproduction steps |
| GET | `/v1/findings/{id}/export?format=` | `scenario`, `playwright`, `prompt` |
| POST | `/v1/findings/{id}/scenario`, `/v1/findings/{id}/rerun` | Keep as a scenario / re-verify now |
| GET | `/v1/artifacts/{name}` | Screenshots, videos (`video/webm`), traces |

Event types: `run.queued`, `run.phase`, `login`, `mission.started`, `step`, `mission.finished`, `finding`, `scenario.started`, `scenario.step`, `scenario.finished`, `scenario.drafted`, `run.warning`, `run.finished`. Errors use one envelope: `{"status": "error", "code", "message", "retryable"}`. On a team server every `/v1` path but `/v1/meta` and `/v1/auth/*` answers `401 UNAUTHENTICATED` without a signed-in browser or a key.
