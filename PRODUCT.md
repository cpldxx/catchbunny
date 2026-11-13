# Catchbunny, the product

What is true about the product, kept apart from how it looks (`DESIGN.md`).

## What it is

Catchbunny sends a swarm of browser testers at a running web app. They map it, hunt for
bugs, and replay every sighting in a fresh browser. Only what replays is reported, with its
shortest reproduction, a recording, and a regression test that needs no model to run.

## The one mechanism

A finding is a claim, and a claim has to be proven before anyone reads it. Everything else
in the product serves that: the shared map (so the hunt is thorough), the replay (so the
claim is proven), the shortening (so it is readable), the scenario (so it stays fixed).

## Who uses it

- A developer with an app running on their own machine or on staging, who wants to know what is broken before a user does.
- A coding agent that has just changed that app and needs to check its own work. It reaches Catchbunny through MCP: Catchbunny is an MCP server, and Claude Code, Codex, Claude and ChatGPT connect to it.
- A team that shares one Catchbunny on its own domain: each person signs in, joins with the team's code, and gives their agents a key of their own.

They are at a desk with an editor and a terminal open, usually in a dark interface, often
late in a task. They read a finding to act on it, not to admire it.

## What it promises, and what it does not

- It reports only bugs it reproduced. A sighting that does not replay is never shown.
- With no model it finds bugs that raise an error somewhere: a failed request, an exception, a dead control, a broken image, a missing label, an overflow. It does not find a bug where nothing errors; that takes a model-driven tester.
- It runs on the user's machine, or on a server the user's team runs. Nothing is sent anywhere except to the app under test, and to a model if the user configured one.
- On your own machine there is no account: it answers only to localhost. A team server has accounts of its own, kept on that server.
- It tests in Chromium, Firefox or WebKit. WebKit is the engine Safari is built on, not the Safari application, and a page says so wherever it says Safari.
- It is not a hosted service and has no price. No page may suggest otherwise. The site offers a console only where a server is behind it.

## Voice

Plain, specific, second person. A button says what it does ("Start a run", "Keep as scenario").
An error says what happened and what to do next. Misses and limits are stated where the
claims are, not in a footnote. Sentence case.

## Facts a page may use

Every number on the site comes from a file the product wrote:

- `web/src/landing/demo-run.json`: one real deep scan of the bundled demo shop.
- `web/src/landing/bench.json`: the benchmark, bug by bug, written by `bench/run_bench.py`.
- `web/src/landing/models.json`: what a model-driven run added, written by `bench/run_model.py`.

A page may choose what to lead with. It may not say what these files do not back: the benchmark's
headline is the nine planted bugs that raise an error, all caught with no model, and it says that
is its scope. The two where nothing errors are a model-driven tester's work and are reported where
models are, and in the docs.

There are no customers, testimonials, prices or usage counts, so none are shown.
