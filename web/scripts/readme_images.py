"""Redraw the pictures in README.md from one real run.

    uv run catchbunny start          # then, in the console: Scan the demo shop
    uv run python web/scripts/readme_images.py

Writes docs/assets/: pipeline.png and how-it-runs.png, drawn from diagrams.html
with that run's numbers, and run.png and finding.png, taken from the console.
All four are twice their shown size, for sharp screens.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import urllib.request
from pathlib import Path

from playwright.async_api import Browser, async_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "assets"
FONTS = {  # the faces the site is built with; diagrams.html names them and has none of its own
    "Atkinson Hyperlegible Next": "atkinson-hyperlegible-next-latin-wght-normal-*.woff2",
    "Recursive Mono": "recursive-latin-mono-normal-*.woff2",
    "Recursive Casual": "recursive-latin-casl-normal-*.woff2",
}

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://127.0.0.1:8700")
ap.add_argument("--run", help="a finished deep scan (default: the latest one on the server)")
ap.add_argument("--finding", default="api/orders", help="text in the title of the finding to show")
args = ap.parse_args()


def get(path: str):
    return json.load(urllib.request.urlopen(args.api + path, timeout=30))


def latest_run() -> str:
    runs = [r for p in get("/v1/projects") for r in get(f"/v1/projects/{p['id']}/runs")]
    done = [r for r in runs if r["spec"]["kind"] == "deep_scan" and r["phase"] == "finished"]
    if not done:
        raise SystemExit("No finished deep scan on this server. In the console, choose Scan the demo shop, then run this again.")
    return max(done, key=lambda r: r["created_at"])["id"]


def font_css() -> str:
    faces = []
    for family, pattern in FONTS.items():
        for file in sorted((ROOT / "web" / "dist" / "assets").glob(pattern))[:1]:
            data = base64.b64encode(file.read_bytes()).decode()
            faces.append(f'@font-face {{ font-family: "{family}"; font-weight: 200 1000; src: url("data:font/woff2;base64,{data}") format("woff2"); }}')
    return "\n".join(faces)


async def diagrams(browser: Browser, run: dict, finding: dict) -> None:
    found, summary = run["summary"]["findings"], run["summary"]
    numbers = {
        "screens": summary["coverage"]["screens"],
        "steps": summary["budget"]["steps"],
        "seconds": round(summary["budget"]["seconds"]),
        "candidates": found["candidates"],
        "confirmed": found["confirmed"],
        "before": found["repro_steps_before"],
        "after": found["repro_steps_after"],
        "blocking": len(summary["blocking_findings"]),
        "gate": summary["gate"],
        "taken": finding["original_steps"],
        "needed": len(finding["repro"]),
        "severity": finding["severity"],
        "title": finding["title"],
    }
    page = await browser.new_page(viewport={"width": 960, "height": 900}, device_scale_factor=2)
    await page.goto((OUT / "diagrams.html").as_uri())
    await page.add_style_tag(content=font_css())
    await page.evaluate("document.fonts.ready")
    await page.evaluate("numbers => draw(numbers)", numbers)
    for name in ("pipeline", "how-it-runs"):
        await page.locator(f"#{name}").screenshot(path=OUT / f"{name}.png", omit_background=True)
    await page.close()


async def console(browser: Browser, run: dict, finding: dict) -> None:
    page = await browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
    await page.goto(f"{args.api}/app/runs/{run['id']}", wait_until="networkidle")
    await page.evaluate("document.fonts.ready")
    await page.wait_for_selector("canvas")
    await page.wait_for_timeout(800)  # the lanes draw on the frame after their data arrives
    below = await page.locator(".run-body").bounding_box()  # the findings: the next picture
    await page.screenshot(path=OUT / "run.png", clip={"x": 0, "y": 0, "width": 1280, "height": below["y"] - 8})
    await page.close()

    # The findings and the chosen one, whole: a page tall enough that nothing scrolls under the top bar.
    page = await browser.new_page(viewport={"width": 1440, "height": 2400}, device_scale_factor=2)
    await page.goto(f"{args.api}/app/runs/{run['id']}?finding={finding['id']}", wait_until="networkidle")
    video = page.locator("video").first
    await video.wait_for()
    await video.evaluate(
        """(v, at) => new Promise(done => { v.controls = false; v.addEventListener("seeked", () => done(), { once: true }); v.currentTime = at; })""",
        max(0.0, (finding["evidence"].get("fired_at_ms") or 0) / 1000),
    )
    await page.wait_for_timeout(400)
    box = await page.locator(".run-body").bounding_box()
    await page.screenshot(path=OUT / "finding.png", clip={"x": box["x"] - 16, "y": box["y"] - 16, "width": box["width"] + 32, "height": box["height"] + 32})
    await page.close()


async def main() -> None:
    run = get(f"/v1/runs/{args.run or latest_run()}")
    findings = get(f"/v1/runs/{run['id']}/findings")
    match = next((f for f in findings if args.finding in f["title"]), None)
    if match is None:
        raise SystemExit(f"No finding in {run['id']} has {args.finding!r} in its title.")
    finding = get(f"/v1/findings/{match['id']}")
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        await diagrams(browser, run, finding)
        await console(browser, run, finding)
        await browser.close()
    for name in ("pipeline", "how-it-runs", "run", "finding"):
        print(f"docs/assets/{name}.png  {(OUT / f'{name}.png').stat().st_size // 1024} kB")


asyncio.run(main())
