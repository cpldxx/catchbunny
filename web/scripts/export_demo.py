"""Regenerate what the landing page shows from one real run.

    uv run python bench/buggy_shop.py --port 8765          # the demo shop, all bugs on
    uv run catchbunny serve                                   # the API
    # start a deep scan of http://127.0.0.1:8765 from the console, then:
    uv run python web/scripts/export_demo.py --run <run-id>

Writes web/src/landing/demo-run.json (the run's event log, its map, one finding
with the recorded path behind it, and what the MCP tools return for it) and
web/public/demo/ (a screenshot of every page each persona reached, and the
recording of the replay that confirmed the finding). Host names are removed.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import shutil
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))
from buggy_shop import PASSWORD, USER  # noqa: E402  the demo shop's own test account
from playwright.async_api import async_playwright  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--run", required=True)
ap.add_argument("--api", default="http://127.0.0.1:8700")
ap.add_argument("--shop", default="http://127.0.0.1:8765")
ap.add_argument("--data-dir", default=str(ROOT / ".catchbunny-dev"))
ap.add_argument("--finding", default="api/orders", help="text in the title of the finding to feature")
args = ap.parse_args()

OUT = ROOT / "web" / "public" / "demo"
DOC = ROOT / "web" / "src" / "landing" / "demo-run.json"
shutil.rmtree(OUT / "shots", ignore_errors=True)
(OUT / "shots").mkdir(parents=True, exist_ok=True)


def get(path: str):
    return json.load(urllib.request.urlopen(args.api + path, timeout=60))


def post(path: str):
    return json.load(urllib.request.urlopen(urllib.request.Request(args.api + path, method="POST"), timeout=180))


def path_of(url: str) -> str:
    s = urlsplit(url)
    return (s.path or "/") + (f"?{s.query}" if s.query else "")


def slug(path: str) -> str:
    return path.strip("/").replace("/", "-") or "home"


host = urlsplit(args.shop).netloc
run = get(f"/v1/runs/{args.run}")
assert run["status"] not in ("queued", "running"), "the run has not finished"

# ---------------------------------------------------------------- the event log, trimmed to what the page draws
events = []
with urllib.request.urlopen(f"{args.api}/v1/runs/{args.run}/events", timeout=120) as stream:
    seq, kind = 0, ""
    for raw in stream:
        line = raw.decode().rstrip("\n")
        if line.startswith("id:"):
            seq = int(line[3:])
        elif line.startswith("event:"):
            kind = line[6:].strip()
        elif line.startswith("data:"):
            d = json.loads(line[5:])
            ts = d.pop("ts")
            if kind == "run.queued":
                continue
            if kind == "run.phase":
                data = {"phase": d["phase"], "budget": d.get("budget")}
            elif kind in ("mission.started", "mission.finished"):
                m = d["mission"]
                data = {"mission": {k: m[k] for k in ("id", "kind", "title", "charter", "persona", "device", "brain", "status", "verdict", "summary", "steps_taken", "signals", "started_at", "finished_at", "scenario_id", "run_id", "start_path", "max_steps")}}
            elif kind == "step":
                data = {"mission_id": d["mission_id"], "index": d["index"], "action": d["action"], "ok": d["ok"], "error": d["error"], "url": path_of(d["url"]), "ms": d["ms"], "screenshot": "",
                        "signals": [{"kind": s["kind"], "message": s["message"].replace(f"http://{host}", "")} for s in d.get("signals", [])]}
            elif kind == "finding":
                f = d["finding"]
                data = {"finding": {**{k: f[k] for k in ("id", "run_id", "project_id", "kind", "severity", "title", "expected", "actual", "persona", "device", "status", "verification", "original_steps", "minimized", "occurrences", "created_at")},
                                    "url": path_of(f["url"]), "repro": [{"kind": a["kind"]} for a in f["repro"]], "missions": f["missions"], "check": None, "evidence": {}}}
            elif kind == "run.finished":
                data = {"run": None}
            else:
                data = d
            events.append({"seq": seq, "type": kind, "ts": round(ts, 3), "data": data})
            if kind == "run.finished":
                break

s = run["summary"]
meta = {"id": run["id"], "project_id": run["project_id"], "spec": run["spec"], "status": run["status"], "phase": run["phase"], "created_at": run["created_at"], "started_at": run["started_at"], "finished_at": run["finished_at"], "error": "",
        "summary": {k: s[k] for k in ("verdict", "gate", "blocking_findings", "missions", "brains", "model", "findings", "coverage", "budget", "stop_reason", "hunt_seconds", "triage_seconds", "steps_per_second", "verification_replays") if k in s}}
t0, pid = run["started_at"], run["project_id"]
missions = {e["data"]["mission"]["id"]: e["data"]["mission"] for e in events if e["type"] == "mission.started"}

# ---------------------------------------------------------------- a screenshot of every page each persona reached
seen: dict[str, list[str]] = collections.OrderedDict()
first: dict[str, float] = {}
for e in events:
    if e["type"] == "step":
        path = urlsplit(e["data"]["url"]).path
        first.setdefault(path, e["ts"])
        pages = seen.setdefault(missions[e["data"]["mission_id"]]["persona"], [])
        if path not in pages:
            pages.append(path)


async def shots() -> dict[str, str]:
    index: dict[str, str] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for persona, paths in seen.items():
            ctx = await browser.new_context(viewport={"width": 1120, "height": 700}, device_scale_factor=0.5)
            page = await ctx.new_page()
            if persona != "visitor":
                await page.goto(args.shop + "/login")
                await page.fill('input[name="username"]', USER)
                await page.fill('input[name="password"]', PASSWORD)
                await page.click('button:has-text("Sign in")')
                await page.wait_for_load_state("networkidle")
            for path in paths:
                try:
                    await page.goto(args.shop + path, wait_until="networkidle")
                except Exception:
                    continue
                name = f"{persona}-{slug(path)}.jpg"
                await page.screenshot(path=str(OUT / "shots" / name), type="jpeg", quality=70)
                index[f"{persona}:{path}"] = name
            await ctx.close()
        await browser.close()
    return index


index = asyncio.run(shots())

# ---------------------------------------------------------------- the product map, in the order the screens were first reached
m = get(f"/v1/projects/{pid}/map")
by_path: dict[str, dict] = {}
for st in m["states"]:
    path = urlsplit(st["url"]).path
    cur = by_path.get(path)
    if cur is None or st["visits"] > cur["visits"]:
        by_path[path] = {"id": st["id"], "path": path, "title": st["title"].split(" - ")[0], "controls": st["controls_total"], "exercised": st["controls_done"], "visits": st["visits"]}
state_path = {st["id"]: urlsplit(st["url"]).path for st in m["states"]}
links: dict[tuple[str, str], str] = collections.OrderedDict()
for src, outs in m["edges"].items():
    for label, dst in outs.items():
        a, b = state_path.get(src), state_path.get(dst)
        if a and b and a != b and label.startswith("click"):
            links.setdefault((a, b), label.split("|")[1] if "|" in label else "")
screens = sorted((sc for sc in by_path.values() if sc["path"] in first), key=lambda sc: first[sc["path"]])
for sc in screens:
    sc["at"] = round(first[sc["path"]] - t0, 2)
    sc["shot"] = index.get(f"visitor:{sc['path']}") or index.get(f"member:{sc['path']}", "")

# ---------------------------------------------------------------- one finding: its evidence, and the recorded path before it was shortened
findings = get(f"/v1/runs/{args.run}/findings")
chosen = next(x for x in findings if args.finding in x["title"] and x["status"] == "confirmed")
f = get(f"/v1/findings/{chosen['id']}")
ev = f["evidence"]
step_at, signature = ev["signal"]["step"], ev["signal"]["signature"]
witness = None
for mid in f["missions"]:
    steps = get(f"/v1/missions/{mid}/steps")
    if len(steps) > step_at and any(sig["signature"] == signature for sig in steps[step_at]["signals"]):
        witness = mid
        break
assert witness, "no mission's trace fires the finding at the recorded step"
recorded = [e["data"]["action"] for e in events if e["type"] == "step" and e["data"]["mission_id"] == witness and e["data"]["index"] <= step_at]
kept, need = [], list(f["repro_text"])
for text in reversed(recorded):  # the shortest reproduction is a subsequence; match from the end, where the defect is
    if need and text == need[-1]:
        kept.append(True)
        need.pop()
    else:
        kept.append(False)
kept.reverse()
finding = {"id": f["id"], "severity": f["severity"], "kind": f["kind"], "title": f["title"], "expected": f["expected"], "actual": f["actual"], "persona": f["persona"], "device": f["device"], "url": path_of(f["url"]),
           "verification": f["verification"], "occurrences": f["occurrences"], "testers": len(f["missions"]), "original_steps": f["original_steps"], "steps": f["repro_text"],
           "recorded": [{"text": t, "kept": k} for t, k in zip(recorded, kept)],
           "timeline": ev["timeline"], "network": ev["network"], "console": ev["console"], "fired_at_ms": ev["fired_at_ms"], "duration_ms": ev["duration_ms"], "video_offset_ms": ev.get("video_offset_ms", 0)}
shutil.copyfile(Path(args.data_dir) / "artifacts" / ev["video"], OUT / "replay.webm")
scenario = urllib.request.urlopen(f"{args.api}/v1/findings/{f['id']}/export?format=scenario").read().decode()

# ---------------------------------------------------------------- what the MCP tools return for this run (the fields mcp_server.py builds)
def brief(x: dict) -> dict:
    return {"id": x["id"], "severity": x["severity"], "status": x["status"], "title": x["title"], "url": path_of(x["url"]), "steps": len(x["repro"])}


again = post(f"/v1/findings/{f['id']}/rerun?runs=2")
name = "".join(c if c.isalnum() else "-" for c in f["title"].lower()).strip("-")[:60]
agent = {
    "scan": {"status": run["status"], "run_id": run["id"], "coverage": {k: s["coverage"][k] for k in ("screens", "controls_found", "controls_exercised")}, "steps": s["budget"]["steps"], "findings": [brief(x) for x in findings[:3]], "more": len(findings) - 3},
    "get_finding": {**brief(f), "expected": f["expected"], "actual": f["actual"], "reproduced": f"{f['verification']['reproduced']}/{f['verification']['runs']} fresh-browser replays", "steps_to_reproduce": f["repro_text"]},
    "verify_fix": {**again, "verdict": "still broken" if again["still_present"] else "fixed: the reproduction no longer triggers the defect"},
    "keep_as_scenario": {"suggested_path": f"qa/{name}.yaml"},
}

doc = {"run": meta, "events": events, "finding": finding, "scenario": scenario, "map": {"screens": screens, "links": [{"from": a, "to": b, "via": v} for (a, b), v in links.items() if a in first and b in first]}, "agent": agent, "shots": index}
text = json.dumps(doc, separators=(",", ":"))
assert host not in text, "a host name survived in the export"
DOC.write_text(text)
print(f"run {run['id']}: {sum(e['type'] == 'step' for e in events)} steps, {len(findings)} findings, {len(screens)} screens, {len(index)} screenshots")
print(f"finding: {finding['title']} | recorded {len(recorded)} -> {sum(kept)} kept (unmatched: {need}) | verify_fix: {agent['verify_fix']['verdict']}")
print(f"phases: {[(e['data']['phase'], round(e['ts'] - t0, 1)) for e in events if e['type'] == 'run.phase']} | {round(run['finished_at'] - t0, 1)} s | {len(text)} bytes")
