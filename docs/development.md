# Working on Catchbunny

A checkout, its tests, and where things are.

```bash
git clone https://github.com/cpldxx/catchbunny && cd catchbunny && uv sync
```

```bash
uv run pytest
```

`uv run catchbunny ...` runs the checkout. The site is committed built in `web/dist`, so a `git+https` install needs no Node; after changing anything under `web/src`, rebuild it with `(cd web && npm install && npm run build)` and commit `web/dist` with the change. `uv run python bench/run_bench.py` regenerates `bench/RESULTS.md` and the landing page's benchmark; `uv run python bench/run_model.py --brain claude` measures a Claude-driven run (spends up to $2); `uv run python web/scripts/readme_images.py` retakes the README's pictures from the latest finished scan on a running server.

With `ANTHROPIC_API_KEY` set, `--brain auto` adds Claude-driven testers on top of the heuristic ones. `--brain ollama --model <tag>` does the same with a model on your own machine: no key, nothing billed, nothing leaves it. Without either, everything still runs.

## Layout

```
src/catchbunny/        domain, store, events, budget, config, vault, projects, api (API, request guard, the combined server), accounts (team, users, sessions, keys), oauth (connector sign-in), cli, mcp_server (stdio + HTTP), connect (agent registration), demo_shop (the shop the console's demo scan starts; the benchmark's target)
  browser/           observe, locate, session, samples (files for upload controls)
  brain/             base, heuristic, llm (provider-neutral), claude, ollama
  engine/            productmap, tester, replay, triage, scenarios, relevance, orchestrator
bench/               buggy_shop.py (the demo shop under its old path), run_bench.py, run_model.py (a model-driven run, measured), RESULTS.md
web/dist/            the built site, committed so an install needs no Node
web/src/             api, install (is a server behind this page?), stream (SSE store), theme (tokens), antTheme, console (the /app chunk), pages/ (incl. Connect, Team and the ways in), components/ (Lanes, RouteTree, Evidence, marks, controls, ...), landing/ (page, recorded run, bench.json, models.json), docs/, site/ (masthead, footer)
web/scripts/         export_demo.py (the landing page's recorded run), readme_images.py (the README's pictures)
docs/                these pages; assets/ holds the README's pictures and diagrams.html, which two of them are drawn from
DESIGN.md PRODUCT.md  the design system, and what the product is
tests/               core (no browser), browser, reach (engines, uploads, drags, frames, shadow roots, reset; lab_app.py is its target), claude (fake client), ollama (fake server), mcp (real client, in-memory), connect (HTTP, stdio subprocess, installer), team (accounts, keys, OAuth with the SDK's client), console (the pages in a real browser), e2e
```
