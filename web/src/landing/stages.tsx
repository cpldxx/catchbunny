// The figures on the landing page. Each one is a piece of the product showing
// one idea, driven by the recorded run (see demo.ts) and built from the
// console's own parts where one exists.

import { CaretRightOutlined, LeftOutlined, PauseOutlined, ReloadOutlined, RightOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import type { Mission } from "../api";
import { Lanes } from "../components/Lanes";
import { relate, RouteTree, routesOf, tally } from "../components/RouteTree";
import { Catch, Pips, SeverityMark, Sighting, Stamp } from "../components/marks";
import { clock as clockText, ms, plural } from "../format";
import { Copy, Seg } from "../components/controls";
import type { Live } from "../stream";
import { demo, findingMarks, fired, huntShare, shot, useDemo, useOnScreen, useTimeline, verified, VERIFY_SPEED, type Demo, type Screen } from "./demo";

function Again({ onClick, label }: { onClick: () => void; label: string }) {
  return <button className="again" onClick={onClick}><ReloadOutlined />{label}</button>;
}

// ---------------------------------------------------------------- the run, in miniature

/** Six browsers at work, the findings they lead to, and a transport to move through the run. */
export function RunStage() {
  const ref = useRef<HTMLDivElement>(null);
  const run = useDemo(useOnScreen(ref, 0.15));
  const { live, clock } = run;
  const missions = useMemo(() => [...live.missions.values()].sort((a, b) => a.started_at - b.started_at), [live.version]);
  const findings = useMemo(() => [...live.findings.values()], [live.version]);
  const steps = useMemo(() => [...live.ticks.values()].reduce((n, t) => n + t.length, 0), [live.version]);
  const alarms = useMemo(() => [...live.ticks.values()].reduce((n, ticks) => n + ticks.filter((t) => t.signals.length).length, 0), [live.version]);
  const verifying = live.phases.some((p) => p.phase === "triage");

  return (
    <div className="stage-wrap">
      {/* A bunny behind the window. Its ears twitch each time the run catches one. */}
      <svg className="peek" key={findings.length} viewBox="0 0 64 56" width="64" height="56" aria-hidden="true">
        <ellipse cx="20" cy="30" rx="8.5" ry="25" transform="rotate(-8 20 30)" />
        <g className="twitch"><ellipse cx="44" cy="30" rx="8.5" ry="25" transform="rotate(15 44 30)" /></g>
      </svg>
    <div className="window stage" ref={ref}>
      <div className="window-bar">
        <Stamp status={run.run.status} />
        <strong>Deep scan</strong>
        <span className="faint ellipsis">the bundled demo shop</span>
        <span className="spacer" />
        <span className="tag num">{clockText(clock - run.run.started_at)}</span>
        <span className="tag num opt">{plural(steps, "step")}</span>
        <span className="tag num opt">{plural(alarms, "detector hit")}</span>
      </div>
      <div className="stage-grid">
        <div className="tiles">
          {Array.from({ length: run.run.spec.testers }, (_, i) => <Tile key={i} mission={missions[i]} live={live} clock={clock} />)}
        </div>
        <div className="feed">
          <div className="feed-head">Findings <span className="tag">{findings.length}</span></div>
          {findings.length ? (
            <ul>
              {findings.map((f) => (
                <li key={f.id}>
                  <SeverityMark level={f.severity} />
                  <span className="ellipsis">{f.title}</span>
                  <Pips {...f.verification} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="feed-empty">
              {verifying ? "Replaying each candidate in fresh browsers" : alarms ? `Detectors have fired ${alarms} times. Nothing is reported until it has been replayed` : "Starting six browsers"}
            </p>
          )}
        </div>
      </div>
      <Transport run={run} />
    </div>
    </div>
  );
}

function Tile({ mission, live, clock }: { mission?: Mission; live: Live; clock: number }) {
  if (!mission) return <figure className="tile idle"><div className="tile-bar"><i className="led" /><span className="faint">starting a browser</span></div><div className="tile-shot" /><figcaption>&nbsp;</figcaption></figure>;
  const ticks = live.ticks.get(mission.id) ?? [];
  const last = ticks.at(-1);
  const path = last?.url || mission.start_path || "/";
  const hit = !!last?.signals.length && clock - last.ts < 1.5;
  const finished = !!mission.finished_at;
  return (
    <figure className={`tile ${hit ? "hit" : ""}`}>
      <div className="tile-bar">
        <i className={`led ${finished ? "" : "running"}`} />
        <span className="ellipsis">{mission.persona}, {mission.kind}</span>
        <code className="faint ellipsis">{path}</code>
      </div>
      <div className="tile-shot">
        <img src={shot(mission.persona, path)} alt="" width={560} height={350} />
        {hit && <span className="tile-mark"><Sighting size={14} /></span>}
      </div>
      <figcaption className="ellipsis">{hit ? last!.signals[0].message : finished ? "Finished" : last?.action ?? "Opening the app"}</figcaption>
    </figure>
  );
}

function Transport({ run }: { run: Demo }) {
  const dragging = useRef(false);
  const to = (e: PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    run.seek((e.clientX - box.left) / box.width);
  };
  return (
    <div className="transport">
      <button className="play" onClick={run.toggle} aria-label={run.playing ? "Pause the run" : "Play the run"}>
        {run.playing ? <PauseOutlined /> : <CaretRightOutlined />}
      </button>
      <div
        className="scrub" role="slider" tabIndex={0} aria-label="Position in the recorded run" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(run.progress * 100)}
        onPointerDown={(e) => { dragging.current = true; e.currentTarget.setPointerCapture(e.pointerId); to(e); }}
        onPointerMove={(e) => { if (dragging.current) to(e); }}
        onPointerUp={() => { dragging.current = false; }}
        onKeyDown={(e) => { if (e.key === "ArrowRight") run.seek(run.progress + 0.02); if (e.key === "ArrowLeft") run.seek(run.progress - 0.02); }}
      >
        <span className="phase" style={{ left: 0 }}>Hunt</span>
        <span className="phase" style={{ left: `${huntShare * 100}%` }}>Verify in fresh browsers</span>
        <span className="track" />
        <span className="track done" style={{ width: `${run.progress * 100}%` }} />
        <span className="split" style={{ left: `${huntShare * 100}%` }} />
        {findingMarks.map((m, i) => <i key={i} className={`mark ${m <= run.progress ? "on" : ""}`} style={{ left: `${m * 100}%` }} />)}
        <span className="knob" style={{ left: `${run.progress * 100}%` }} />
      </div>
      <span className="faint opt">Recorded run. Verification plays {VERIFY_SPEED}x faster</span>
    </div>
  );
}

// ---------------------------------------------------------------- 1. map

const MAP_SCREENS: Screen[] = demo.map.screens.map((s) => ({ ...s, id: s.path }));
const MAP_ROUTES = routesOf(MAP_SCREENS);
const MAP_LINKS = relate(MAP_ROUTES, demo.map.links.map((l) => ({ from: l.from, to: l.to, label: l.via })));

/** The screens the testers reached, as a tree of routes. One screen is shown at a time: point at a route to see it and where it leads. */
export function MapStage() {
  const ref = useRef<HTMLDivElement>(null);
  const { t, replay } = useTimeline(3.2, useOnScreen(ref));
  const [selected, setSelected] = useState("/");
  const [page, setPage] = useState(0);
  const first = MAP_SCREENS[0].at, span = MAP_SCREENS.at(-1)!.at - first || 1;
  const reached = (s: Screen) => t >= ((s.at - first) / span) * 0.9;
  const route = MAP_ROUTES.find((r) => r.key === selected) ?? MAP_ROUTES[0];
  const pages = route.screens;
  const at = Math.min(page, pages.length - 1);
  const screen = pages[at];
  const sum = tally(pages);
  const leads = useMemo(() => new Set(MAP_LINKS.out.get(route.key)?.keys() ?? []), [route.key]);
  const choose = (key: string | null) => { if (key && key !== selected) { setSelected(key); setPage(0); } };
  const turn = (by: number) => setPage((at + by + pages.length) % pages.length);

  return (
    <div className="window" ref={ref}>
      <div className="window-bar">
        <strong>Product map</strong>
        <span className="faint">{plural(MAP_SCREENS.filter(reached).length, "screen")}</span>
        <span className="spacer" />
        <Again onClick={replay} label="Map it again" />
      </div>
      <div className="map-pane">
        <RouteTree routes={MAP_ROUTES} selected={route.key} onSelect={choose} leads={leads} shown={reached} compact hover />
        <div className="map-view">
          <div className="map-shot">
            {MAP_SCREENS.map((s) => <img key={s.path} className={s.path === screen.path ? "on" : ""} src={`/demo/shots/${s.shot}`} width={560} height={350} alt={s.path === screen.path ? `The ${s.title} screen of the demo shop` : ""} loading="lazy" />)}
          </div>
          <div className="map-name">
            <strong>{screen.title}</strong>
            <code className="faint ellipsis">{screen.path}</code>
            {pages.length > 1 && (
              <span className="pager">
                <button type="button" onClick={() => turn(-1)} aria-label="Previous page on this route"><LeftOutlined /></button>
                <span className="num">{at + 1} of {pages.length}</span>
                <button type="button" onClick={() => turn(1)} aria-label="Next page on this route"><RightOutlined /></button>
              </span>
            )}
          </div>
          <dl className="map-facts">
            <div><dt>Controls exercised</dt><dd>{sum.exercised} of {sum.controls}</dd></div>
            <div><dt>Visits</dt><dd>{sum.visits}</dd></div>
            <div><dt>Leads to</dt><dd>{plural(leads.size, "route")}</dd></div>
            <div><dt>First reached</dt><dd>{Math.min(...pages.map((s) => s.at)).toFixed(1)} s in</dd></div>
          </dl>
        </div>
      </div>
      <div className="window-foot">
        <span>Every screen was reached within {MAP_SCREENS.at(-1)!.at.toFixed(1)} seconds. Point at a route to see the screen and where it leads</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 2. hunt

const DETECTORS: [string, string][] = [
  ["http_error", "Server errors"], ["page_error", "Uncaught exceptions"], ["console_error", "Console errors"], ["dead_click", "Dead controls"],
  ["broken_image", "Broken images"], ["a11y", "Missing labels"], ["layout", "Overflow"],
];

/** Every tester's steps on one clock, in the console's own chart, and how often each detector fired. */
export function HuntStage() {
  const ref = useRef<HTMLDivElement>(null);
  const run = useDemo(useOnScreen(ref, 0.2));
  const [selected, setSelected] = useState<string>();
  return (
    <div className="window" ref={ref}>
      <Lanes bare live={run.live} run={run.run} clock={run.clock} selectedMission={selected} onMission={(id) => setSelected(id === selected ? undefined : id)} onFinding={() => {}} />
      <div className="detectors">
        {DETECTORS.map(([kind, label]) => (
          <span key={kind} className={fired[kind] ? "" : "none"}>
            {fired[kind] ? <Sighting size={10} /> : <i />}{label}<span className="tag num">{fired[kind] ?? 0}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 3. verify

/** Each candidate, replayed twice in fresh browsers, in the order the run confirmed them. */
export function VerifyStage() {
  const ref = useRef<HTMLDivElement>(null);
  const { t, replay } = useTimeline(5, useOnScreen(ref));
  const state = (at: number) => (t >= 0.08 + at * 0.9 ? "done" : t >= Math.max(0, at * 0.9 - 0.06) ? "busy" : "");
  const confirmed = verified.filter((f) => state(f.at) === "done").length;
  return (
    <div className="window" ref={ref}>
      <div className="window-bar">
        <strong>Verification</strong>
        <span className="faint opt">{plural(verified.length, "candidate")}</span>
        <span className="spacer" />
        <span className="tally"><b>{confirmed}</b> confirmed</span>
        <span className="tally"><b>0</b> dropped</span>
        <Again onClick={replay} label="Verify again" />
      </div>
      <ul className="verify">
        {verified.map((f) => {
          const s = state(f.at);
          return (
            <li key={f.id} className={s}>
              <SeverityMark level={f.severity} />
              <span className="ellipsis">{f.title}</span>
              <span className="tag opt">seen {f.occurrences}x</span>
              <span className="replays" aria-hidden="true">{[0, 1].map((n) => <i key={n} className={s === "done" ? "hit" : s === "busy" ? "run" : ""} />)}</span>
              <span className="state">{s === "done" ? "Confirmed" : s === "busy" ? "Replaying" : "Waiting"}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- 4. shorten

/** The path a tester actually took, and the part of it the bug needs. */
export function ShortenStage() {
  const ref = useRef<HTMLDivElement>(null);
  const visible = useOnScreen(ref);
  const { recorded, steps } = demo.finding;
  const [view, setView] = useState<"recorded" | "short">("recorded");
  const touched = useRef(false);
  useEffect(() => {
    if (!visible || touched.current) return;
    const id = setTimeout(() => { if (!touched.current) setView("short"); }, 1400);
    return () => clearTimeout(id);
  }, [visible]);
  let kept = 0;
  return (
    <div className="window" ref={ref}>
      <div className="window-bar">
        <strong>{demo.finding.title}</strong>
        <span className="spacer" />
        <Seg<"recorded" | "short"> label="Which path to show" value={view} onChange={(v) => { touched.current = true; setView(v); }}
          options={[{ value: "recorded", label: `Recorded, ${recorded.length} steps` }, { value: "short", label: `Shortened, ${steps.length} steps` }]} />
      </div>
      <ol className={`shorten ${view}`}>
        {recorded.map((s, i) => (
          <li key={i} className={s.kept ? "kept" : "cut"}>
            <span className="was">{i + 1}</span>
            <span className="ellipsis">{s.text}</span>
            <span className="now">{s.kept ? ++kept : ""}</span>
          </li>
        ))}
      </ol>
      <div className="window-foot"><span>Each removal is tested by replaying what is left, several candidates at a time</span></div>
    </div>
  );
}

// ---------------------------------------------------------------- 5. inspect

/** The recording of the replay that confirmed the finding, with its steps, requests and console lines on one clock. */
export function InspectStage() {
  const ref = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const visible = useOnScreen(ref, 0.4);
  const f = demo.finding;
  const [at, setAt] = useState(0); // ms on the replay's clock
  const [playing, setPlaying] = useState(false);
  const pct = (t: number) => `${Math.min(100, Math.max(0, (t / f.duration_ms) * 100))}%`;
  const bad = (status: number | string) => status === "failed" || (typeof status === "number" && status >= 400);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    v.playbackRate = 0.5; // the replay took under two seconds; half speed is slow enough to follow
    if (visible) v.play().catch(() => {}); else v.pause();
  }, [visible]);
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = () => {
      if (video.current) setAt(video.current.currentTime * 1000 - f.video_offset_ms);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, f.video_offset_ms]);

  const seek = (t: number) => {
    setAt(t);
    if (video.current) video.current.currentTime = (t + f.video_offset_ms) / 1000;
  };
  const current = f.timeline.reduce((best, s, i) => (s.t_ms <= at ? i : best), -1);
  const firedYet = at >= f.fired_at_ms;

  return (
    <div className="window inspect" ref={ref}>
      <div className="window-bar">
        <SeverityMark level={f.severity} />
        <strong className="ellipsis">{f.title}</strong>
        <span className="spacer" />
        <Pips {...f.verification} />
        <span className="faint opt">reproduced in {f.verification.reproduced} of {plural(f.verification.runs, "fresh browser")}</span>
      </div>
      <div className="inspect-grid">
        <div>
          <div className="player">
            <video ref={video} src="/demo/replay.webm" width={1366} height={850} muted loop playsInline preload="metadata" aria-label="Recording of the replay that confirmed this finding" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
            <button className="play" onClick={() => (video.current?.paused ? video.current.play() : video.current?.pause())} aria-label={playing ? "Pause the recording" : "Play the recording"}>
              {playing ? <PauseOutlined /> : <CaretRightOutlined />}
            </button>
            {firedYet && <span className="verdict"><Catch size={16} />the defect fired here</span>}
          </div>
          <div className="reel" role="slider" tabIndex={0} aria-label="Replay timeline" aria-valuemin={0} aria-valuemax={f.duration_ms} aria-valuenow={Math.round(at)}
            onClick={(e) => { const box = e.currentTarget.getBoundingClientRect(); seek(((e.clientX - box.left) / box.width) * f.duration_ms); }}
            onKeyDown={(e) => { if (e.key === "ArrowRight") seek(Math.min(f.duration_ms, at + 100)); if (e.key === "ArrowLeft") seek(Math.max(0, at - 100)); }}>
            <span className="lbl" style={{ top: 4 }}>steps</span>
            <span className="rowline" style={{ top: 24 }} />
            <span className="lbl" style={{ top: 28 }}>requests</span>
            <span className="rowline" style={{ top: 48 }} />
            <span className="lbl" style={{ top: 52 }}>console</span>
            {f.timeline.map((s) => <i key={s.index} className={`blk ${s.ok ? "" : "bad"}`} style={{ left: pct(s.t_ms), width: pct(s.ms) }} />)}
            {f.network.map((n, i) => <i key={i} className={`req ${bad(n.status) ? "bad" : ""}`} style={{ left: pct(n.t_ms) }} />)}
            {f.console.map((c, i) => <i key={i} className="con bad" style={{ left: pct(c.t_ms) }} />)}
            <span className="fired" style={{ left: pct(f.fired_at_ms) }}><Catch size={16} /></span>
            <span className="head" style={{ left: pct(at) }} />
          </div>
          <p className="caption">Playing at half speed. Click the reel or a step to jump there</p>
        </div>
        <div>
          <ol className="steps sync">
            {f.timeline.map((s, i) => (
              <li key={s.index} className={i === current ? "now" : i < current ? "past" : ""}>
                <button type="button" onClick={() => seek(s.t_ms)} aria-current={i === current ? "step" : undefined}>
                  <span>{s.action}</span>
                  <span className="tag num">{ms(s.t_ms)}</span>
                </button>
              </li>
            ))}
          </ol>
          <div className={`log ${firedYet ? "" : "pending"}`}>
            <table><tbody>
              {f.network.slice(-2).map((n, i) => (
                <tr key={i} className={bad(n.status) ? "bad" : ""}>
                  <td className="t">{ms(n.t_ms)}</td><td className="s">{n.status}</td><td className="url one"><code>{n.method} {n.url}</code></td>
                </tr>
              ))}
            </tbody></table>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 6. hand off

const TOOLS: { id: keyof typeof demo.agent; call: string; says: string }[] = [
  { id: "scan", call: "scan(url)", says: "Did I break anything?" },
  { id: "get_finding", call: "get_finding(finding_id)", says: "What exactly is wrong?" },
  { id: "verify_fix", call: "verify_fix(finding_id)", says: "Is it gone?" },
  { id: "keep_as_scenario", call: "keep_as_scenario(finding_id)", says: "Keep it from coming back" },
];

/** How an agent is connected. MCP comes first because it is what all the others are: the protocol, then who speaks it. */
const AGENTS = [
  { name: "MCP", run: "catchbunny serve", note: "Any MCP client can use the endpoint it serves at /mcp" },
  { name: "Claude Code", run: "catchbunny connect claude-code", note: "Writes .mcp.json in the project you are in" },
  { name: "Codex", run: "catchbunny connect codex", note: "Adds one table to ~/.codex/config.toml" },
  { name: "Claude", run: "catchbunny serve --team --public-url https://qa.yourcompany.com", note: "Add its /mcp address as a custom connector, then sign in" },
  { name: "ChatGPT", run: "catchbunny serve --team --public-url https://qa.yourcompany.com", note: "Add its /mcp address as an MCP server, then sign in" },
];

/** What a coding agent sends and what it gets back, for this run. */
export function AgentStage() {
  const ref = useRef<HTMLDivElement>(null);
  const visible = useOnScreen(ref);
  const [i, setI] = useState(0);
  const touched = useRef(false);
  useEffect(() => {
    if (!visible || touched.current) return;
    const id = setInterval(() => { if (!touched.current) setI((n) => (n + 1) % TOOLS.length); }, 4200);
    return () => clearInterval(id);
  }, [visible]);
  const [agent, setAgent] = useState(AGENTS[0]);
  const tool = TOOLS[i];
  const result = tool.id === "keep_as_scenario"
    ? `${JSON.stringify(demo.agent.keep_as_scenario, null, 2).replace(/\n}$/, ',\n  "yaml":')}\n${demo.scenario.split("\nlearned:")[0].split("\n").map((l) => "    " + l).join("\n")}\n    # recorded actions follow, so these steps replay without a model\n}`
    : JSON.stringify(demo.agent[tool.id], null, 2);
  return (
    <div className="window agent" ref={ref}>
      <div className="window-bar">
        <strong>MCP server</strong>
        <code className="faint ellipsis opt" translate="no">http://127.0.0.1:8700/mcp</code>
        <span className="spacer" />
        <span className="tag">7 tools</span>
      </div>
      <div className="connect">
        <Seg label="Connect it to" options={AGENTS.map((a) => ({ value: a.name, label: a.name }))} value={agent.name} onChange={(name) => setAgent(AGENTS.find((a) => a.name === name)!)} />
        <div className="connect-run">
          <span className="faint" aria-hidden="true">$</span>
          <code translate="no">{agent.run}</code>
          <span className="spacer" />
          <Copy text={agent.run} />
        </div>
        <p className="connect-note">{agent.note}</p>
      </div>
      <div className="agent-grid">
        <ol className="tools">
          {TOOLS.map((t, n) => (
            <li key={t.id}>
              <button className={n === i ? "on" : ""} onClick={() => { touched.current = true; setI(n); }} aria-current={n === i}>
                <span className="says">{t.says}</span>
                <code>{t.call}</code>
              </button>
            </li>
          ))}
        </ol>
        <div className="reply">
          <div className="reply-head"><code>{tool.call}</code><span className="spacer" /><Copy text={result} /></div>
          <pre className="code" key={tool.id}>{result}</pre>
        </div>
      </div>
    </div>
  );
}
