"""A brain with no model: systematic exploration and form fuzzing.

It costs nothing per step and is fully deterministic, which makes it the
swarm's breadth layer (and the engine's test double): it will click every
control, submit every form with valid, empty, hostile and boundary inputs,
hand every upload control a file, and drag whatever can be dragged.
It finds whatever the passive detectors can prove - crashes, 5xx, dead
controls, broken assets, a11y and layout defects. It cannot judge meaning
("the total is wrong"); that is what model-driven testers are for.
"""

from __future__ import annotations

import re

from ..browser import samples
from ..browser.observe import FIELD_ROLES, Element, Observation
from ..browser.session import policy_violation
from ..budget import Usage
from ..domain import Action, Verdict
from ..engine.productmap import DRAG, FUZZ_VARIANTS, VALID, ProductMap
from .base import Brief, Feedback, Intent

_NAV_WORDS = frozenset(
    "home cart login logout account search about contact orders checkout sign products shop view more back next menu settings".split()
)
_WORD = re.compile(r"[A-Za-z]{4,}")

_BY_KEY: list[tuple[str, str]] = [
    ("email", "qa.tester@example.com"),
    ("phone", "5550100"),
    ("zip", "94107"),
    ("postal", "94107"),
    ("card", "4242424242424242"),
    ("cvv", "123"),
    ("cvc", "123"),
    ("expir", "12/30"),
    ("quantity", "2"),
    ("qty", "2"),
    ("coupon", "SAVE10"),
    ("promo", "SAVE10"),
    ("city", "San Francisco"),
    ("address", "500 Market Street"),
    ("name", "Quinn Tester"),
    ("user", "quinn"),
    ("url", "https://example.com"),
    ("website", "https://example.com"),
]
_BY_TYPE = {"email": "qa.tester@example.com", "tel": "5550100", "url": "https://example.com", "date": "2026-01-15", "password": "Test1234!x"}
_SPECIAL = "%' \"<b>x</b> ;-- \U0001f642"


