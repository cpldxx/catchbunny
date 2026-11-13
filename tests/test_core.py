"""Engine pieces that need no browser."""

from __future__ import annotations

import asyncio
from pathlib import Path

from catchbunny.browser.locate import HEAL_MIN_SCORE, similarity
from catchbunny.browser.observe import Observation
from catchbunny.browser.session import policy_violation
from catchbunny.budget import Budget
from catchbunny.domain import Action, Check, ElementSig, Finding, Policy, Scenario, Severity, Signal, path_pattern
from catchbunny.engine import relevance
from catchbunny.engine.productmap import FUZZ_VARIANTS, VALID, ProductMap
from catchbunny.engine.replay import ddmin
from catchbunny.engine.scenarios import finding_to_scenario, fix_prompt, playwright_test, scenario_from_yaml, scenario_to_yaml
from catchbunny.events import EventBus
from catchbunny.store import Store

# ----------------------------------------------------------------------------- store


async def test_store_is_write_behind_until_flush(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    await store.open()
    for i in range(300):
        store.put("doc", f"d{i}", {"n": i}, parent="p", created=i)  # never awaits
    await store.flush()
    docs = await store.list("doc", parent="p")
    assert [d["n"] for d in docs] == list(range(300))
    assert store.batches < store.writes, "writes enqueued in a burst must be group-committed"
    store.put("doc", "d0", {"n": -1}, parent="p")
    await store.flush()
    assert (await store.get("doc", "d0")) == {"n": -1}
    await store.close()


async def test_idempotency_key_binds_to_first_run(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    await store.open()
    owners = await asyncio.gather(*(store.idempotent_run("key", f"run{i}") for i in range(8)))
    assert len(set(owners)) == 1
    await store.close()


# ----------------------------------------------------------------------------- events


async def test_stream_resumes_without_gap_or_duplicate(tmp_path: Path) -> None:
    store = Store(tmp_path / "t.db")
    await store.open()
    bus = EventBus(store)
    await bus.start()
    for i in range(5):
        bus.publish("r1", "step", {"i": i})

    async def late_publisher() -> None:
        await asyncio.sleep(0.05)
        for i in range(5, 8):
            bus.publish("r1", "step", {"i": i})
        bus.publish("r1", "run.finished", {})

    task = asyncio.create_task(late_publisher())
    seen = [e async for e in bus.stream("r1", after=2)]  # client already has seq 1 and 2
    await task
    assert [e.data.get("i") for e in seen if e.type == "step"] == [2, 3, 4, 5, 6, 7]
    assert seen[-1].type == "run.finished"
    assert [e.seq for e in seen] == sorted({e.seq for e in seen})
    # A client connecting after the run ended gets the full history and the stream closes.
    assert len([e async for e in bus.stream("r1")]) == 9
    await store.close()


async def test_slow_subscriber_drops_oldest_instead_of_blocking() -> None:
    bus = EventBus(queue_size=4)
    sub = bus.subscribe("r")
    for i in range(10):
        bus.publish("r", "step", {"i": i})  # must not block or raise
    bus.end("r")
    got = [e.data["i"] async for e in sub]
    assert sub.dropped > 0 and got[-1] == 9 and len(got) <= 4


# ----------------------------------------------------------------------------- budget


async def test_last_step_is_taken_exactly_once() -> None:
    budget = Budget(max_steps=50, max_seconds=60)

    async def tester() -> int:
        taken = 0
        while budget.take_step():
            taken += 1
            await asyncio.sleep(0)  # yield between steps, as a real tester does
        return taken

    assert sum(await asyncio.gather(*(tester() for _ in range(16)))) == 50
    assert budget.stop_reason == "step budget exhausted"


# ----------------------------------------------------------------------------- delta debugging


async def test_ddmin_reduces_to_the_minimal_reproduction_in_parallel() -> None:
    actions = [Action(kind="press", value=str(i)) for i in range(20)]
    culprits = {"3", "11", "17"}
    running = peak = 0

    async def reproduces(subset: list[Action]) -> bool:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return culprits <= {a.value for a in subset}

    minimal, tests = await ddmin(actions, reproduces, max_tests=200)
    assert [a.value for a in minimal] == ["3", "11", "17"]
    assert peak > 1, "candidates of a round must be tested concurrently"
    assert tests < 200


async def test_ddmin_respects_its_replay_budget() -> None:
    actions = [Action(kind="press", value=str(i)) for i in range(40)]
    calls = 0

    async def reproduces(subset: list[Action]) -> bool:
        nonlocal calls
        calls += 1
        return {"1", "38"} <= {a.value for a in subset}

    minimal, tests = await ddmin(actions, reproduces, max_tests=10)
    assert calls == tests <= 10
    assert {"1", "38"} <= {a.value for a in minimal}, "a budget-limited result must still reproduce"


# ----------------------------------------------------------------------------- identity


def test_signatures_merge_instances_and_separate_defects() -> None:
    a = Signal(kind="http_error", message="x", detail={"method": "GET", "url": "http://h/api/products/3/reviews", "status": 500})
    b = Signal(kind="http_error", message="y", detail={"method": "GET", "url": "http://h/api/products/41/reviews", "status": 500})
    c = Signal(kind="http_error", message="x", detail={"method": "POST", "url": "http://h/api/products/3/reviews", "status": 500})
    assert a.signature == b.signature != c.signature
    assert path_pattern("http://h/orders/1001/items/7") == "/orders/#/items/#"


def test_healing_score_prefers_the_moved_control_over_lookalikes() -> None:
    recorded = ElementSig(role="button", name="Add to cart", tag="button", attrs={"type": "submit", "id": "add-1"}, near="Blue Mug", css="#add-1")
    moved = {"role": "button", "name": "Add to cart", "tag": "button", "attrs": {"type": "submit", "id": "buy-1"}, "near": "Blue Mug", "css": "main > form > button"}
    other = {"role": "button", "name": "Add to wishlist", "tag": "button", "attrs": {"type": "button"}, "near": "Blue Mug", "css": "main > p > button"}
    assert similarity(recorded, moved) >= HEAL_MIN_SCORE > similarity(recorded, other)


def test_healing_never_crosses_to_a_differently_named_control() -> None:
    """Regression: on an empty cart, a recorded "Apply coupon" click once healed onto "Copy link"
    because role, tag, type and neighbourhood all matched. A different name is a different control."""
    from catchbunny.browser.locate import HEAL_MIN_NAME, _ratio

    recorded = ElementSig(role="button", name="Apply coupon", tag="button", attrs={"type": "button"}, near="Your cart", css="main > p > button")
    lookalike = {"role": "button", "name": "Copy link", "tag": "button", "attrs": {"type": "button"}, "near": "Your cart", "css": "footer > button"}
    assert _ratio(recorded.name, lookalike["name"]) < HEAL_MIN_NAME <= _ratio("Add to cart", "Add to bag")


# ----------------------------------------------------------------------------- policy


def test_policy_guard() -> None:
    origin = "http://app.test"
    post_button = ElementSig(role="button", name="Save", attrs={"form_method": "post"})
    search_field = ElementSig(role="searchbox", name="Search", attrs={"form_method": "get"})
    assert policy_violation(Policy.READ_ONLY, Action(kind="click", target=post_button), origin)
    assert policy_violation(Policy.READ_ONLY, Action(kind="fill", target=search_field, value="x"), origin) is None
    assert policy_violation(Policy.SAFE_WRITES, Action(kind="click", target=post_button), origin) is None
    assert policy_violation(Policy.SAFE_WRITES, Action(kind="click", target=ElementSig(role="button", name="Delete account")), origin)
    assert policy_violation(Policy.FULL, Action(kind="click", target=ElementSig(role="link", name="Docs", attrs={"href": "https://other.test/x"})), origin)
    assert policy_violation(Policy.FULL, Action(kind="navigate", value="https://other.test/"), origin)
    assert policy_violation(Policy.FULL, Action(kind="navigate", value="/cart"), origin) is None


# ----------------------------------------------------------------------------- product map


def _obs(path: str, controls: list[dict]) -> Observation:
    elements = [{"ref": f"e{i}", "tag": "a", "text": "", "near": "", "css": f"a:nth-of-type({i})", **c} for i, c in enumerate(controls, 1)]
    return Observation.from_raw({"url": f"http://app.test{path}", "title": path, "elements": elements})


def test_claims_are_exclusive_within_a_scope_and_independent_across_scopes() -> None:
    pm = ProductMap("http://app.test")
    nav = {"role": "link", "name": "Cart", "attrs": {"href": "/cart", "chrome": "1"}}
    home = _obs("/", [nav, {"role": "link", "name": "Mug", "attrs": {"href": "/p/1"}}, {"role": "button", "name": "Like", "tag": "button", "attrs": {}}])
    other = _obs("/about", [nav, {"role": "link", "name": "Team", "attrs": {"href": "/team"}}])
    pm.observe(home)
    pm.observe(other)
    taken = []
    while (c := pm.claim(home, (VALID,), "visitor@desktop")) is not None:
        taken.append(c[0].name)
    assert sorted(taken) == ["Cart", "Like", "Mug"], "each control is handed out exactly once"
    about = [c[0].name for c in iter(lambda: pm.claim(other, (VALID,), "visitor@desktop"), None)]
    assert about == ["Team"], "site chrome already exercised on another screen is not repeated"
    member = [c[0].name for c in iter(lambda: pm.claim(home, (VALID,), "member@desktop"), None)]
    assert sorted(member) == ["Cart", "Like", "Mug"], "another persona explores the same screen from scratch"
    assert pm.next_target(set(), (VALID,), "visitor@desktop") is None
    assert pm.next_target({home.state_id}, (VALID,), "member@desktop").id == other.state_id  # type: ignore[union-attr]
    cov = pm.coverage()
    assert cov["controls_exercised"] == cov["controls_found"] == 4  # Mug, Like, Team + the nav link counted once


def test_fuzz_variants_apply_only_to_controls_with_inputs() -> None:
    pm = ProductMap("http://app.test")
    obs = _obs(
        "/login",
        [
            {"role": "textbox", "name": "Email", "tag": "input", "attrs": {"form": "f1", "form_method": "post", "type": "email"}},
            {"role": "button", "name": "Sign in", "tag": "button", "attrs": {"form": "f1", "form_method": "post"}},
            {"role": "link", "name": "Help", "attrs": {"href": "/help"}},
        ],
    )
    pm.observe(obs)
    claims = [(c[0].name, c[1]) for c in iter(lambda: pm.claim(obs, FUZZ_VARIANTS, "visitor@desktop"), None)]
    assert sorted(claims) == sorted([("Help", VALID), *(("Sign in", v) for v in FUZZ_VARIANTS)])


def test_state_identity_ignores_counters_and_query_values() -> None:
    a = _obs("/search?q=mug", [{"role": "link", "name": "Cart (2)", "attrs": {"href": "/cart"}}])
    b = _obs("/search?q=lamp", [{"role": "link", "name": "Cart (7)", "attrs": {"href": "/cart"}}])
    c = _obs("/products/1", [{"role": "link", "name": "Cart (2)", "attrs": {"href": "/cart"}}])
    d = _obs("/products/2", [{"role": "link", "name": "Cart (2)", "attrs": {"href": "/cart"}}])
    assert a.state_id == b.state_id
    assert c.state_id != d.state_id and c.template_id == d.template_id


# ----------------------------------------------------------------------------- scenarios and exports


def _finding() -> Finding:
    button = ElementSig(role="button", name="Show reviews", tag="button", attrs={"id": "show-reviews"}, css="#show-reviews")
    signal = Signal(kind="page_error", message="TypeError: Cannot read properties of null (reading 'map')", url="http://app.test/products/3")
    return Finding(
        run_id="run_1", project_id="prj_1", signature=signal.signature, kind="page_error", severity=Severity.P2,
        title="Uncaught exception on /products/#", expected="No uncaught exception.", actual=signal.message, url=signal.url,
        repro=[Action(kind="navigate", value="/products/3"), Action(kind="click", target=button)],
        check=Check(kind="no_signal", value=signal.signature), evidence={"signal": signal.model_dump()},
    )  # fmt: skip


def test_finding_becomes_a_model_free_regression_scenario_and_survives_yaml() -> None:
    scenario = finding_to_scenario(_finding())
    assert scenario.start == "/products/3" and scenario.steps == ['Click button "Show reviews"']
    assert len(scenario.learned) == len(scenario.steps) and scenario.checks[0].kind == "no_signal"
    again = scenario_from_yaml(scenario_to_yaml(scenario))
    assert again.steps == scenario.steps and again.checks == scenario.checks
    assert again.learned[0].actions[0].target.attrs["id"] == "show-reviews"  # type: ignore[union-attr]


def test_exports_are_valid_and_self_contained() -> None:
    code = playwright_test(_finding(), "http://app.test")
    compile(code, "generated_test.py", "exec")
    assert 'page.locator(\'#show-reviews\').click()' in code and "assert not page_errors" in code
    prompt = fix_prompt(_finding(), "http://app.test")
    assert "1. Open /products/3" in prompt and '2. Click button "Show reviews"' in prompt


def test_relevance_selects_scenarios_the_diff_can_reach() -> None:
    scenarios = [
        Scenario(name="Customer applies a coupon in the cart", steps=["Open the cart", "Apply coupon SAVE10"]),
        Scenario(name="Visitor signs in", steps=["Open login", "Fill username and password"]),
    ]
    ranked = relevance.select(scenarios, ["src/cart/CouponForm.tsx"], "+ const discount = applyCoupon(code)\n- old line")
    assert ranked[0]["name"].startswith("Customer applies") and ranked[0]["selected"]
    assert not ranked[1]["selected"] and "coupon" in ranked[0]["matched"]


def test_a_run_that_explores_nothing_keeps_the_coverage_already_recorded() -> None:
    saved = {"origin": "http://app.test", "states": [], "edges": {}, "items": {"s1": ["a", "b"]}, "chrome": [], "done": {"s1": ["a"]}}
    reloaded = ProductMap.from_json(saved)
    assert reloaded.coverage()["controls_exercised"] == 0, "progress is not carried into the run: every control is re-tested"
    assert reloaded.to_json()["done"] == {"s1": ["a"]}, "but a scenario run, which explores nothing, must not erase what the map shows"
