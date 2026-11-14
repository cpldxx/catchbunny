# Web console

`web/` is a React + TypeScript single-page app, built with Vite and served by `catchbunny start` itself (one port, no CORS): the landing page at `/`, the documentation at `/docs`, the console at `/app`. Each is its own chunk: the landing page loads about 120 kB of script (gzipped) and the docs about 100 kB, and neither loads the console's component library; before the split every page loaded 400 kB. In development, `npm run dev` proxies the API, including the event stream.

- **Lanes.** One lane per tester, time running left to right. A block is one step, as wide as it took; a diamond above a block is a sighting, a step where a detector fired; the last lane shows each finding as a disc at the moment its fresh-browser replays finished, ticked if it replayed. Gaps are time spent waiting - on the page, or on a model. It is drawn on a canvas from the same SSE stream the API exposes, so a run in progress and a finished one are the same code path.
- **Findings.** Severity as signal-strength bars (red only at or above the run's gate), one pip per replay, expected against actual, the steps, and the recording with a reel under it: steps, requests and console lines on one clock, a marker where the defect fired, click to seek.
- **Actions.** Rerun in a fresh browser, keep as a scenario, copy the fix prompt, export a Playwright test, download the trace.
- **Scenarios and map.** Scenarios list how many of their steps replay without a model. The map is a table of routes, indented by address: screens that share a route (`/products/:id`, or two states of one cart) are one row with their coverage, and choosing a route marks what it leads to and what leads to it. An earlier version drew every screen as a box with lines between them; with site navigation on every page the lines said nothing and hid the structure.
- **Start a run.** Kind, goal, scope, devices, the browser engine, budgets, and who drives the testers - no model, Claude, or any tool-capable model Ollama has installed.
- **Connect an agent.** The MCP endpoint of this server and, for MCP in general, Claude Code, Codex, Claude and ChatGPT, what to run or enter with this server's address in it. On a team server it also makes and revokes your keys.
- **Team** (a team server only). The people, the team code and a new one on request. The ways in (set up, join, sign in, allow a connector) are pages of their own.
- **Keyboard.** `n` starts a run, `g` then `a` / `c` / `r` / `s` / `m` jumps between apps, connecting an agent, runs, scenarios and the map, `j` and `k` walk the findings, `?` lists them all. Rows that can be clicked hold a real link or button, and the chosen finding and the chosen route are in the address.

The site asks the server what it is (`/v1/meta`) before it offers a console. Served by `catchbunny start`, the pages say "Open the console" (or "Sign in" on a team server). A copy of the site on a static host has no server behind it, so there the console is not offered at all and `/app` explains how to get one.

## Landing page

The page at `/` explains the product by showing it. It follows the skeleton the products in this space use on theirs - what it does, then one figure per stage - and every figure is driven by one real deep scan of the bundled shop (333 steps, 9 findings), exported with host names removed.

- **The hero is that run, playable.** Six browser tiles show the page each tester is on and what it just did; a tile lights up when a detector fires; findings arrive in the list as their replays finish. A transport plays, pauses and scrubs through the run. The event log goes through the console's own reducer (`liveFrom` in `stream.ts`), so the page and the console cannot drift apart.
- **One figure per stage**, each with a title and a sentence:
  1. *Map* - the same route table as the console, filling in the order the screens were found, beside one real screenshot at a time; pointing at a route shows its screen and marks where it leads.
  2. *Hunt* - the console's own lane chart on the same clock as the hero, with how often each detector fired.
  3. *Verify* - the nine candidates being replayed in fresh browsers, in the order the run confirmed them.
  4. *Shorten* - the 18 steps a tester actually took, and the 10 the bug needs, switchable.
  5. *Inspect* - the recording of the replay that confirmed a finding, with a reel and a step list that follow it; clicking either seeks the video.
  6. *Hand off* - the figure leads with what it is, an MCP server, and how to connect one (MCP first, then Claude Code, Codex, Claude, ChatGPT, each with its one command); below it, four of the tools and what each returned for this run.
- **Each stage carries its own number** from that run (16 screens, 333 steps, 9 findings, 18 steps cut to 10), under its sentence.
- **Then the benchmark, as what it is with no model**: nine planted bugs that raise an error, nine caught, in large numbers (9 of 9, 0 findings on the fixed build, 10 of 10 regression tests, 4.6 times faster with eight testers) sitting on the table that proves it, row by row with what was reported and how far the reproduction shrank. It is read from `web/src/landing/bench.json`, which `bench/run_bench.py` writes and which still holds all eleven. The two planted bugs where nothing errors are a model-driven tester's work; they are counted in the next section (what a model adds, from `models.json`, which `bench/run_model.py` writes) and in the docs, not in the headline.
- **Then two ways to run it** (alone, or for a team on your own domain), and what the testers reach, as a ticked list. What it does not do is linked from there and lives in the docs.
- **Documentation at `/docs`**: quick start, how a run works, scenarios, coding agents, teams, models, a reference and the limits, written from this file.
- **Nothing is invented.** No customer logos, testimonials, pricing or usage counts, because there are none.

Motion is there to explain: screens appear as they were discovered, pips fill as replays finish, steps are struck as they are removed. Each figure plays once when it is first seen, the run pauses when it is off screen, and everything stands still for a visitor who prefers reduced motion. The assets are in `web/public/demo/` (page screenshots and the replay video) and `web/src/landing/demo-run.json`; `web/scripts/export_demo.py --run <id>` regenerates all of them from any finished run of the demo shop.

The demo shop itself (**Scan the demo shop** in the console, `src/catchbunny/demo_shop.py`) is dressed as a real small store - illustrated products, a proper cart and checkout - because it is what people see in the tiles and the recording. The dressing is presentation only: every control, label, id and handler is unchanged, nothing loads from another origin, and no decoration is interactive. One thing did leak while restyling it: a placeholder on the newsletter field gave the field an accessible name and silently repaired a seeded bug. The test suite caught it, and the placeholder is gone.

## Design

[DESIGN.md](../DESIGN.md) is the design system and [PRODUCT.md](../PRODUCT.md) is what the product is; `web/src/theme.ts` holds the values the code reads. In short:

| | |
|---|---|
| Character | Playful name, exact tool: friendly in what it says, strict in what it claims |
| World | A meadow after dark. Grounds are a green so deep it reads as night, text is moonlit, and the one bright colour is the moon's pale yellow |
| Logo and marks | The bunny is the logo and nothing else. Inside the product a diamond is a sighting (a detector fired) and a ticked disc is a catch (a defect that replayed) |
| Emphasis | On the landing page, numbers the product measured are loud: the display face at 64 to 120px, counted up when first seen, with two pen strokes under the one a section turns on |
| Type | Recursive Casual for the name and headlines, Atkinson Hyperlegible Next for everything that is read, Recursive Mono for code |
| Surfaces | The landing page persuades, the console is a tool, the docs are a document; each is designed for that and loads only its own code |

It was built by reading six public guides and applying what each asks for: Anthropic's `frontend-design` skill, ui-ux-pro-max, awesome-design-md (the shape of DESIGN.md), impeccable, open-design's craft rules, and Vercel's Web Interface Guidelines and React rules. DESIGN.md lists what was taken from each, and what was not: no tool from those repositories was installed or run, Title Case was not adopted, and long tables are not virtualised. The first design pass used the [unslop](https://github.com/heydamir/unslop) method by Damir Sokolovsky ([@heydamir](https://x.com/heydamir)), CC BY 4.0; its 4px grid and quiet chrome remain.

What the audit against those guides changed, beyond the look: rows that only a mouse could open now hold a link or a button; every list has an error state with a way to retry; images and the demo recording declare their size; numbers, money and dates go through `Intl`; the selection, the scrollbar, the caret and the focus ring are themed; a skip link and keyboard shortcuts were added; and the bundle was split by surface.

The stack follows GitHub stars, read from the API on 2026-11-11, with one deliberate skip:

| Role | Choice | Stars |
|---|---|---|
| UI library | React | 252,527 |
| Language | TypeScript | 111,780 |
| Components | Ant Design 6 | 100,163 |
| Build | Vite | 83,872 |
| Routing | React Router | 56,636 |
| Server state | TanStack Query | 50,571 |

Bootstrap (175,036) is a CSS toolkit rather than a React component library, and has no table, drawer or form model to build a dense tool on. shadcn/ui (127,934) is the highest-starred React option - and the default output of every app generator, which is the look this console set out not to have. Ant Design is next, and is built for exactly this kind of data-heavy tool. It is themed through tokens (`web/src/theme.ts`) rather than used as shipped, and its outlined icon set is the only one in the app.
