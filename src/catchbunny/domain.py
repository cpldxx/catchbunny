"""Domain model: the vocabulary every other layer speaks.

Nothing in here does I/O. Models are plain pydantic so they serialise the same
way into SQLite, the event stream, the HTTP API and scenario files.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- helpers

_NUM = re.compile(r"\d+")
_HEX = re.compile(r"\b[0-9a-f]{8,}\b", re.I)
_WS = re.compile(r"\s+")
_ID_SEGMENT = re.compile(r"^(\d+|[0-9a-f]{8,}|[0-9a-f-]{32,})$", re.I)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


def sha(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:n]


def norm_text(text: str) -> str:
    """Collapse the parts of a string that vary between runs (ids, counters)."""
    text = _HEX.sub("#", text or "")
    text = _NUM.sub("#", text)
    return _WS.sub(" ", text).strip().lower()[:200]


def url_path(url: str) -> str:
    return urlsplit(url).path or "/"


def path_pattern(url: str) -> str:
    """`/products/42/reviews` -> `/products/#/reviews` (template-level identity)."""
    parts = [("#" if _ID_SEGMENT.match(p) else p) for p in url_path(url).split("/")]
    return "/".join(parts) or "/"


def origin_of(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}"


# --------------------------------------------------------------------------- enums


BROWSERS = ("chromium", "firefox", "webkit")  # WebKit is the engine Safari is built on
BrowserName = Literal["chromium", "firefox", "webkit"]


class Severity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RunKind(StrEnum):
    DISCOVERY = "discovery"  # map the product, draft scenarios
    DEEP_SCAN = "deep_scan"  # goal-directed bug hunt by the swarm
    SCENARIOS = "scenarios"  # run saved regression scenarios


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELED = "canceled"
    ERROR = "error"


class Verdict(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"  # could not run (login failed, no brain, policy)
    INCONCLUSIVE = "inconclusive"  # ran, but nothing proves the outcome


class Policy(StrEnum):
    READ_ONLY = "read_only"  # look, navigate, search; never mutate
    SAFE_WRITES = "safe_writes"  # normal user writes; no account-destroying actions
    FULL = "full"


class FindingStatus(StrEnum):
    CONFIRMED = "confirmed"  # reproduced in every fresh-browser replay
    FLAKY = "flaky"  # reproduced in some replays
    NOT_REPRODUCED = "not_reproduced"
    UNVERIFIED = "unverified"  # model judgement with no typed check to replay


SignalKind = Literal[
    "http_error",
    "page_error",
    "console_error",
    "request_failed",
    "broken_image",
    "a11y",
    "layout",
    "dead_click",
    "check_failed",
    "agent_report",
]

# --------------------------------------------------------------------------- page vocabulary


class ElementSig(BaseModel):
    """Everything needed to find an element again in a *different* page load.

    `ref` is an ephemeral handle valid for one observation only; the remaining
    fields are the durable signature used by replay and self-healing.
    """

    ref: str = ""
    role: str = ""
    name: str = ""
    tag: str = ""
    text: str = ""
    attrs: dict[str, str] = Field(default_factory=dict)
    near: str = ""
    css: str = ""
    frame: str = ""  # which iframe of the page it lives in: the frame's name, or the path it loaded; "" is the page itself

    def key(self) -> str:
        """Identity of the element inside one screen state (frontier bookkeeping)."""
        href = self.attrs.get("href", "")
        key = "|".join(
            (self.role, norm_text(self.name), self.attrs.get("name", ""), self.attrs.get("type", ""), url_path(href) if href else "")
        )
        return f"{key}|frame:{self.frame}" if self.frame else key  # the same button in two frames is two controls

    def label(self) -> str:
        return f'{self.role} "{self.name}"' if self.name else f"{self.role} <{self.tag}>"


ActionKind = Literal["navigate", "click", "fill", "select", "check", "press", "hover", "back", "wait", "upload", "drag"]


class Action(BaseModel):
    kind: ActionKind
    target: ElementSig | None = None
    value: str = ""  # upload: which sample file (see browser/samples.py), never a path on this machine
    to: ElementSig | None = None  # drag: what the target is dropped on
    # "username" | "password": resolved from the persona's credential by the
    # supervisor at execution time. The secret never enters `value`, the event
    # stream, a trace, or a model prompt.
    secret: str = ""
    note: str = ""

    def describe(self) -> str:
        t = self.target.label() if self.target else ""
        match self.kind:
            case "navigate":
                return f"Open {self.value}"
            case "click":
                return f"Click {t}"
            case "fill":
                shown = f"the persona's {self.secret}" if self.secret else f'"{self.value}"'
                return f"Fill {t} with {shown}"
            case "select":
                return f'Select "{self.value}" in {t}'
            case "check":
                return f"Check {t}"
            case "press":
                return f"Press {self.value}"
            case "hover":
                return f"Hover {t}"
            case "back":
                return "Go back"
            case "upload":
                return f"Upload {self.value or 'a file'} to {t}"
            case "drag":
                return f"Drag {t} onto {self.to.label() if self.to else 'itself'}"
            case _:
                return f"Wait {self.value}ms"


CheckKind = Literal[
    "url_contains",
    "url_matches",
    "title_contains",
    "text_visible",
    "text_absent",
    "element_visible",
    "element_absent",
    "value_equals",
    "no_signal",
    "no_http_error",
]


class Check(BaseModel):
    """A typed, machine-evaluated assertion. Verdicts rest on these, never on prose."""

    kind: CheckKind
    value: str = ""
    role: str = ""
    name: str = ""

    def describe(self) -> str:
        match self.kind:
            case "element_visible" | "element_absent" | "value_equals":
                tail = f' = "{self.value}"' if self.kind == "value_equals" else ""
                return f'{self.kind}({self.role} "{self.name}"){tail}'
            case _:
                return f'{self.kind}("{self.value}")'

    def well_formed(self) -> bool:
        """Whether the check says what it is about. An empty operand matches everything or
        nothing - `text_absent("")` "fails" on every page ever rendered - and proves neither."""
        match self.kind:
            case "element_visible" | "element_absent" | "value_equals":
                return bool(norm_text(self.name))  # value_equals may rightly expect an empty value, never an unnamed field
            case "no_http_error":
                return True  # an empty value means "any request"
            case _:
                return bool(self.value.strip())

    def witnesses(self) -> bool:
        """Whether this check, failing, shows something that IS on the page.

        `text_absent` fails because the text is there; `value_equals` because the field
        holds something else. Those failures are observations, and a replay can repeat
        them. `text_visible` and `element_visible` fail because something is *missing* -
        and a missing thing looks the same whether the app is broken or the expectation
        was wrong. Worse, removing steps from a reproduction can only keep it missing, so
        minimisation would shrink such a "proof" to nothing. Absence is therefore a claim,
        never a verified defect.
        """
        return self.kind not in ("text_visible", "element_visible")


class CheckResult(BaseModel):
    check: Check
    passed: bool
    detail: str = ""
    evaluable: bool = True  # False: the check's subject is not on the page, so "failed" would mean nothing


class Signal(BaseModel):
    """One raw observation that something is wrong. Many signals -> one finding."""

    kind: SignalKind
    message: str
    url: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    step: int = -1
    signature: str = ""

    def model_post_init(self, _ctx: Any) -> None:
        if not self.signature:
            self.signature = signal_signature(self.kind, self.message, self.detail, self.url)


def signal_signature(kind: str, message: str, detail: dict[str, Any], page_url: str = "") -> str:
    """Stable identity of a defect across testers, sessions and replays."""
    if kind in ("http_error", "request_failed"):
        basis = f"{detail.get('method', 'GET')} {path_pattern(detail.get('url', ''))} {detail.get('status', '')}"
    elif kind == "broken_image":
        basis = path_pattern(detail.get("src", ""))
    elif kind == "a11y":
        basis = f"{detail.get('rule', '')}|{detail.get('element', '')}"
    elif kind == "layout":
        basis = f"{detail.get('rule', '')}|{path_pattern(page_url)}|{detail.get('device', '')}"
    elif kind == "dead_click":
        basis = f"{detail.get('element', '')}|{path_pattern(page_url)}"
    elif kind == "check_failed":
        basis = detail.get("check", message)
    elif kind == "agent_report" and isinstance(detail.get("check"), dict):
        # A model words the same defect differently each time it reports it. Its check is what
        # it proved, so two reports with one check on one kind of page are one defect.
        c = detail["check"]
        basis = f"{c.get('kind', '')}|{norm_text(str(c.get('value', '')))}|{c.get('role', '')}|{norm_text(str(c.get('name', '')))}|{path_pattern(page_url)}"
    else:
        basis = norm_text(message)
    return f"{kind}:{sha(basis)}"


class Step(BaseModel):
    index: int
    action: Action
    ok: bool = True
    error: str = ""
    healed: bool = False
    url_before: str = ""
    url_after: str = ""
    state_after: str = ""
    duration_ms: int = 0
    screenshot: str = ""
    thought: str = ""
    signals: list[Signal] = Field(default_factory=list)


# --------------------------------------------------------------------------- project setup


class Credential(BaseModel):
    username: str
    password: str


class LoginRecipe(BaseModel):
    """Deterministic sign-in. Blank selectors are auto-detected from the page."""

    path: str = "/login"
    username_selector: str = ""
    password_selector: str = ""
    submit_selector: str = ""


class Persona(BaseModel):
    name: str = "visitor"
    role: str = "Signed-out visitor"
    goals: str = ""
    credential: Credential | None = None

    def public(self) -> dict[str, Any]:
        d = self.model_dump(exclude={"credential"})
        d["has_credential"] = self.credential is not None
        return d


class Environment(BaseModel):
    name: str = "default"
    base_url: str
    login: LoginRecipe = Field(default_factory=LoginRecipe)
    # The app's own "put the test data back" endpoint, as a path on its origin. Called (POST) before every
    # replay whose verdict depends on state, so a reproduction never leans on what an earlier one left behind.
    reset_path: str = ""


class Project(BaseModel):
    id: str = Field(default_factory=lambda: new_id("prj"))
    name: str
    environments: list[Environment]
    personas: list[Persona] = Field(default_factory=lambda: [Persona()])
    knowledge: str = ""  # free-form product notes handed to planners and testers
    policy: Policy = Policy.SAFE_WRITES
    created_at: float = Field(default_factory=now)

    def environment(self, name: str = "default") -> Environment:
        for e in self.environments:
            if e.name == name:
                return e
        return self.environments[0]

    def persona(self, name: str) -> Persona:
        for p in self.personas:
            if p.name == name:
                return p
        return self.personas[0]

    def public(self) -> dict[str, Any]:
        d = self.model_dump(exclude={"personas"})
        d["personas"] = [p.public() for p in self.personas]
        return d


# --------------------------------------------------------------------------- scenarios


class LearnedStep(BaseModel):
    """Actions + checks that completed a step in a passing run; replayed without a model."""

    step_hash: str
    actions: list[Action]
    checks: list[Check] = Field(default_factory=list)
    source_run: str = ""
    learned_at: float = Field(default_factory=now)


def step_hash(text: str) -> str:
    return sha(_WS.sub(" ", text.strip().lower()), 16)


class Scenario(BaseModel):
    id: str = Field(default_factory=lambda: new_id("scn"))
    project_id: str = ""
    name: str
    persona: str = "visitor"
    device: str = "desktop"
    browser: BrowserName = "chromium"
    policy: Policy | None = None
    start: str = "/"
    steps: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)  # plain English, judged by the model
    checks: list[Check] = Field(default_factory=list)  # typed, judged by the engine
    learned: list[LearnedStep] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_finding: str = ""
    created_at: float = Field(default_factory=now)


# --------------------------------------------------------------------------- runs


class RunSpec(BaseModel):
    kind: RunKind = RunKind.DEEP_SCAN
    environment: str = "default"
    goal: str = ""
    areas: list[str] = Field(default_factory=list)  # path prefixes to stay inside
    personas: list[str] = Field(default_factory=list)  # empty = every persona
    devices: list[str] = Field(default_factory=lambda: ["desktop"])
    browser: BrowserName = "chromium"  # the engine every tester of this run drives; a finding is replayed in the engine that found it
    testers: int = Field(default=4, ge=1, le=32)  # parallel browser contexts
    max_steps: int = Field(default=400, ge=1)  # shared step budget for the whole run
    max_minutes: float = Field(default=5.0, gt=0)
    max_usd: float = Field(default=2.0, ge=0)
    brain: Literal["auto", "heuristic", "claude", "ollama"] = "auto"
    model: str = ""  # provider model override, e.g. an Ollama tag; "" uses the configured default
    scenario_ids: list[str] = Field(default_factory=list)
    verify_runs: int = Field(default=2, ge=1, le=5)
    minimize: bool = True
    screenshots: Literal["on_signal", "every_step", "off"] = "on_signal"
    record: bool = True  # video + trace + network/console log for every confirmed finding
    gate: Severity = Severity.P1  # a confirmed finding at or above this fails the run


class Mission(BaseModel):
    """One tester's assignment: one browser context, one persona, one device."""

    id: str = Field(default_factory=lambda: new_id("msn"))
    run_id: str = ""
    kind: Literal["explore", "fuzz", "charter", "scenario"] = "explore"
    title: str = ""
    charter: str = ""
    persona: str = "visitor"
    device: str = "desktop"
    browser: BrowserName = "chromium"
    start_path: str = "/"
    max_steps: int = 60
    scenario_id: str = ""
    status: Literal["queued", "running", "done"] = "queued"
    verdict: Verdict | None = None
    summary: str = ""
    brain: str = ""
    steps_taken: int = 0
    signals: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0


class Run(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    project_id: str
    spec: RunSpec
    status: RunStatus = RunStatus.QUEUED
    phase: str = "queued"
    created_at: float = Field(default_factory=now)
    started_at: float = 0.0
    finished_at: float = 0.0
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    pid: int = 0  # the process running it: several may share one data directory (one MCP server per agent session)


class Verification(BaseModel):
    runs: int = 0
    reproduced: int = 0


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fnd"))
    run_id: str
    project_id: str = ""
    signature: str
    kind: str
    severity: Severity
    title: str
    expected: str
    actual: str
    url: str = ""
    persona: str = "visitor"
    device: str = "desktop"
    browser: BrowserName = "chromium"
    status: FindingStatus = FindingStatus.UNVERIFIED
    verification: Verification = Field(default_factory=Verification)
    repro: list[Action] = Field(default_factory=list)
    original_steps: int = 0  # trace length before locality + delta-debugging
    minimized: bool = False
    check: Check | None = None  # the oracle that fails while the bug exists
    occurrences: int = 1
    missions: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=now)

    def repro_text(self) -> list[str]:
        return [a.describe() for a in self.repro]
