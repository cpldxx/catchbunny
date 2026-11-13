"""The Brain port: what decides a tester's next move.

The engine never knows whether a model, a rule set or a test script is driving.
A brain reads an Observation plus Feedback on its previous intents and returns
new Intents. Everything a brain can do is one of five intent kinds, and every
intent gets exactly one IntentResult back - which is also precisely the
tool_use / tool_result contract a model needs, so the Claude adapter is a thin
translation rather than a second engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from ..browser.observe import Observation
from ..budget import Usage
from ..domain import Action, Check, Mission, Policy, Scenario, Severity, Signal, Verdict


class FindingDraft(BaseModel):
    title: str
    expected: str = ""
    actual: str = ""
    severity: Severity = Severity.P2
    check: Check | None = None  # a typed check that FAILS while the bug exists


class Intent(BaseModel):
    kind: Literal["act", "check", "report", "step_done", "finish"]
    id: str = ""  # correlates with the model's tool_use id
    action: Action | None = None
    check: Check | None = None
    report: FindingDraft | None = None
    step_index: int = -1
    passed: bool = True
    verdict: Verdict | None = None
    summary: str = ""
    thought: str = ""


class IntentResult(BaseModel):
    id: str = ""
    ok: bool = True
    message: str = "ok"


class Feedback(BaseModel):
    results: list[IntentResult] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)  # defects the detectors saw since the last decision


@dataclass(slots=True)
class Brief:
    """Everything a brain may know about its assignment. Contains no secrets."""

    mission: Mission
    base_url: str
    policy: Policy
    persona: dict[str, Any] = field(default_factory=dict)
    knowledge: str = ""
    goal: str = ""
    scenario: Scenario | None = None
    first_step: int = 0  # scenario steps before this index were already completed by replay
    areas: list[str] = field(default_factory=list)


class Brain(Protocol):
    name: str

    async def decide(self, obs: Observation, feedback: Feedback) -> list[Intent]: ...

    def usage(self) -> Usage: ...
