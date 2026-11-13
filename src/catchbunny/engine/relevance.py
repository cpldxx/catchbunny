"""Which saved scenarios does a code change put at risk?

Running every scenario on every pull request does not scale; running the ones
the diff can reach does. This is the deterministic baseline: score each
scenario by the vocabulary it shares with the changed paths and added lines,
weighting path tokens (a change under `checkout/` is strong evidence for a
checkout scenario) above body tokens. It is fast, explainable ("matched:
cart, coupon") and needs no model; a model re-rank can sit on top of it.
"""

from __future__ import annotations

import re
from typing import Any

from ..domain import Scenario

_TOKEN = re.compile(r"[a-z]{3,}")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_STOP = frozenset(
    "the and for with from that this then when click open fill page button link user can use src app lib test tests index main "
    "component components view views utils util html css tsx jsx json yaml true false null none return import export const function class def self".split()
)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(_CAMEL.sub(" ", text).lower()) if t not in _STOP}


def select(scenarios: list[Scenario], changed_files: list[str], diff: str = "", threshold: float = 0.2) -> list[dict[str, Any]]:
    path_tokens = _tokens(" ".join(changed_files))
    added = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    body_tokens = _tokens(added)
    ranked: list[dict[str, Any]] = []
    for sc in scenarios:
        mine = _tokens(" ".join([sc.name, sc.start, *sc.steps, *sc.outcomes, *sc.tags]))
        if not mine:
            continue
        by_path, by_body = mine & path_tokens, (mine & body_tokens) - path_tokens
        score = min(1.0, (2.0 * len(by_path) + 0.5 * len(by_body)) / max(3.0, len(mine) ** 0.5 * 2))
        ranked.append(
            {
                "scenario_id": sc.id,
                "name": sc.name,
                "confidence": round(score, 2),
                "selected": score >= threshold,
                "matched": sorted(by_path | by_body)[:12],
            }
        )
    return sorted(ranked, key=lambda r: r["confidence"], reverse=True)
