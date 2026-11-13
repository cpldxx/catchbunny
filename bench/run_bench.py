"""Seeded-bug benchmark: does the swarm find what was planted, and only that?

    uv run python bench/run_bench.py            # full run, writes bench/RESULTS.md
    uv run python bench/run_bench.py --quick    # skip the scaling sweep

It starts two builds of the demo shop as separate processes (so the target's
work does not run on the swarm's event loop), then measures:

1. Recall / precision on the buggy build, against the manifest in buggy_shop.
2. False positives on the fixed build (which still contains the traps).
3. The regression loop: every confirmed finding, exported as a scenario, must
   FAIL on the buggy build and PASS on the fixed build - with zero model calls.
4. Scaling: wall-clock time to exhaust the same frontier with 1, 2, 4, 8 testers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from buggy_shop import BUGS, PASSWORD, USER  # noqa: E402

from catchbunny.config import Settings  # noqa: E402
from catchbunny.domain import Credential, Environment, Finding, Persona, Project, RunKind, RunSpec  # noqa: E402
from catchbunny.engine.orchestrator import App  # noqa: E402
from catchbunny.engine.scenarios import finding_to_scenario  # noqa: E402

HERE = Path(__file__).parent


def match(f: Finding) -> str | None:
    """Which seeded bug does this finding correspond to? None = not a seeded bug."""
    signal = f.evidence.get("signal", {})
    d, msg = signal.get("detail", {}), signal.get("message", "")
    url, status = str(d.get("url", "")), d.get("status")
    if f.kind == "http_error" and "/api/orders" in url and status == 500:
        return "checkout_500"
    if f.kind == "page_error" and "map" in msg:
        return "reviews_crash"
    if f.kind == "broken_image" and "bottle" in str(d.get("src", "")):
        return "broken_image"
    if f.kind == "dead_click" and "coupon" in str(d.get("label", "")).lower():
        return "dead_coupon"
    if f.kind == "http_error" and "/search" in url and status == 500:
        return "search_500"
    if f.kind == "a11y" and "newsletter" in str(d.get("element", "")):
        return "unlabeled_input"
    if f.kind == "layout" and "/products" in f.url:
        return "mobile_overflow"
    if (f.kind == "http_error" and "/api/prefs" in url) or (f.kind == "console_error" and "preferences" in msg):
        return "prefs_error"
    if f.kind == "http_error" and "/shipping" in url and status == 404:
        return "dead_link"
    text = f"{f.title} {f.actual}".lower()
    if f.kind == "agent_report" and ("negative" in text or "-1" in text):
        return "negative_qty"
    if f.kind == "agent_report" and ("badge" in text or "cart count" in text or "cart (" in text):
        return "stale_badge"
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def start_shop(bugs: str, latency_ms: int) -> tuple[asyncio.subprocess.Process, str]:
    port = free_port()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(HERE / "buggy_shop.py"), "--port", str(port), "--bugs", bugs, "--latency-ms", str(latency_ms),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            await asyncio.to_thread(urllib.request.urlopen, url, None, 1)
            return proc, url
        except OSError:
            await asyncio.sleep(0.1)
    raise RuntimeError("demo shop did not start")


def project_for(url: str) -> Project:
    member = Persona(name="member", role="Signed-in customer", credential=Credential(username=USER, password=PASSWORD))
    return Project(name=url, environments=[Environment(base_url=url)], personas=[Persona(), member])


async def scan(app: App, url: str, *, testers: int, devices: list[str], steps: int = 2000, verify: bool = True) -> tuple[dict[str, Any], list[Finding], Project]:
    project = project_for(url)
    await app.store.put_now("project", project.id, project)
    spec = RunSpec(kind=RunKind.DEEP_SCAN, brain="heuristic", testers=testers, devices=devices, max_steps=steps, max_minutes=6, verify_runs=2 if verify else 1, minimize=verify, record=verify)
    run = await app.runs.wait((await app.runs.start(project, spec)).id)
    if run.error:
        raise RuntimeError(run.error)
    findings = [Finding(**d) for d in await app.store.list("finding", parent=run.id)]
    return run.summary, findings, project


async def run_scenarios(app: App, url: str, findings: list[Finding], testers: int) -> dict[str, Any]:
    project = project_for(url)
    await app.store.put_now("project", project.id, project)
    for f in findings:
        sc = finding_to_scenario(f)
        sc.project_id = project.id
        await app.store.put_now("scenario", sc.id, sc, parent=project.id)
    run = await app.runs.wait((await app.runs.start(project, RunSpec(kind=RunKind.SCENARIOS, brain="heuristic", testers=testers))).id)
    if run.error:
        raise RuntimeError(run.error)
    return run.summary


async def main(quick: bool, latency_ms: int) -> dict[str, Any]:
    settings = Settings()
    settings.data_dir = Path(tempfile.mkdtemp(prefix="catchbunny-bench-"))
    buggy_proc, buggy = await start_shop("all", latency_ms)
    fixed_proc, fixed = await start_shop("none", latency_ms)
    out: dict[str, Any] = {"latency_ms": latency_ms, "seeded": len(BUGS), "detectable": sum(not b["semantic"] for b in BUGS.values())}
    try:
        async with App(settings, in_memory=True) as app:
            print("1/4 buggy build ...", flush=True)
            summary, findings, _ = await scan(app, buggy, testers=8, devices=["desktop", "iphone"])
            confirmed = [f for f in findings if f.status.value == "confirmed"]
            matched = {f.id: match(f) for f in confirmed}
            found = {b for b in matched.values() if b}
            out["buggy"] = {
                "confirmed": len(confirmed),
                "true_positive_findings": sum(1 for b in matched.values() if b),
                "false_positive_findings": [f.title for f in confirmed if not matched[f.id]],
                "bugs_found": sorted(found),
                "bugs_missed": sorted(set(BUGS) - found),
                "recall_detectable": round(len([b for b in found if not BUGS[b]["semantic"]]) / out["detectable"], 3),
                "recall_all": round(len(found) / len(BUGS), 3),
                "precision": round(sum(1 for b in matched.values() if b) / len(confirmed), 3) if confirmed else 0.0,
                "not_reproduced": summary["findings"]["not_reproduced"],
                "flaky": summary["findings"]["flaky"],
                "repro_steps_before": summary["findings"]["repro_steps_before"],
                "repro_steps_after": summary["findings"]["repro_steps_after"],
                "steps": summary["budget"]["steps"],
                "hunt_seconds": summary["hunt_seconds"],
                "triage_seconds": summary["triage_seconds"],
                "steps_per_second": summary["steps_per_second"],
                "verification_replays": summary["verification_replays"],
                "coverage": summary["coverage"],
                "findings": [{"severity": f.severity.value, "title": f.title, "bug": matched[f.id], "repro_steps": len(f.repro), "from_steps": f.original_steps} for f in confirmed],
            }

            print("2/4 fixed build ...", flush=True)
            fsummary, ffindings, _ = await scan(app, fixed, testers=8, devices=["desktop", "iphone"])
            out["fixed"] = {
                "confirmed": [f.title for f in ffindings if f.status.value == "confirmed"],
                "steps": fsummary["budget"]["steps"],
                "screens": fsummary["coverage"]["screens"],
            }

            print("3/4 regression loop ...", flush=True)
            on_buggy = await run_scenarios(app, buggy, confirmed, 8)
            on_fixed = await run_scenarios(app, fixed, confirmed, 8)
            out["regression"] = {
                "scenarios": len(confirmed),
                "fail_on_buggy": on_buggy["failed"],
                "pass_on_fixed": on_fixed["passed"],
                "other_on_buggy": on_buggy["passed"] + on_buggy["blocked"] + on_buggy["inconclusive"],
                "other_on_fixed": on_fixed["failed"] + on_fixed["blocked"] + on_fixed["inconclusive"],
                "not_passing_on_fixed": [f"{s['name']}: {s['verdict']} - {s['reason']}" for s in on_fixed["scenarios"] if s["verdict"] != "passed"],
                "steps_replayed_without_model": on_fixed["steps_replayed_without_model"],
                "model_requests": on_fixed["budget"]["llm"]["requests"],
            }

            if not quick:
                print("4/4 scaling sweep ...", flush=True)
                rows = []
                for n in (1, 2, 4, 8):
                    t0 = time.monotonic()
                    s, _, _ = await scan(app, buggy, testers=n, devices=["desktop"], verify=False)
                    rows.append({"testers": n, "steps": s["budget"]["steps"], "hunt_seconds": s["hunt_seconds"], "steps_per_second": s["steps_per_second"], "wall_seconds": round(time.monotonic() - t0, 2)})
                    print(f"     testers={n}: {rows[-1]}", flush=True)
                base = rows[0]["hunt_seconds"]
                for r in rows:
                    r["speedup"] = round(base / r["hunt_seconds"], 2)
                out["scaling"] = rows
            out["pool"] = app.pool.stats()
            out["store"] = {"writes": app.store.writes, "batches": app.store.batches, "writes_per_batch": round(app.store.writes / max(1, app.store.batches), 1)}
    finally:
        for p in (buggy_proc, fixed_proc):
            p.terminate()
    return out


def ledger(r: dict[str, Any]) -> dict[str, Any]:
    """The benchmark as the landing page shows it: every seeded bug, and what became of it."""
    rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    rows = []
    for bug, meta in BUGS.items():
        found = sorted((f for f in r["buggy"]["findings"] if f["bug"] == bug), key=lambda f: rank[f["severity"]])
        row: dict[str, Any] = {"id": bug, "planted": meta["title"], "semantic": meta["semantic"], "caught": bool(found)}
        if found:
            row |= {"severity": found[0]["severity"], "reported": found[0]["title"], "recorded": found[0]["from_steps"], "kept": found[0]["repro_steps"]}
        rows.append(row)
    rows.sort(key=lambda row: (not row["caught"], rank.get(row.get("severity", "P3"), 3)))
    g, s = r["regression"], r["scaling"]
    return {
        "bugs": rows,
        "false_alarms": len(r["buggy"]["false_positive_findings"]),
        "on_fixed_build": len(r["fixed"]["confirmed"]),
        "scenarios": {"count": g["scenarios"], "fail_on_buggy": g["fail_on_buggy"], "pass_on_fixed": g["pass_on_fixed"], "model_requests": g["model_requests"]},
        "scaling": [{"testers": x["testers"], "steps_per_second": x["steps_per_second"], "speedup": x["speedup"]} for x in s],
    }


def report(r: dict[str, Any]) -> str:
    b, f, g = r["buggy"], r["fixed"], r["regression"]
    lines = [
        "# Benchmark results",
        "",
        f"Target: demo shop with {r['seeded']} seeded bugs ({r['detectable']} raise an error somewhere, {r['seeded'] - r['detectable']} are purely semantic), "
        f"plus traps that are correct behaviour. Simulated backend latency: {r['latency_ms']} ms per request. Brain: heuristic (no model calls).",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Detectable bugs found | {len([x for x in b['bugs_found']])} of {r['detectable']} (recall {b['recall_detectable']:.0%}) |",
        f"| All seeded bugs found | {len(b['bugs_found'])} of {r['seeded']} (recall {b['recall_all']:.0%}) |",
        f"| Missed | {', '.join(b['bugs_missed']) or 'none'} |",
        f"| Confirmed findings that are real seeded bugs | {b['true_positive_findings']} of {b['confirmed']} (precision {b['precision']:.0%}) |",
        f"| Candidates dropped by fresh-browser verification | {b['not_reproduced']} not reproduced, {b['flaky']} flaky |",
        f"| Confirmed findings on the fixed build | {len(f['confirmed'])} |",
        f"| Reproduction length | {b['repro_steps_before']} steps -> {b['repro_steps_after']} after locality + delta debugging |",
        f"| Hunt | {b['steps']} steps in {b['hunt_seconds']} s ({b['steps_per_second']} steps/s, 8 testers) |",
        f"| Triage | {b['verification_replays']} replays in {b['triage_seconds']} s |",
        f"| Exported scenarios failing on the buggy build | {g['fail_on_buggy']} of {g['scenarios']} |",
        f"| Exported scenarios passing on the fixed build | {g['pass_on_fixed']} of {g['scenarios']} ({g['steps_replayed_without_model']} steps replayed, {g['model_requests']} model requests) |",
        "",
        "## Findings on the buggy build",
        "",
        "| Severity | Finding | Seeded bug | Repro steps |",
        "|---|---|---|---|",
    ]
    lines += [f"| {x['severity']} | {x['title']} | {x['bug'] or 'NOT SEEDED'} | {x['from_steps']} -> {x['repro_steps']} |" for x in b["findings"]]
    if f["confirmed"]:
        lines += ["", "## False positives on the fixed build", ""] + [f"- {t}" for t in f["confirmed"]]
    if g["not_passing_on_fixed"]:
        lines += ["", "## Scenarios not passing on the fixed build", ""] + [f"- {t}" for t in g["not_passing_on_fixed"]]
    if "scaling" in r:
        lines += ["", "## Scaling (same frontier, desktop only, no verification)", "", "| Testers | Steps | Hunt seconds | Steps/s | Speedup |", "|---|---|---|---|---|"]
        lines += [f"| {x['testers']} | {x['steps']} | {x['hunt_seconds']} | {x['steps_per_second']} | {x['speedup']}x |" for x in r["scaling"]]
    lines += ["", f"Browser pool: {r['pool']}. Store: {r['store']}.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--latency-ms", type=int, default=25)
    args = ap.parse_args()
    result = asyncio.run(main(args.quick, args.latency_ms))
    (HERE / "results.json").write_text(json.dumps(result, indent=2))
    site = HERE.parent / "web" / "src" / "landing"
    if site.is_dir():  # the landing page shows the same run, bug by bug
        (site / "bench.json").write_text(json.dumps(ledger(result), indent=1) + "\n")
    text = report(result)
    (HERE / "RESULTS.md").write_text(text)
    print("\n" + text)
