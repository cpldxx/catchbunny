// The landing page. It explains the product by showing it: a recorded run you
// can play and scrub, then one figure per stage of a run, each built from that
// run's own data. Every number on the page comes from a file the product wrote
// (demo-run.json, bench.json), and the ones that matter are said loudly.

import { useEffect, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { Catch } from "../components/marks";
import { consoleDoor, useInstall } from "../install";
import { Code, Command, SiteFoot, SiteNav } from "../site/Site";
import ledger from "./bench.json";
import measured from "./models.json";
import { demo, DemoProvider, totals } from "./demo";
import { Count, Key, Pen, Stat } from "./emphasis";
import { AgentStage, HuntStage, InspectStage, MapStage, RunStage, ShortenStage, VerifyStage } from "./stages";
import "./landing.css";

interface Bug { id: string; planted: string; semantic: boolean; caught: boolean; severity?: string; reported?: string; recorded?: number; kept?: number }
const bench = ledger as { bugs: Bug[]; false_alarms: number; on_fixed_build: number; scenarios: { count: number; fail_on_buggy: number; pass_on_fixed: number; model_requests: number }; scaling: { testers: number; steps_per_second: number; speedup: number }[] };
// The benchmark the page leads with is the one that needs nothing: no model, no key. Its subject is the
// planted bugs that raise an error somewhere. The shop also has bugs where nothing errors; those are what
// a model-driven tester is for, and they are counted under "Who drives the testers" and in the docs.
const loud = bench.bugs.filter((b) => !b.semantic);
const caught = loud.filter((b) => b.caught).length;
const quiet = bench.bugs.length - loud.length;
const fastest = bench.scaling.at(-1)!;
const models = measured as { ollama: { model: string; runs: number; found: Record<string, number> } | null; claude: { model: string; found: number; usd: number; requests: number } | null };
/** "claude-opus-5-5" -> "Claude Opus 5.5": the file keeps the id, the page says the name. */
const modelName = (id: string) => id.replace(/^claude-/, "").split("-").reduce((out, part) => (/^\d+$/.test(part) && /\d$/.test(out) ? `${out}.${part}` : `${out} ${part[0].toUpperCase()}${part.slice(1)}`), "Claude");
const finding = demo.finding;
const WORDS = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
const word = (n: number) => WORDS[n] ?? String(n); // the headline follows the benchmark file, not the other way round

// What a tester gets to. Each line has a test in the suite that fails if it stops being true.
const REACH = [
  "Chromium and WebKit, the engine Safari is built on",
  "Controls inside the app’s own iframes",
  "Shadow roots, open or closed",
  "File uploads, with hostile names and oversized files",
  "Drag and drop onto whatever takes drops",
  "Your app’s own reset, called before every replay",
  "Phone and tablet viewports, with touch",
  "A signed-in account beside a signed-out visitor",
];

export function LandingPage() {
  const { hash } = useLocation();
  const door = consoleDoor(useInstall()); // the console is offered only where there is one: behind an install, not on a static copy of the site
  useEffect(() => {
    // A link to a section can arrive before the section exists, or from another page; go there once it does.
    const id = hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView();
  }, [hash]);

  return (
    <DemoProvider>
      <div className="site">
        <SiteNav />
        <main id="main">
          <section className="hero">
            <div className="frame">
              <div className="hero-text">
                <h1 className="display">Browser testers that prove a bug before they report it</h1>
                <p className="sub">Point Catchbunny at a running web app. What you see below is a real run of six testers: play it, pause it, drag through it.</p>
                <div className="hero-act">
                  <div className="actions">
                    <Link to="/docs/quick-start" className="btn primary large">Run your first scan</Link>
                    {door ? <Link to={door.to} className="btn large">{door.label}</Link> : <Link to={{ pathname: "/", hash: "#agents" }} className="btn large">Connect your agent</Link>}
                  </div>
                  <Command text="curl -LsSf https://raw.githubusercontent.com/cpldxx/catchbunny/main/install.sh | sh" />
                </div>
              </div>
              <RunStage />
            </div>
          </section>

          <div id="how">
            <Step n={1} title="Map" figure={<MapStage />}
              text={<>Testers share <Key>one map</Key> of your app, so no control is tried twice.</>}
              stat={<Stat label={<>screens found, all of them within {totals.mappedIn.toFixed(1)} seconds</>}><Pen><Count to={totals.screens} /></Pen></Stat>} />
            <Step n={2} flip title="Hunt" figure={<HuntStage />}
              text={<>Explorers click everything, fuzzers fill <Key>every form</Key>, and detectors watch each step.</>}
              stat={<Stat label={<>steps by {totals.testers} testers. A detector fired on {totals.sightings} of them</>}><Pen><Count to={totals.steps} /></Pen></Stat>} />
            <Step n={3} title="Verify" figure={<VerifyStage />}
              text={<>Every sighting is replayed in <Key>fresh browsers</Key>. What does not reproduce is never shown to you.</>}
              stat={<Stat label={<>sightings became findings. Each one replayed twice, in a browser that had never seen the app</>}><Pen><Count to={totals.confirmed} /></Pen></Stat>} />
            <Step n={4} flip title="Shorten" figure={<ShortenStage />}
              text={<>Steps the bug does not need are removed, until <Key>only the ones that matter</Key> are left.</>}
              stat={<Stat label={<>steps reproduce this bug. The tester that found it took {finding.recorded.length}</>}><s>{finding.recorded.length}</s><Pen><Count to={finding.steps.length} /></Pen></Stat>} />
            <Step n={5} title="Inspect" figure={<InspectStage />}
              text={<>Watch the replay that confirmed it, with every step and request on <Key>one clock</Key>.</>}
              stat={<Stat label={<>seconds of recording, with the moment the defect fired marked on it</>}><Pen><Count to={finding.duration_ms / 1000} digits={1} /></Pen></Stat>} />
            <Step n={6} flip id="agents" title="Hand off" figure={<AgentStage />}
              text={<>Catchbunny is an <Key>MCP server</Key>. Connect it to Claude Code, Codex, Claude or ChatGPT, and the agent that wrote the change scans it, reads the finding, fixes it, and asks whether it is gone.</>}
              stat={<Stat label={<>tools over MCP. One command connects an agent, and no call waits longer than 45 seconds</>}><Pen><Count to={7} /></Pen></Stat>} />
          </div>

          <section className="band" id="bench">
            <div className="frame">
              <div className="band-head">
                <h2 className="display"><span className="line">{word(loud.length)} planted bugs.</span> {caught === loud.length ? "All" : word(caught)} caught, <Pen>with no model.</Pen></h2>
                <p className="sub">The bundled benchmark: a shop with {word(loud.length).toLowerCase()} bugs planted that raise an error somewhere, among traps that only look like bugs. No model, no API key, nothing sent anywhere. A bug counts only if it replayed in a fresh browser.</p>
              </div>

              <dl className="score">
                <div className="lead">
                  <dd><Count to={caught} /><small>of {loud.length}</small></dd>
                  <dt>planted bugs caught and replayed, with no model and no API key</dt>
                </div>
                <div>
                  <dd><Count to={bench.on_fixed_build} /></dd>
                  <dt>findings on the fixed build, which is full of traps that look like bugs</dt>
                </div>
                <div>
                  <dd><Count to={bench.scenarios.fail_on_buggy} /><small>of {bench.scenarios.count}</small></dd>
                  <dt>regression tests fail on the buggy build and pass on the fixed one</dt>
                </div>
                <div>
                  <dd><Count to={fastest.speedup} digits={1} suffix="×" /></dd>
                  <dt>faster with {fastest.testers} testers than with one</dt>
                </div>
              </dl>

              <div className="ledger" role="table" aria-label="The benchmark, bug by bug">
                <div className="ledger-head" role="row">
                  <span role="columnheader">Planted in the shop</span>
                  <span role="columnheader">What Catchbunny reported</span>
                  <span role="columnheader" className="steps-col">Steps to reproduce</span>
                </div>
                {loud.map((b) => (
                  <div key={b.id} role="row" className={b.caught ? "" : "missed"}>
                    <span role="cell" className="planted">
                      <Catch size={20} hollow={!b.caught} title={b.caught ? "Caught" : "Missed"} />
                      {b.planted}
                    </span>
                    <span role="cell" className="reported">
                      {b.caught ? <><span className="tag dark">{b.severity}</span><span>{b.reported}</span></> : <span>Missed in this run</span>}
                    </span>
                    <span role="cell" className="steps-col num">
                      {b.caught && (b.recorded === b.kept ? <b>{b.kept}</b> : <><s>{b.recorded}</s><b>{b.kept}</b></>)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section className="band" id="models">
            <div className="frame">
              <div className="band-head">
                <h2 className="display"><span className="line">It needs <Pen>no model.</Pen></span> It can use one.</h2>
                <p className="sub">Detectors, fuzzing and replay find every bug that raises an error. A model adds testers that understand what a page is for, and find the bugs where nothing errors. What they report is replayed like everything else: a claim that does not reproduce is never shown.</p>
              </div>
              <div className="drivers">
                <article>
                  <h3 className="display">No model</h3>
                  <p>Exploration, fuzzing and the detectors. Nothing leaves your machine.</p>
                  <p className="got"><b><Count to={caught} /> of {loud.length}</b><span>planted bugs that raise an error, caught</span></p>
                </article>
                <article>
                  <h3 className="display">Ollama</h3>
                  <p>Goal-directed testers on a model you run yourself.</p>
                  {models.ollama && <p className="got"><b><Count to={models.ollama.found.stale_badge} /> of {models.ollama.runs}</b><span>runs of {models.ollama.model} found the stale cart counter, a bug where nothing errors</span></p>}
                </article>
                <article>
                  <h3 className="display">Claude</h3>
                  <p>Plans what to probe from the map, then probes it. Your key, your bill, and a cap on it.</p>
                  {models.claude
                    ? <p className="got"><b><Count to={models.claude.found} /> of {quiet}</b><span>planted bugs where nothing errors, proven by replay. {modelName(models.claude.model)}, one run, ${models.claude.usd.toFixed(2)}</span></p>
                    : <p className="got"><b>$<Count to={2} /></b><span>is the most a run spends unless you raise it. The run stops there</span></p>}
                </article>
              </div>
            </div>
          </section>

          <section className="band" id="team">
            <div className="frame">
              <div className="band-head">
                <h2 className="display">On your machine, or on <Pen>your team’s domain</Pen></h2>
                <p className="sub">It is the same server either way: the console, the API and the MCP endpoint on one port.</p>
              </div>
              <div className="ways">
                <article>
                  <h3 className="display">Alone</h3>
                  <Command text="catchbunny start" />
                  <ul>
                    <li><b>0</b><span>accounts. It answers only to localhost, so whoever reaches it is you</span></li>
                    <li><b>1</b><span>port for the console at /app and the MCP endpoint at /mcp</span></li>
                  </ul>
                </article>
                <article>
                  <h3 className="display">With a team</h3>
                  <Command text="catchbunny start --team --public-url https://qa.yourcompany.com" />
                  <ul>
                    <li><b>1</b><span>team code. A colleague creates their own account with it</span></li>
                    <li><b>1</b><span>key per person for a coding agent or CI, revoked on its own</span></li>
                    <li><b>0</b><span>tokens to paste into Claude or ChatGPT: they sign in at your address</span></li>
                  </ul>
                </article>
              </div>
              <p className="more"><Link to="/docs/teams">How to run it for a team</Link></p>
            </div>
          </section>

          <section className="band" id="reach">
            <div className="frame">
              <div className="band-head">
                <h2 className="display">What the testers <Pen>reach</Pen></h2>
                <p className="sub">Each line has a test that fails if it stops being true.</p>
              </div>
              <ul className="reach">
                {REACH.map((line) => <li key={line}><Catch size={18} />{line}</li>)}
              </ul>
              <p className="more">What it still does not do is written down too: <Link to="/docs/limits">the limits</Link></p>
            </div>
          </section>

          <section className="field" id="start">
            <div className="frame start">
              <div>
                <h2 className="display">Point it at <Pen>your app</Pen></h2>
                <ul className="zero">
                  <li><b>0</b><span>things to install in your app</span></li>
                  <li><b>0</b><span>API keys</span></li>
                  <li><b>30<small>s</small></b><span>or so, for a first scan of a small app</span></li>
                </ul>
                <div className="actions">
                  <Link to="/docs/quick-start" className="btn primary large">Run your first scan</Link>
                  <Link to="/docs/agents" className="btn large">Connect a coding agent</Link>
                </div>
              </div>
              <div className="install-steps">
                <Code label="Install" text="curl -LsSf https://raw.githubusercontent.com/cpldxx/catchbunny/main/install.sh | sh" />
                <Code label="Then, in a new terminal, open it" text="catchbunny start" />
              </div>
            </div>
          </section>
        </main>
        <SiteFoot />
      </div>
    </DemoProvider>
  );
}

function Step({ n, title, text, stat, figure, flip, id }: { n: number; title: string; text: ReactNode; stat: ReactNode; figure: ReactNode; flip?: boolean; id?: string }) {
  return (
    <section className={`step ${flip ? "flip" : ""}`} id={id}>
      <div className="frame">
        <div className="step-text">
          <span className="step-n" aria-hidden="true">{n}</span>
          <h2 className="display">{title}</h2>
          <p>{text}</p>
          {stat}
        </div>
        <div className="step-stage">{figure}</div>
      </div>
    </section>
  );
}
