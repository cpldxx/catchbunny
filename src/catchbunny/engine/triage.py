"""Signals -> findings: dedupe, verify in fresh browsers, minimise, rank.

The swarm produces raw signals, many of them duplicates seen by different
testers. Triage turns them into a short list a human can trust:

1. **Dedupe** by signature: one candidate per distinct defect.
2. **Verify**: replay the trace that produced it in fresh, isolated browser
   contexts. Reproduced every time -> confirmed; sometimes -> flaky; never ->
   not reproduced (and not reported as a bug).
3. **Localise + minimise**: first try only the actions since the last page
   load (most bugs are local to a screen), then delta-debug what remains down
   to a minimal reproduction.
4. **Rank** by severity rules that depend only on the evidence.

Candidates are processed concurrently; the browser pool bounds the fan-out.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

from ..browser.session import ActResult, Recorder, Session
from ..domain import Action, Check, Finding, FindingStatus, Mission, Severity, Signal, Step, Verification, path_pattern, url_path
from .replay import ddmin, replay
from .tester import MissionResult

OpenSession = Callable[..., AbstractAsyncContextManager[Session]]  # (mission, record_dir=None)
_ORDER = {Severity.P0: 0, Severity.P1: 1, Severity.P2: 2, Severity.P3: 3}
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Controls a person came to the page to use. One of these doing nothing stops them; a dead "Like" does not.
_PRIMARY = re.compile(r"\b(buy|pay|checkout|check out|place order|order|submit|save|send|apply|confirm|continue|next|sign in|sign up|log in|register|add to (cart|bag|basket)|subscribe|book|reserve|upload|create|delete|remove|update)\b", re.I)


@dataclass(slots=True)
class Candidate:
    signal: Signal
    mission: Mission
    trace: list[Step]
    step: int
    occurrences: int = 0
    missions: set[str] = field(default_factory=set)
    screenshot: str = ""


def collect(results: list[MissionResult], anonymous: frozenset[str] | set[str] = frozenset()) -> list[Candidate]:
    """One candidate per signature.

    Represented by the least-privileged witness, then the shortest trace: a bug an
    anonymous visitor can hit is reproduced as a visitor - fewer preconditions in
    the report, and a replay that needs no sign-in and no account lock.
    """

    def rank(mission: Mission, step: int) -> tuple[int, int]:
        return (0 if mission.persona in anonymous else 1, step)

    by_sig: dict[str, Candidate] = {}
    for r in results:
        for step in r.trace:
            for s in step.signals:
                c = by_sig.get(s.signature)
                if c is None:
                    c = by_sig[s.signature] = Candidate(signal=s, mission=r.mission, trace=r.trace, step=step.index)
                elif rank(r.mission, step.index) < rank(c.mission, c.step):
                    c.signal, c.mission, c.trace, c.step = s, r.mission, r.trace, step.index
                c.occurrences += 1
                c.missions.add(r.mission.id)
                c.screenshot = c.screenshot or next((t.screenshot for t in r.trace[step.index :] if t.screenshot), "")
    return sorted(by_sig.values(), key=lambda c: (_ORDER[describe(c.signal, c.mission.device)[0]], c.step))


def describe(s: Signal, device: str = "desktop") -> tuple[Severity, str, str, str]:
    """(severity, title, expected, actual) from evidence alone - no model involved."""
    d = s.detail
    where = path_pattern(s.url)
    match s.kind:
        case "http_error":
            method, status, rtype = d.get("method", "GET"), int(d.get("status", 0) or 0), d.get("resource_type", "")
            target = path_pattern(d.get("url", ""))
            if status >= 500:
                sev = Severity.P1 if method in _WRITE_METHODS or rtype == "document" else Severity.P2
                return sev, f"{method} {target} returns {status}", "The request succeeds or fails with a handled 4xx.", f"The server answered {status} ({s.message})."
            if rtype == "document":
                return Severity.P2, f"Dead link: {target} returns 404", "Every in-app link leads to a page.", f"Following a link from {where} lands on a 404."
            return Severity.P3, f"{method} {target} returns 404", "Every resource the page requests exists.", f"{s.message} (requested from {where})."
        case "page_error":
            return Severity.P2, f"Uncaught exception on {where}: {s.message[:90]}", "Interacting with the page raises no uncaught exception.", s.message
        case "console_error":
            return Severity.P3, f"Console error on {where}: {s.message[:90]}", "The page logs no errors.", s.message
        case "request_failed":
            return Severity.P2, f"Request failed: {d.get('method', 'GET')} {path_pattern(d.get('url', ''))}", "Requests complete.", s.message
        case "broken_image":
            return Severity.P3, f"Broken image {url_path(d.get('src', ''))}", "Every visible image loads.", f"The image on {where} failed to load."
        case "a11y":
            rule = d.get("rule", "")
            what = {"input-missing-label": "Form field has no accessible label", "img-missing-alt": "Image has no alt text"}.get(
                rule, "Control has no accessible name"
            )
            return Severity.P3, f"{what} ({d.get('element', '')[:60]})", "Every control exposes a name to assistive technology.", d.get("html", s.message)
        case "layout":
            sev = Severity.P2 if device in ("iphone", "android") else Severity.P3
            return sev, f"{where} overflows horizontally on {device}", "The page fits the viewport width.", f"{s.message} at {d.get('viewport')}."
        case "dead_click":
            # Proven by replay like everything else, so how much it matters is a question of which control it is.
            sev = Severity.P2 if _PRIMARY.search(str(d.get("label", ""))) else Severity.P3
            return sev, f"{d.get('label', 'Control')} does nothing on {where}", "Activating the control has an effect.", s.message
        case "check_failed":
            return Severity.P1, s.message, d.get("expected", "The scenario outcome holds."), d.get("actual", "The typed check failed.")
        case _:  # agent_report
            sev = Severity(d.get("severity", "P2"))
            return sev, d.get("title", s.message), d.get("expected", ""), d.get("actual", "")


def _repro_slices(c: Candidate) -> tuple[list[Action], list[Action]]:
    """(local, full): actions since the last page load, and the whole prefix."""
    steps = [st for st in c.trace[: c.step + 1] if st.ok or st.index == c.step]
    full = [st.action for st in steps]
    last_nav = max((i for i, st in enumerate(steps) if st.action.kind == "navigate"), default=0)
    return full[last_nav:], full


class Triage:
    def __init__(
        self,
        *,
        run_id: str,
        project_id: str,
        open_session: OpenSession,
        verify_runs: int,
        minimize: bool,
        max_replays: int,
        concurrency: int,
        record: bool = False,
        anonymous: set[str] | None = None,
    ) -> None:
        self.run_id, self.project_id, self.record = run_id, project_id, record
        self.anonymous = anonymous or set()
        self.open_session = open_session
        self.verify_runs, self.minimize, self.max_replays = verify_runs, minimize, max_replays
        self._gate = asyncio.Semaphore(concurrency)
        self.replays = 0

    async def _reproduces(self, c: Candidate, actions: list[Action], check: Check | None) -> bool:
        if not actions:
            return False
        self.replays += 1
        sig = c.signal.signature
        try:
            async with self.open_session(c.mission) as session:
                if check is not None:  # model-reported bug: the typed check must fail again
                    out = await replay(session, actions)
                    if out.failed_at >= 0 or out.final is None:
                        return False
                    result = await session.run_check(check, out.final, out.signals)
                    return result.evaluable and not result.passed
                out = await replay(session, actions, until=lambda signals: any(s.signature == sig for s in signals))
                return out.hit
        except Exception:
            return False  # a replay that cannot run did not reproduce

    async def _count(self, c: Candidate, actions: list[Action], check: Check | None) -> int:
        hits = await asyncio.gather(*(self._reproduces(c, actions, check) for _ in range(self.verify_runs)))
        return sum(hits)

    async def finding(self, c: Candidate) -> Finding:
        severity, title, expected, actual = describe(c.signal, c.mission.device)
        raw_check = c.signal.detail.get("check")
        check = Check(**raw_check) if isinstance(raw_check, dict) else None
        local, full = _repro_slices(c)
        f = Finding(
            run_id=self.run_id,
            project_id=self.project_id,
            signature=c.signal.signature,
            kind=c.signal.kind,
            severity=severity,
            title=title,
            expected=expected,
            actual=actual,
            url=c.signal.url,
            persona=c.mission.persona,
            device=c.mission.device,
            browser=c.mission.browser,
            repro=full,
            original_steps=len(full),
            occurrences=c.occurrences,
            missions=sorted(c.missions),
            evidence={"signal": c.signal.model_dump(), "screenshot": c.screenshot},
        )
        if c.signal.kind == "agent_report" and (check is None or not check.witnesses()):
            # A judgement with no oracle, or one whose oracle only shows that something is
            # missing (see Check.witnesses), stays UNVERIFIED and says why. It is never replayed
            # into a "confirmed": that would launder an opinion into a fact.
            f.check = check
            f.evidence["note"] = (
                "Reported by the model without a typed check." if check is None else
                f"The model's check, {check.describe()}, fails because something is missing. A replay cannot tell that apart from a wrong expectation, so this was not verified."
            )
            return f
        if check is None and c.signal.kind != "agent_report":
            f.check = Check(kind="no_signal", value=c.signal.signature)
        else:
            f.check = check
        # A detector's signature is a complete oracle: any path that fires it is the same defect,
        # so the path can be cut down freely. A model's check is not - "Cart (2) is shown" is a
        # defect only after the steps that emptied the cart, and the check does not say so.
        # Shortening against it would keep the check failing and lose the bug. A model's report
        # is therefore replayed along the tester's whole path, and that path is the reproduction.
        reported = c.signal.kind == "agent_report"
        if reported:
            local = full
        async with self._gate:
            hits = await self._count(c, local, check)
            repro = local
            if hits < self.verify_runs and len(full) > len(local):
                full_hits = await self._count(c, full, check)
                if full_hits > hits:
                    hits, repro = full_hits, full
            f.verification = Verification(runs=self.verify_runs, reproduced=hits)
            f.repro = repro
            if hits == self.verify_runs:
                f.status = FindingStatus.CONFIRMED
                if self.minimize and len(repro) > 1 and not reported:
                    f.repro = await self._minimize(c, repro, check)
                    f.minimized = True
                if self.record:
                    f.evidence.update(await self._record(c, f.repro, check))
            else:
                f.status = FindingStatus.FLAKY if hits else FindingStatus.NOT_REPRODUCED
        return f

    async def _minimize(self, c: Candidate, repro: list[Action], check: Check | None) -> list[Action]:
        """Delta-debug everything after the opening navigation.

        The page the reproduction starts on is pinned: a reproduction must say
        where it begins, and must not come to depend on wherever a session
        happens to be standing (after sign-in, say).
        """
        head, tail = (repro[:1], repro[1:]) if repro[0].kind == "navigate" else ([], repro)
        if head and await self._reproduces(c, head, check):
            return head  # opening the page is enough
        if len(tail) < 2:
            return repro
        kept, _ = await ddmin(tail, lambda actions: self._reproduces(c, head + actions, check), max_tests=self.max_replays)
        return head + kept

    async def _record(self, c: Candidate, actions: list[Action], check: Check | None) -> dict[str, Any]:
        """One last replay of the minimal reproduction, this time on camera.

        Produces what a person needs to believe and fix the bug without re-running
        anything: a video, a Playwright trace (DOM snapshots, openable in the trace
        viewer), the network and console logs, and a timeline that puts the actions,
        the requests and the moment the defect fired on one clock.
        """
        sig = c.signal.signature
        timeline: list[dict[str, Any]] = []
        fired: list[int] = []
        with tempfile.TemporaryDirectory(prefix="catchbunny-rec-") as tmp:
            try:
                async with self.open_session(c.mission, record_dir=tmp) as session:
                    rec = session.recorder = Recorder()
                    await session.context.tracing.start(screenshots=True, snapshots=True)

                    def on_step(i: int, action: Action, res: ActResult) -> None:
                        timeline.append({"index": i, "action": action.describe(), "t_ms": max(0, rec.ms() - res.duration_ms), "ms": res.duration_ms, "ok": res.ok})

                    def seen(signals: list[Signal]) -> bool:
                        if not fired and any(s.signature == sig for s in signals):
                            fired.append(rec.ms())
                        return False  # keep going: record the whole reproduction

                    out = await replay(session, actions, until=seen if check is None else None, on_step=on_step)
                    if check is not None and out.final is not None and not (await session.run_check(check, out.final, out.signals)).passed:
                        fired.append(rec.ms())
                    await asyncio.sleep(0.25)  # let the final frame reach the video
                    screenshot = await session.screenshot()
                    await session.context.tracing.stop(path=f"{tmp}/trace.zip")
                    video = session.page.video
                # The context is closed here, which is what finalises the video file.
                artifacts = session.artifacts
                if artifacts is None:
                    return {}
                return {
                    "video": await artifacts.put_file(await video.path(), "webm") if video else "",
                    "trace": await artifacts.put_file(f"{tmp}/trace.zip", "zip"),
                    "screenshot": screenshot,
                    "timeline": timeline,
                    "network": rec.network,
                    "console": rec.console,
                    "fired_at_ms": fired[0] if fired else None,
                    "video_offset_ms": int((rec.started - session.created) * 1000),  # the video also shows sign-in
                    "duration_ms": rec.ms(),
                }
            except Exception as exc:  # evidence is a bonus; a failed recording never loses the finding
                return {"recording_error": f"{type(exc).__name__}: {exc}"[:200]}

    async def run(self, results: list[MissionResult], limit: int = 40) -> AsyncIterator[Finding]:
        """Yield findings as each finishes verification (completion order, not severity)."""
        tasks = [asyncio.create_task(self.finding(c)) for c in collect(results, self.anonymous)[:limit]]
        try:
            for done in asyncio.as_completed(tasks):
                yield await done
        finally:
            for t in tasks:
                t.cancel()


def rank(findings: list[Finding]) -> list[Finding]:
    status_order = {FindingStatus.CONFIRMED: 0, FindingStatus.FLAKY: 1, FindingStatus.UNVERIFIED: 2, FindingStatus.NOT_REPRODUCED: 3}
    return sorted(findings, key=lambda f: (status_order[f.status], _ORDER[f.severity], -f.occurrences))


def summarize(findings: list[Finding]) -> dict[str, Any]:
    confirmed = [f for f in findings if f.status is FindingStatus.CONFIRMED]
    return {
        "candidates": len(findings),
        "confirmed": len(confirmed),
        "flaky": sum(f.status is FindingStatus.FLAKY for f in findings),
        "not_reproduced": sum(f.status is FindingStatus.NOT_REPRODUCED for f in findings),
        "unverified": sum(f.status is FindingStatus.UNVERIFIED for f in findings),
        "by_severity": {s.value: sum(f.severity is s for f in confirmed) for s in Severity},
        "repro_steps_before": sum(f.original_steps for f in confirmed),
        "repro_steps_after": sum(len(f.repro) for f in confirmed),
    }
