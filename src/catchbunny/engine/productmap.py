"""The product map: the swarm's shared blackboard.

Every tester writes what it sees (screen states, the controls on them, which
action led where) and reads what is still unexplored. That one structure is
what turns N independent crawlers into a swarm: `claim()` hands each unexplored
control to exactly one tester, so parallel testers fan out instead of repeating
each other. Claiming is a plain synchronous method - atomic on the event loop,
no lock - and is the only coordination primitive the swarm needs.

The map outlives a run (it is persisted per project), so later runs start from
what earlier ones learned instead of rediscovering the app.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field

from ..browser.observe import CLICK_ROLES, FIELD_ROLES, Element, Observation
from ..domain import Action, origin_of, path_pattern, url_path

VALID = "valid"
FUZZ_VARIANTS = (VALID, "empty", "special", "boundary")
DRAG = "drag"  # a draggable control's own frontier item, beside whatever clicking it does
UPLOAD_VARIANTS = (VALID, "special", "boundary")  # "empty" for a file is not choosing one, which is what the form's own empty variant tries


class StateNode(BaseModel):
    id: str
    template: str
    url: str
    path: str
    title: str = ""
    headings: list[str] = Field(default_factory=list)
    controls: int = 0
    visits: int = 0
    explorable: bool = True  # False once its template has enough explored instances


class ProductMap:
    def __init__(self, origin: str, max_per_template: int = 8) -> None:
        self.origin = origin
        self.max_per_template = max_per_template
        self.states: dict[str, StateNode] = {}
        self.edges: dict[str, dict[str, str]] = {}  # state -> {action key -> state}
        self._items: dict[str, set[str]] = {}  # state -> every explorable item key seen there
        # (scope, state) -> item keys already claimed. A scope is a persona on a device:
        # a signed-in member and an anonymous visitor are looking at different apps.
        self._done: dict[tuple[str, str], set[str]] = {}
        self._templates: dict[str, int] = {}
        self._targeted: dict[str, int] = {}
        self._chrome: set[str] = set()  # item keys that live in nav/header/footer
        self._chrome_done: dict[str, set[str]] = {}  # scope -> chrome items already exercised somewhere
        self._seen_paths: set[str] = set()
        self._last_exercised: dict[str, list[str]] = {}  # what the previous run exercised: shown, never trusted

    # ------------------------------------------------------------------ writing

    def observe(self, obs: Observation) -> StateNode:
        node = self.states.get(obs.state_id)
        if node is None:
            count = self._templates.get(obs.template_id, 0)
            self._templates[obs.template_id] = count + 1
            node = StateNode(
                id=obs.state_id,
                template=obs.template_id,
                url=obs.url,
                path=url_path(obs.url),
                title=obs.title,
                headings=obs.headings[:4],
                controls=len(obs.elements),
                explorable=count < self.max_per_template and origin_of(obs.url) == self.origin,
            )
            self.states[obs.state_id] = node
            self._items[obs.state_id] = set()
        node.visits += 1
        self._seen_paths.add(path_pattern(obs.url))
        if node.explorable:
            for e, vs in self._eligible(obs):
                keys = [f"{e.key()}@{v}" for v in vs]
                self._items[node.id].update(keys)
                if e.attrs.get("chrome"):
                    self._chrome.update(keys)
        return node

    def record(self, src: str, action: Action, dst: str) -> None:
        key = f"{action.kind}:{action.target.key() if action.target else action.value}"
        self.edges.setdefault(src, {})[key] = dst

    # ------------------------------------------------------------------ frontier

    def _eligible(self, obs: Observation) -> list[tuple[Element, tuple[str, ...]]]:
        """Controls worth trying on this screen, each with its input variants."""
        out: list[tuple[Element, tuple[str, ...]]] = []
        forms_with_button = {e.attrs.get("form") for e in obs.elements if e.role == "button" and e.attrs.get("form")}
        for e in obs.elements:
            if e.disabled:
                continue
            if e.role in FIELD_ROLES:
                # A field is its own frontier item only when nothing submits it (search-as-you-press-Enter).
                if e.attrs.get("form") in forms_with_button or not (e.role == "searchbox" or "search" in f"{e.name} {e.attrs.get('name', '')}".lower()):
                    continue
                out.append((e, FUZZ_VARIANTS))
            elif e.role == "file":
                out.append((e, UPLOAD_VARIANTS))
            elif e.role == "draggable":
                out.append((e, (DRAG,)))
            elif e.role in CLICK_ROLES:
                href = e.attrs.get("href", "")
                if e.role == "link":
                    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                        continue
                    if origin_of(urljoin(obs.url, href)) != self.origin:
                        continue
                has_fields = e.role == "button" and bool(obs.form_fields(e.attrs.get("form", "-")) or obs.form_uploads(e.attrs.get("form", "-")))
                variants = FUZZ_VARIANTS if has_fields else (VALID,)
                out.append((e, (*variants, DRAG) if e.attrs.get("draggable") else variants))
        return out

    def _priority(self, obs: Observation, e: Element) -> int:
        if e.role == "link":
            target = path_pattern(urljoin(obs.url, e.attrs.get("href", "")))
            return 3 if target not in self._seen_paths else 0  # breadth first: reveal new screens early
        if e.role == "button" and e.attrs.get("form"):
            return 2
        return 1

    def claim(self, obs: Observation, variants: tuple[str, ...], scope: str, areas: list[str] | None = None) -> tuple[Element, str] | None:
        """Atomically take one unexplored (control, variant) on this screen."""
        node = self.states.get(obs.state_id)
        if node is None or not node.explorable or not _in_areas(node.path, areas):
            return None
        done = self._done.setdefault((scope, node.id), set())
        chrome_done = self._chrome_done.setdefault(scope, set())
        best: tuple[int, Element, str] | None = None
        for e, vs in self._eligible(obs):
            if areas and e.role == "link" and not _in_areas(url_path(urljoin(obs.url, e.attrs.get("href", ""))), areas):
                continue  # leads out of the area this run is focused on
            for v in vs:
                item = f"{e.key()}@{v}"
                if v in variants and item not in done and item not in chrome_done:
                    prio = self._priority(obs, e)
                    if best is None or prio > best[0]:
                        best = (prio, e, v)
                    break
        if best is None:
            return None
        _, element, variant = best
        item = f"{element.key()}@{variant}"
        done.add(item)
        if item in self._chrome:  # the same nav link on 40 screens is one control, not 40
            chrome_done.add(item)
        return element, variant

    def remaining(self, state_id: str, variants: tuple[str, ...], scope: str) -> int:
        todo = self._items.get(state_id, set()) - self._done.get((scope, state_id), set()) - self._chrome_done.get(scope, set())
        return sum(1 for k in todo if k.rsplit("@", 1)[-1] in variants)

    def next_target(self, exclude: set[str], variants: tuple[str, ...], scope: str, areas: list[str] | None = None) -> StateNode | None:
        """The screen a free tester should go to next: least crowded, then most left to do."""
        best: tuple[int, int, StateNode] | None = None
        for node in self.states.values():
            if node.id in exclude or not node.explorable or not _in_areas(node.path, areas):
                continue
            left = self.remaining(node.id, variants, scope)
            if left == 0:
                continue
            rank = (-self._targeted.get(f"{scope}|{node.id}", 0), left)
            if best is None or rank > (best[0], best[1]):
                best = (rank[0], rank[1], node)
        if best is None:
            return None
        key = f"{scope}|{best[2].id}"
        self._targeted[key] = self._targeted.get(key, 0) + 1
        return best[2]

    # ------------------------------------------------------------------ reading

    def coverage(self) -> dict[str, Any]:
        # Site chrome counts once, however many screens repeat it.
        local = {s: v - self._chrome for s, v in self._items.items()}
        total = sum(len(v) for v in local.values()) + len(self._chrome)
        exercised = self._exercised()
        chrome = set().union(*self._chrome_done.values()) if self._chrome_done else set()
        done = sum(len(exercised.get(s, set()) & local[s]) for s in local) + len(chrome)
        return {
            "screens": len(self.states),
            "templates": len(self._templates),
            "transitions": sum(len(v) for v in self.edges.values()),
            "controls_found": total,
            "controls_exercised": done,
            "control_coverage": round(done / total, 3) if total else 0.0,
            "scopes": sorted({scope for scope, _ in self._done}),
        }

    def _exercised(self) -> dict[str, set[str]]:
        """state -> items exercised by any scope."""
        out: dict[str, set[str]] = {}
        for (_, state), items in self._done.items():
            out.setdefault(state, set()).update(items)
        return out

    def brief(self, limit: int = 40) -> str:
        """Bounded text summary for a planning model."""
        rows = sorted(self.states.values(), key=lambda n: (n.path, -n.controls))
        seen: set[str] = set()
        lines: list[str] = []
        for n in rows:
            pattern = path_pattern(n.url)
            if pattern in seen:
                continue
            seen.add(pattern)
            lines.append(f"- {pattern} | {n.title} | {'; '.join(n.headings[:2])} | {n.controls} controls")
            if len(lines) >= limit:
                break
        return "\n".join(lines) or "(nothing mapped yet)"

    def relative(self, node: StateNode) -> str:
        u = urlsplit(node.url)
        return u.path + (f"?{u.query}" if u.query else "")

    def to_json(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "states": [n.model_dump() for n in self.states.values()],
            "edges": self.edges,
            "items": {k: sorted(v) for k, v in self._items.items()},
            "chrome": sorted(self._chrome),
            # A run that explored nothing (a scenario run) must not wipe the coverage the last exploring run recorded.
            "done": {k: sorted(v) for k, v in self._exercised().items()} or self._last_exercised,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], max_per_template: int = 8) -> ProductMap:
        """Reload a persisted map. Screens and controls are kept (the next run knows
        where to go); exploration progress is not, so every control is re-tested
        against the current build."""
        pm = cls(data["origin"], max_per_template)
        for raw in data.get("states", []):
            node = StateNode(**raw)
            pm.states[node.id] = node
            pm._templates[node.template] = pm._templates.get(node.template, 0) + 1
            pm._seen_paths.add(path_pattern(node.url))
            pm._items[node.id] = set(data.get("items", {}).get(node.id, []))
        pm.edges = data.get("edges", {})
        pm._chrome = set(data.get("chrome", []))
        pm._last_exercised = data.get("done", {})
        return pm


def _in_areas(path: str, areas: list[str] | None) -> bool:
    return not areas or any(path.startswith(a) for a in areas)
