"""Finding an element again: precise locators first, self-healing last.

A recorded action carries an `ElementSig`. On replay the page may have changed
(a control moved, an id was renamed, the list is in a different order), so
resolution is a ladder:

1. the live `data-gx` ref, when the action comes from the current observation;
2. unambiguous locators, most specific first (test id, id, role+name, name
   attribute, href, CSS path) - a rung is accepted only if it matches exactly
   one element;
3. healing: re-snapshot the page, score every control against the signature and
   accept the best match only when it is good, clearly better than the
   runner-up, and - if the recorded control had a name - similarly named. Role,
   tag and position alone never identify a control: "Apply coupon" must not
   heal onto a "Copy link" button that happens to sit in a similar place.
   Anything less is "not found" - a wrong click is worse than none.

A control inside one of the app's own iframes is looked for in that frame, which
is found again by its name or by the path it loaded.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from playwright.async_api import Frame, Locator, Page

from ..domain import ElementSig, norm_text, origin_of, path_pattern, url_path
from .observe import MAX_FRAMES, SNAPSHOT_JS

Scope = Page | Frame  # where a control is looked for: the page, or one of its frames

ARIA_ROLES = frozenset(
    {"link", "button", "textbox", "searchbox", "spinbutton", "combobox", "checkbox", "radio", "tab", "menuitem", "switch", "slider"}
)
HEAL_MIN_SCORE = 0.72
HEAL_MIN_MARGIN = 0.06
HEAL_MIN_NAME = 0.6  # a named control is never healed onto one whose name is this unlike it


class ElementNotFound(Exception):
    pass


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def app_frames(page: Page, origin: str) -> list[tuple[str, Frame]]:
    """The page's iframes that belong to the app under test, each with the key it is found again by.

    A frame from another origin is somebody else's product (a payment form, a video, a captcha):
    a tester has no business filling it in, so it is left alone. A frame the page wrote itself
    (`about:blank`, `srcdoc`) is the app's.
    """
    out: list[tuple[str, Frame]] = []
    seen: dict[str, int] = {}
    for frame in page.frames:
        if frame is page.main_frame or frame.is_detached():
            continue
        if not (frame.url.startswith("about:") or origin_of(frame.url) == origin):
            continue
        key = f"name={frame.name}" if frame.name else f"src={path_pattern(frame.url)}"
        seen[key] = seen.get(key, 0) + 1
        out.append((key if seen[key] == 1 else f"{key}#{seen[key]}", frame))
    return out[:MAX_FRAMES]


def scope_of(page: Page, sig: ElementSig, origin: str) -> Scope:
    if not sig.frame:
        return page
    for key, frame in app_frames(page, origin):
        if key == sig.frame:
            return frame
    raise ElementNotFound(f"the frame {sig.frame} is not on this page")


def _ladder(page: Scope, sig: ElementSig) -> list[Locator]:
    a = sig.attrs
    rungs: list[Locator] = []
    if a.get("data-testid"):
        rungs.append(page.locator(f'[data-testid="{_quote(a["data-testid"])}"]'))
    if a.get("id"):
        rungs.append(page.locator(f'[id="{_quote(a["id"])}"]'))
    if sig.role in ARIA_ROLES and sig.name:
        rungs.append(page.get_by_role(sig.role, name=sig.name, exact=True))  # type: ignore[arg-type]
    if a.get("name") and sig.tag:
        rungs.append(page.locator(f'{sig.tag}[name="{_quote(a["name"])}"]'))
    if sig.tag == "a" and a.get("href"):
        rungs.append(page.locator(f'a[href="{_quote(a["href"])}"]'))
    if sig.css:
        rungs.append(page.locator(sig.css))
    return rungs


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


def similarity(sig: ElementSig, cand: dict[str, Any]) -> float:
    """Weighted similarity in [0, 1] between a recorded signature and a live control."""
    score = 2.0 * (sig.role == cand.get("role"))
    total = 2.0
    score += 4.0 * _ratio(sig.name, cand.get("name", ""))
    total += 4.0
    score += 1.0 * (sig.tag == cand.get("tag"))
    total += 1.0
    cattrs = cand.get("attrs", {})
    for key in ("id", "name", "type", "placeholder", "data-testid", "href"):
        if key in sig.attrs:
            total += 1.5
            mine, theirs = sig.attrs[key], cattrs.get(key, "")
            score += 1.5 * ((url_path(mine) == url_path(theirs)) if key == "href" else (mine == theirs))
    if sig.near:
        total += 1.5
        score += 1.5 * _ratio(sig.near, cand.get("near", ""))
    if sig.css:
        total += 1.0
        score += 1.0 * _ratio(sig.css, cand.get("css", ""))
    return score / total


async def resolve(page: Page, sig: ElementSig, *, live: bool, origin: str = "") -> tuple[Locator, bool]:
    """Return (locator, healed). Raises ElementNotFound when nothing is trustworthy."""
    page = scope_of(page, sig, origin)  # type: ignore[assignment]  # from here on, "the page" is the frame the control lives in
    if live and sig.ref:
        loc = page.locator(f'[data-gx="{sig.ref}"]')
        if await loc.count() == 1:
            return loc, False
    for loc in _ladder(page, sig):
        try:
            if await loc.count() == 1:
                return loc, False
        except Exception:
            continue  # an invalid selector on one rung must not sink the others
    raw = await page.evaluate(SNAPSHOT_JS, "h")
    plausible = [c for c in raw["elements"] if not c.get("disabled") and (not sig.name or _ratio(sig.name, c.get("name", "")) >= HEAL_MIN_NAME)]
    scored = sorted(((similarity(sig, c), c) for c in plausible), key=lambda t: t[0], reverse=True)
    if scored:
        best, cand = scored[0]
        margin = best - (scored[1][0] if len(scored) > 1 else 0.0)
        if best >= HEAL_MIN_SCORE and margin >= HEAL_MIN_MARGIN:
            return page.locator(f'[data-gx="{cand["ref"]}"]'), True
    raise ElementNotFound(f"no trustworthy match for {sig.label()}")
