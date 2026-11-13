"""Browser layer against a real Chromium: detectors, traps, healing, typed checks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from catchbunny.browser.session import Session, dead_click_signal
from catchbunny.domain import Action, Check
from catchbunny.engine.orchestrator import App
from catchbunny.engine.replay import replay


@asynccontextmanager
async def open_session(app: App, url: str, device: str = "desktop") -> AsyncIterator[Session]:
    async with app.pool.context(device) as ctx:
        yield await Session.open(ctx, base_url=url, device=device, settings=app.settings, artifacts=app.artifacts)


async def click(session: Session, name: str) -> tuple[Action, object, object, object]:
    before = await session.observe()
    el = next(e for e in before.elements if e.name == name)
    action = Action(kind="click", target=el.sig())
    res = await session.act(action, live=True)
    return action, res, before, await session.observe()


async def test_detectors_fire_on_real_defects(app: App, buggy_shop: str) -> None:
    async with open_session(app, buggy_shop) as s:
        await s.goto("/products/3")
        await click(s, "Show reviews")
        await s.goto("/products/5")
        await s.observe()
        kinds = {sig.kind: sig for sig in s.drain(0)}
    assert "map" in kinds["page_error"].message  # uncaught exception inside a promise chain
    assert "bottle-v2" in kinds["broken_image"].detail["src"]
    assert kinds["a11y"].detail["rule"] == "input-missing-label"


async def test_dead_click_is_reported_but_traps_are_not(app: App, buggy_shop: str) -> None:
    async with open_session(app, buggy_shop) as s:
        await s.goto("/products/1")
        assert dead_click_signal(*await click(s, "Like")) is None, "a class toggle changes the DOM"  # type: ignore[arg-type]
        assert dead_click_signal(*await click(s, "Copy link")) is None, "a clipboard write is an effect"  # type: ignore[arg-type]
        await click(s, "Add to cart")
        dead = dead_click_signal(*await click(s, "Apply coupon"))  # type: ignore[arg-type]
        assert dead is not None and dead.kind == "dead_click"
        # Submitting the footer form empty is blocked by HTML5 validation: not a dead button.
        assert dead_click_signal(*await click(s, "Subscribe")) is None  # type: ignore[arg-type]


async def test_expected_4xx_is_not_a_defect_but_a_clicked_404_is(app: App, buggy_shop: str) -> None:
    async with open_session(app, buggy_shop) as s:
        await s.goto("/login")
        obs = await s.observe()
        user, pw = (next(e for e in obs.elements if e.name == n) for n in ("Username", "Password"))
        await s.act(Action(kind="fill", target=user.sig(), value="nobody"), live=True)
        await s.act(Action(kind="fill", target=pw.sig(), value="wrong"), live=True)
        await click(s, "Sign in")
        assert "Wrong username" in (await s.observe()).text
        await s.goto("/orders/999999")  # a URL the tester typed: 404, but nobody linked to it
        await s.observe()
        assert [x for x in s.drain(0) if x.kind == "http_error"] == []
        await s.goto("/")
        await click(s, "Shipping policy")
        assert [x.detail["status"] for x in s.drain(0) if x.kind == "http_error"] == [404]


async def test_layout_overflow_only_on_the_narrow_viewport(app: App, buggy_shop: str) -> None:
    for device, expected in (("desktop", 0), ("iphone", 1)):
        async with open_session(app, buggy_shop, device) as s:
            await s.goto("/products")
            await s.observe()
            assert len([x for x in s.drain(0) if x.kind == "layout"]) == expected, device


async def test_replay_relocates_heals_and_refuses(app: App) -> None:
    handler = "onclick=\"document.title='added'\""
    recorded_page = f'<main><h2>Blue Mug</h2><form><button id="add-1" type="button" {handler}>Add to cart</button></form></main>'
    moved = f'<main><section><h2>Blue Mug</h2><p><button id="buy-now" type="button" {handler}>Add to cart</button></p></section></main>'
    renamed = moved.replace(">Add to cart<", ">Add to bag<")
    ambiguous = renamed.replace("<section>", '<div><button type="button">Add to wishlist</button></div><section>')
    async with app.pool.context() as ctx:
        s = await Session.open(ctx, base_url="http://localhost", device="desktop", settings=app.settings)
        await s.page.set_content(recorded_page)
        recorded = next(e for e in (await s.observe()).elements if e.name == "Add to cart").sig()

        await s.page.set_content(moved)  # new id, new place: the role+name rung still identifies it exactly
        res = await s.act(Action(kind="click", target=recorded), live=False)
        assert res.ok and not res.healed and await s.page.title() == "added"

        await s.page.set_content(renamed)  # every exact rung fails: found again by similarity
        res = await s.act(Action(kind="click", target=recorded), live=False)
        assert res.ok and res.healed and await s.page.title() == "added"

        await s.page.set_content(ambiguous)  # two near-equal candidates: refuse
        res = await s.act(Action(kind="click", target=recorded), live=False)
        assert not res.ok and await s.page.title() == "", "an ambiguous match must fail rather than click a lookalike"


async def test_typed_checks_and_replay_until(app: App, buggy_shop: str) -> None:
    async with open_session(app, buggy_shop) as s:
        await s.goto("/products/3")
        obs = await s.observe()
        button = next(e for e in obs.elements if e.name == "Show reviews").sig()
        for check, want in (
            (Check(kind="url_contains", value="/products/3"), True),
            (Check(kind="text_visible", value="Desk Lamp"), True),
            (Check(kind="text_absent", value="Desk Lamp"), False),
            (Check(kind="element_visible", role="button", name="Add to cart"), True),
            (Check(kind="value_equals", role="spinbutton", name="Quantity", value="1"), True),
            (Check(kind="element_absent", role="link", name="Checkout"), True),
        ):
            assert (await s.run_check(check, obs, [])).passed is want, check.describe()
    async with open_session(app, buggy_shop) as s:
        actions = [Action(kind="navigate", value="/products/3"), Action(kind="click", target=button), Action(kind="navigate", value="/about")]
        out = await replay(s, actions, until=lambda signals: any(x.kind == "page_error" for x in signals))
        assert out.hit and out.completed == 2, "replay stops as soon as the defect shows"
        gone = await s.run_check(Check(kind="no_signal", value=out.signals[-1].signature), out.final, out.signals)  # type: ignore[arg-type]
        assert not gone.passed


async def test_pool_reuses_browsers_and_caps_contexts(app: App, fixed_shop: str) -> None:
    import asyncio

    app.pool._sem = asyncio.Semaphore(4)  # noqa: SLF001

    async def visit(i: int) -> None:
        async with open_session(app, fixed_shop) as s:
            await s.goto(f"/products/{i % 6 + 1}")

    await asyncio.gather(*(visit(i) for i in range(16)))
    stats = app.pool.stats()
    assert stats["contexts_opened"] >= 16 and stats["peak_contexts"] <= 4 and stats["launched"] == 1


async def test_controls_inside_shadow_dom_are_seen_clicked_and_judged(app: App) -> None:
    """Web-component apps keep their controls in shadow roots; they must not be invisible."""
    page_html = """<main><h1>Counter</h1><my-counter></my-counter></main>
<script>
customElements.define('my-counter', class extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = '<h2>Clicks</h2><span id="n">0</span> <button id="inc">Increment</button> <button id="noop">Refresh</button>';
    root.getElementById('inc').onclick = () => { root.getElementById('n').textContent = String(Number(root.getElementById('n').textContent) + 1); };
  }
});
</script>"""
    async with app.pool.context() as ctx:
        s = await Session.open(ctx, base_url="http://localhost", device="desktop", settings=app.settings)
        await s.page.set_content(page_html)
        obs = await s.observe()
        assert {"Increment", "Refresh"} <= {e.name for e in obs.elements} and "h2: Clicks" in obs.headings
        inc = dead_click_signal(*await click(s, "Increment"))  # type: ignore[arg-type]
        assert inc is None and "Clicks 1" in (await s.observe()).text, "a change inside a shadow root is a change"
        noop = dead_click_signal(*await click(s, "Refresh"))  # type: ignore[arg-type]
        assert noop is not None and noop.kind == "dead_click"
        recorded = next(e for e in obs.elements if e.name == "Increment").sig()
        res = await s.act(Action(kind="click", target=recorded), live=False)  # replay path: no live ref
        assert res.ok and "Clicks 2" in (await s.observe()).text
