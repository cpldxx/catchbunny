# Model providers

A model-driven tester is the same loop whichever model answers. `brain/llm.py` owns the tool vocabulary, the prompts and the transcript; a `Provider` turns one turn of it into one request. What differs between providers is the cost model, and therefore the context strategy:

| | Claude (`brain/claude.py`) | Ollama (`brain/ollama.py`) |
|---|---|---|
| What is scarce | Money per token | The context window |
| The transcript is sent | Verbatim, append-only: an unchanged prefix is a cache hit, and thinking blocks must come back untouched | As a view in which only the newest page outline is whole; older ones are cut to URL and title |
| Shared prefix | Frozen tools + system prompt behind a cache breakpoint; one `max_tokens=0` request writes it before the swarm fans out | The runner's KV cache; a masked outline is a prefix of the whole one, so each turn re-evaluates one page, not the conversation |
| Warm-up | Writes the cache entry | Loads the model and evaluates the prefix once |
| Structured output (planner, judge) | `output_config.format` | `format` with the same JSON schema |
| Tool calls | `strict: true` | Not enforced by the runner, so the tester loop corrects malformed or missing calls in the transcript and asks again |
| Things that must not vary | Tools, system prompt, effort | `num_ctx`: a different value reloads the model (31 s for an 8B model here) |
| Cost accounting | Priced per request, capped by `max_usd` | `$0`; cached tokens are the reused KV-cache prefix |

Claude defaults: `claude-opus-5-5` (`CATCHBUNNY_MODEL`), effort `low` for tester turns and `medium` for planning, server-side refusal fallbacks on (`CATCHBUNNY_LLM_FALLBACKS=0` disables). Ollama: `--model` or `CATCHBUNNY_OLLAMA_MODEL`, `CATCHBUNNY_OLLAMA_NUM_CTX` (16384), `CATCHBUNNY_OLLAMA_THINK` (off), `OLLAMA_HOST` honoured. A missing server, a model that is not installed, or one that cannot call tools blocks the run before a browser opens, with the fix in the message. `GET /v1/models` lists what the server can run right now.

## What Claude found

`bench/run_model.py --brain claude` runs one deep scan of the benchmark shop with Claude driving the goal-directed testers (6 testers, 600 steps, a $2 cap) and records the result in `web/src/landing/models.json`, which the landing page reads.

| | Claude Opus 5.5, one run |
|---|---|
| Semantic bugs confirmed by replay | **2 of 2** (negative quantity accepted, cart counter does not drop) |
| Bugs that raise an error, in the same run | 8 of 9 |
| Reports kept out as not verified | 2 (a preferences page stuck on "Loading", and a checkout page that lists no items: neither could be shown by replay) |
| Steps | 354 |
| Model requests | 96 |
| Cost | $0.75 |

One run is one sample. The no-model benchmark is repeatable to the bug; a model's run is not, and this one has not been repeated yet.

Ollama's Anthropic-compatible endpoint would have been less code. Its documented request has no context size, keep-alive or JSON schema, and its usage has no cache counter, so the provider speaks the native API instead.

## What a small local model taught the engine

Live runs on an M1 Max (64 GB) against the benchmark shop, 2 testers, goal "check that cart quantities, prices and totals are always sensible":

| Run | Model | Requests | Prompt tokens reused from the KV cache | What the model's testers reported |
|---|---|---|---|---|
| 1 | `ministral-3:8b` | 61 | 58% | 3 findings marked confirmed - all three false |
| 2 | `ministral-3:8b`, after rules 1-2 | 37 | 59% | Nothing false, and nothing semantic found |
| 3 | `qwen3.6:35b` | 91 | 67% | The seeded stale-counter bug, found and replayed; one false confirmation; a reproduction shortened until it no longer showed the bug |
| 4 | `qwen3.6:35b`, after rules 1-3 | 70 | 63% | The stale-counter bug again, now with a reproduction that shows it (add, remove, add: the header says 2, the cart holds 1); two confirmations of a check with an empty operand |

Every run cost $0, and the detectors' findings were correct in all of them. What went wrong was always the same thing: the engine trusted a model's check more than it had earned.

- **Run 1.** The 8B model wrote checks that could never pass: `value_equals` on a control that did not exist, `text_visible` on a string no page would show. Each failed on the live page, failed again on replay, and was minimised to a bare page load. Three P1 findings, all confirmed, all wrong.
- **Runs 3 and 4.** The 35B model noticed that the header still said "Cart (2)" after the cart had been emptied - one of the two seeded bugs no detector can see - and proved it with `text_absent("Cart (2)")`. Minimisation then deleted the steps that emptied the cart, because the check kept failing without them: a cart with two items in it also shows "Cart (2)". The finding was true and its reproduction was not. The same run confirmed a `value_equals` that named no control, which had matched the first control on the page; the next confirmed `text_absent("")`, which "fails" on every page ever rendered.

Four rules came out of this, each with a test:

1. A check must say what it is about and be about something on the page. One with an empty operand, or naming a control that is not there, is rejected with the reason, so the model can try again in the same mission.
2. A check that fails only because something is missing is kept as an unverified claim. It is never replayed into "confirmed".
3. A model's report is replayed along the tester's whole path and is not minimised. Its check is evidence that the page is wrong at the end of *that* path; it is not a full specification of the defect to shorten against.
4. Detector findings are untouched: a detector's signature is a complete oracle, so any path that fires it is the same defect.

Run 4's empty-operand rejection was added after it and is covered by a test, not by a fifth live run.

What the rules cannot do is judge meaning. For a model's report, "confirmed" means the page showed the same thing again in fresh browsers; that it is a defect is still the model's judgement (run 4 also reported, truthfully, that the cart has no quantity editor - and called it P1). The console says so on every such finding.

In practice: an 8B model adds a plan and coverage, and did not prove a semantic bug here. A 35B model did, in both of its runs, at about one model turn every seven seconds across two testers. Claude-class models were the design target and are still the better tester; the local path is for when nothing may leave the machine or nothing may be spent.
