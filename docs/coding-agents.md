# Use it from a coding agent (MCP)

Catchbunny is an MCP server, so an agent that has just changed your code can test the result itself: scan the dev server, read a finding precise enough to act on, fix it, and ask whether it is gone.

## The endpoint

`catchbunny start` serves MCP over Streamable HTTP at `http://127.0.0.1:8700/mcp`, on the same port as the console. Any MCP client can use it. On your own machine it needs no key, because the server answers only requests addressed to localhost. Because the agent talks to the same server as the console, a scan it starts shows in the console as it happens.

## Claude Code and Codex

```bash
catchbunny connect
```

It finds Claude Code and Codex on this machine and points each at the endpoint; name one to connect only that (`catchbunny connect codex`). Or use the agent's own command:

```bash
claude mcp add --transport http catchbunny http://127.0.0.1:8700/mcp
```

```bash
codex mcp add catchbunny --url http://127.0.0.1:8700/mcp
```

Then start your app and ask the agent: *"scan http://localhost:3000 with Catchbunny and fix what it finds."* The console's **Connect an agent** page shows the same commands with the server's own address filled in, and on a team server makes the key.

| Agent | Name | Where it goes |
|---|---|---|
| Claude Code | `claude-code` | Added for every project through Claude Code's own `claude mcp add-json --scope user`. Without the `claude` command on `PATH`, `.mcp.json` in the current project |
| Codex (CLI, IDE extension, desktop app) | `codex` | One `[mcp_servers.catchbunny]` table in `~/.codex/config.toml` |
| Claude Desktop | `claude-desktop` | One entry under `mcpServers` in `claude_desktop_config.json`, which starts Catchbunny itself (that file takes commands, not addresses). Named only, never found automatically; quit Claude first: it rewrites that file while it runs, and `connect` refuses rather than race it |

`connect` touches one entry named `catchbunny` and nothing else in the file: other servers, comments and ordering stay as they were, the previous file is kept as `.bak`, a file that does not parse is left alone, `--print` shows the entry without writing, and `--remove` takes it out.

With `--stdio`, the agent starts a Catchbunny of its own over stdio instead, so nothing needs to be running. The command it writes is an absolute path, because an app started from the dock does not have your shell's `PATH`. That server keeps its records in `~/.catchbunny` too, so the console lists its runs. Several servers can share that directory: each run records the process that owns it, so a server starting up does not mistake another server's run for one that died.

## Claude and ChatGPT

Their connectors reach a server over the internet, from the vendor's servers, so they need an address with HTTPS. [Serve Catchbunny for a team](teams.md) and add `https://<your address>/mcp` as a custom connector (Claude: Customize > Connectors > Add > Add custom connector). The connector finds the server's OAuth metadata, registers itself, and sends you to your own server to sign in and allow it. No token is pasted anywhere.

Scans are made **from the machine Catchbunny is on**: `http://localhost:3000` in a scan is that machine's own dev server. Whoever can use the endpoint can make that machine open any address it can reach (except a cloud metadata service: see [Teams](teams.md)), so nothing but localhost is answered without a sign-in or a key, and `--host 0.0.0.0` without `--team` is refused.

## Tools

| Tool | |
|---|---|
| `scan` | Hunt for bugs at a URL (optionally only under some paths); returns verified findings, one line each, or `running` and a run id |
| `run_status` | Wait for a run and return its result |
| `list_findings`, `get_finding` | Detail for one finding: expected/actual, minimal steps, failed requests, console errors, and a ready-made task description |
| `verify_fix` | Replay the reproduction against the app as it is now |
| `keep_as_scenario` | The regression test as YAML to commit |
| `run_scenarios` | Run committed scenario files; learned steps need no model |

Results are kept small on purpose (ids and one-line summaries; detail on request), and anticipated failures come back as readable tool errors. Tools that drive a browser are annotated as acting, the rest as read-only, so a client can ask before the first kind and not the second.

**No tool holds a call open for the length of a run.** A scan takes minutes; Codex cuts a tool call off after 60 seconds by default, and a proxy in front of a remote server after about 100. So `scan` waits a bounded time (45 seconds, capped at 50 over HTTP whatever the caller asks for) and, if the run is still going, returns `running` with a run id and how far it has got; `run_status` waits for the rest in calls of the same length. The run itself is not tied to the call that started it.

## What was checked, and what was not

- A real MCP client drove the whole loop over stdio (starting the server with the exact command `connect` writes), over HTTP with a token, over the `/mcp` endpoint of `catchbunny start`, and with a personal key against a team server. The token gate, the polling path and the installer are covered by `tests/test_connect.py`; the team server by `tests/test_team.py`.
- **The OAuth sign-in was driven by the MCP SDK's own OAuth client**: metadata discovery, dynamic registration, the consent page's API, the PKCE code exchange, an authenticated tool call, and a refresh that retires the pair it replaces. Declining is covered too.
- **Codex itself ran a session against it.** The Codex binary that ships in the ChatGPT desktop app (0.148), pointed at `/mcp` with a one-off `-c mcp_servers.catchbunny.url=...`, called `scan` on the demo shop's product pages, then `get_finding`, and reported the four confirmed findings and the three-step reproduction. In `codex exec`, a tool that acts is cancelled unless approvals are routed somewhere: `--approve-for-me` does that.
- **Claude Code itself ran a session against it.** Claude Code 2.1.321, given only the endpoint (`{"type": "http", "url": ".../mcp"}`) and allowed the Catchbunny tools, called `scan` on the demo shop's product pages, `run_status`, `get_finding` and `verify_fix`, and reported the four confirmed findings, the three-step reproduction and "still broken". The server's own log shows the eight MCP requests, and its database the run with exactly the parameters asked for. It ran headless (`claude -p --bare`) with an API key, on Claude Haiku 5.5.
- **Not exercised:** the connectors inside Claude and ChatGPT, which need a server on a public address. Their side is written from their documentation as of November 2026. There is no Claude Code plugin yet: packaging one needs a published repository to install from.