class HeuristicBrain:
    name = "heuristic"

    def __init__(self, brief: Brief, pmap: ProductMap) -> None:
        self.brief = brief
        self.pmap = pmap
        self.variants = (*FUZZ_VARIANTS, DRAG) if brief.mission.kind == "fuzz" else (VALID, DRAG)
        self.scope = f"{brief.mission.persona}@{brief.mission.device}"
        self._target: str | None = None
        self._unreachable: set[str] = set()

    def usage(self) -> Usage:
        return Usage()

    async def decide(self, obs: Observation, feedback: Feedback) -> list[Intent]:
        if self._target and obs.state_id != self._target:
            # The screen could not be re-created from its URL (it depends on session
            # state another tester built). Skip it for this tester only.
            self._unreachable.add(self._target)
        self._target = None
        while (claim := self.pmap.claim(obs, self.variants, self.scope, self.brief.areas)) is not None:
            plan = self._plan(obs, *claim)
            if plan:
                return plan
        node = self.pmap.next_target(self._unreachable | {obs.state_id}, self.variants, self.scope, self.brief.areas)
        if node is None:
            return [Intent(kind="finish", verdict=Verdict.PASSED, summary="Every reachable control was exercised.")]
        self._target = node.id
        return [Intent(kind="act", action=Action(kind="navigate", value=self.pmap.relative(node), note="Move to a screen with unexplored controls"))]

    # ------------------------------------------------------------------ planning

    def _plan(self, obs: Observation, el: Element, variant: str) -> list[Intent]:
        origin = self.pmap.origin
        policy = self.brief.policy
        if variant == DRAG:
            onto = self._drop_target(obs, el)
            if onto is None:
                return []  # nowhere to drop it: claimed and skipped
            actions = [Action(kind="drag", target=el, to=onto)]
        elif el.role == "file":
            actions = [Action(kind="upload", target=el, value=samples.pick(el.attrs.get("accept", ""), variant))]
        elif el.role in FIELD_ROLES:
            actions = [Action(kind="fill", target=el, value=self._value(el, variant, obs) or ""), Action(kind="press", value="Enter")]
        elif el.role == "combobox":
            options = [o for o in el.attrs.get("options", "").split("|") if o and o != el.value]
            if not options:
                return []
            actions = [Action(kind="select", target=el, value=options[0])]
        elif el.role in ("checkbox", "radio"):
            actions = [Action(kind="click" if el.checked else "check", target=el)]
        else:
            actions = [*self._fill_fields(obs, el, variant), Action(kind="click", target=el)]
        if any(policy_violation(policy, a, origin) for a in actions):
            return []  # claimed and skipped: never retried by anyone
        note = "" if variant == VALID else f"{variant} input"
        return [Intent(kind="act", action=a.model_copy(update={"note": note}), thought=variant) for a in actions]

    def _fill_fields(self, obs: Observation, button: Element, variant: str) -> list[Action]:
        if button.role != "button":
            return []
        form = button.attrs.get("form")
        if form:
            fields = obs.form_fields(form)
        else:  # no <form>: take the inputs sitting directly before the button
            i = obs.elements.index(button)
            fields = [e for e in obs.elements[max(0, i - 3) : i] if e.role in FIELD_ROLES and not e.attrs.get("form")]
        actions: list[Action] = []
        for f in fields:
            if f.role == "combobox":
                options = [o for o in f.attrs.get("options", "").split("|") if o]
                if len(options) > 1 and variant == VALID:
                    actions.append(Action(kind="select", target=f, value=options[1] if options[0] == f.value else options[0]))
                continue
            value = self._value(f, variant, obs)
            if value is not None and value != f.value:
                actions.append(Action(kind="fill", target=f, value=value))
        if form and variant != "empty":  # "empty" submits the form without choosing a file
            actions += [Action(kind="upload", target=f, value=samples.pick(f.attrs.get("accept", ""), variant)) for f in obs.form_uploads(form)]
        return actions

    @staticmethod
    def _drop_target(obs: Observation, el: Element) -> Element | None:
        """Where to drop `el`: a drop zone it is not already in, else another draggable beside it (a reorder)."""
        home = el.attrs.get("in_zone", "")
        zones = [e for e in obs.elements if e.attrs.get("dropzone") and e.frame == el.frame and e.css not in (home, el.css)]
        if zones:
            return zones[0]
        peers = [e for e in obs.elements if e.attrs.get("draggable") and e.frame == el.frame and e.key() != el.key()]
        return peers[-1] if peers else None

    def _value(self, f: Element, variant: str, obs: Observation) -> str | None:
        kind = f.attrs.get("type", "text").lower()
        numeric = kind == "number" or f.role == "spinbutton"
        if variant == "empty":
            return ""
        if variant == "special":
            return "-1" if numeric else ("not-an-email" if kind == "email" else _SPECIAL)
        if variant == "boundary":
            return "999999" if numeric else "A" * int(f.attrs.get("maxlength", "300") or 300)
        if numeric:
            low = f.attrs.get("min", "")
            return str(int(float(low)) + 1) if low.lstrip("-").replace(".", "", 1).isdigit() else "2"
        if kind in _BY_TYPE:
            return _BY_TYPE[kind]
        key = f"{f.name} {f.attrs.get('name', '')} {f.attrs.get('placeholder', '')}".lower()
        if f.role == "searchbox" or "search" in key or key.strip() == "q":
            return self._seed_word(obs)
        for needle, value in _BY_KEY:
            if needle in key:
                return value
        return "Test input"

    @staticmethod
    def _seed_word(obs: Observation) -> str:
        """A search term that plausibly matches something: a word the app itself shows."""
        for e in obs.elements:
            if e.role == "link":
                for word in _WORD.findall(e.name):
                    if word.lower() not in _NAV_WORDS:
                        return word.lower()
        return "test"


class ScriptedBrain:
    """Replays a fixed list of decisions. Used by tests and by `catchbunny` demos."""

    name = "scripted"

    def __init__(self, decisions: list[list[Intent]]) -> None:
        self._decisions = list(decisions)
        self.feedback: list[Feedback] = []

    def usage(self) -> Usage:
        return Usage()

    async def decide(self, obs: Observation, feedback: Feedback) -> list[Intent]:
        self.feedback.append(feedback)
        if not self._decisions:
            return [Intent(kind="finish", verdict=Verdict.INCONCLUSIVE, summary="script ended")]
        return self._decisions.pop(0)
