import type { Mission, Run, ScenarioReport } from "../api";
import { clock, ms, offset, pathOf, plural, usd, who, count, tenths } from "../format";
import type { Live, ScenarioLive } from "../stream";
import { Note, Stamp } from "./marks";

/** What the run did, shown when nothing is selected. */
export function SummaryPanel({ run, live, onMission }: { run: Run; live: Live; onMission: (id: string) => void }) {
  const s = run.summary;
  const budget = s.budget ?? live.budget;
  const cov = s.coverage;
  const llm = budget?.llm;
  const missions = [...live.missions.values()].sort((a, b) => a.started_at - b.started_at);
  const failedLogins = live.logins.filter((l) => !l.ok);
  const stepsNow = Math.max(budget?.steps ?? 0, [...live.ticks.values()].reduce((n, t) => n + t.length, 0)); // the budget snapshot only moves at phase changes

  return (
    <article className="sheet">
      <header className="detail-head">
        <h2 style={{ marginTop: 0 }}>What this run did</h2>
        <p className="muted">{run.spec.kind === "scenarios" ? "Choose a scenario on the left to see each of its steps" : "Choose a finding on the left, or a tester in the lanes, to see it in detail"}</p>
      </header>
      {(failedLogins.length > 0 || live.warnings.length > 0 || !!s.blocked_missions?.length) && (
        <section className="detail-section">
          {failedLogins.map((l, i) => <Note key={i} type="warning" title={`Could not sign in as ${l.persona}${l.error ? `: ${l.error}` : ""}. Pages behind sign-in were not tested for that persona.`} />)}
          {live.warnings.map((w, i) => <Note key={`w${i}`} type="warning" title={w} />)}
          {s.blocked_missions?.slice(0, 3).map((b, i) => <Note key={`b${i}`} type="warning" title={`${b.title} could not run: ${b.reason}`} />)}
        </section>
      )}
      <section className="detail-section">
        <dl className="facts">
          {cov && <div><dt>Screens reached</dt><dd>{cov.screens} <small>across {plural(cov.templates, "layout")}</small></dd></div>}
          {cov && <div><dt>Controls exercised</dt><dd>{cov.controls_exercised} of {cov.controls_found} <small>{Math.round(cov.control_coverage * 100)}%</small></dd></div>}
          {budget && <div><dt>Steps taken</dt><dd>{count(stepsNow)} <small>of {count(budget.max_steps)} allowed</small></dd></div>}
          {s.hunt_seconds !== undefined && <div><dt>Time testing</dt><dd>{clock(s.hunt_seconds)} <small>{Math.round(s.steps_per_second ?? 0)} steps a second</small></dd></div>}
          {s.verification_replays !== undefined && <div><dt>Verification</dt><dd>{plural(s.verification_replays, "replay")} <small>in {clock(s.triage_seconds ?? 0)}</small></dd></div>}
          {s.findings && s.findings.repro_steps_before > 0 && <div><dt>Reproductions shortened</dt><dd>{s.findings.repro_steps_before} to {s.findings.repro_steps_after} <small>steps in total</small></dd></div>}
          {s.findings && <div><dt>Candidates checked</dt><dd>{s.findings.candidates} <small>{s.findings.confirmed} confirmed, {s.findings.not_reproduced + s.findings.flaky} dropped</small></dd></div>}
          {llm && llm.requests > 0 && <div><dt>Model</dt><dd>{s.model || "Model"} <small>{plural(llm.requests, "request")}</small></dd></div>}
          {llm && llm.requests > 0 && <div><dt>Tokens</dt><dd>{tenths((llm.input_tokens + llm.cache_read_tokens + llm.cache_write_tokens) / 1000)}k in, {(llm.output_tokens / 1000).toFixed(1)}k out <small>{Math.round(llm.cache_hit_rate * 100)}% from cache</small></dd></div>}
          {llm && llm.requests > 0 && <div><dt>Model cost</dt><dd>{usd(llm.usd)}{llm.usd === 0 && <small> local model</small>}</dd></div>}
          {s.stop_reason && <div><dt>Stopped because</dt><dd>{s.stop_reason}</dd></div>}
        </dl>
      </section>
      <section className="detail-section">
        <h3>{run.spec.kind === "scenarios" ? "Scenarios" : "Testers"}</h3>
        <div className="log open">
          <table>
            <tbody>
              {missions.map((m) => (
                <tr key={m.id} className="click" onClick={() => onMission(m.id)}>
                  <td><button type="button" className="rowlink" onClick={(e) => { e.stopPropagation(); onMission(m.id); }}>{m.kind === "charter" || m.kind === "scenario" ? m.title : who(m)}</button>{m.summary && <div className="why">{m.summary}</div>}</td>
                  <td className="t">{m.brain && m.brain !== "heuristic" ? m.brain : m.kind === "scenario" ? "" : m.kind}</td>
                  <td className="t">{plural(live.ticks.get(m.id)?.length ?? m.steps_taken, "step")}</td>
                  <td className="t">{!m.verdict ? <Stamp status="running" /> : m.kind === "charter" || m.kind === "scenario" ? <Stamp status={m.verdict} /> : <span className="faint">Finished</span>}</td>
                </tr>
              ))}
              {!missions.length && <tr><td className="faint">No tester has started yet</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </article>
  );
}

/** One tester's steps, live. */
export function TesterPanel({ mission, live, t0 }: { mission: Mission; live: Live; t0: number }) {
  const ticks = live.ticks.get(mission.id) ?? [];
  return (
    <article className="sheet">
      <header className="detail-head">
        <div className="meta"><span>{who(mission)}</span><span>{mission.kind}</span><span>{mission.brain && mission.brain !== "heuristic" ? `driven by ${mission.brain}` : "no model"}</span></div>
        <h2>{mission.title}</h2>
        {mission.charter && <p style={{ maxWidth: "76ch", marginBottom: 8 }} className="muted">{mission.charter}</p>}
        <div className="meta">
          {!mission.verdict ? <Stamp status="running" /> : mission.kind === "charter" || mission.kind === "scenario" ? <Stamp status={mission.verdict} /> : null}
          <span>{mission.summary || `${plural(ticks.length, "step")} so far`}</span>
        </div>
      </header>
      <section className="detail-section">
        <h3>{plural(ticks.length, "step")}</h3>
        <div className="log" style={{ maxHeight: 560 }}>
          <table>
            <tbody>
              {ticks.map((t) => (
                <tr key={t.index} className={t.ok ? "" : "bad"}>
                  <td className="t">{offset(t.ts - t0)}</td>
                  <td>
                    {t.action}
                    {!t.ok && <div className="why">{t.error}</div>}
                    {t.signals.map((s, i) => <div key={i} className="why"><span className="tag fired">{s.kind.replace("_", " ")}</span> {s.message}</div>)}
                  </td>
                  <td className="url"><code className="faint">{pathOf(t.url)}</code></td>
                  <td className="t">{ms(t.ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </article>
  );
}

const MODE: Record<string, [string, string]> = {
  learned: ["Replayed", "Replayed from a previous passing run, with no model call."],
  healed: ["Healed", "A control had moved or been renamed. It was found again and the step replayed."],
  model: ["Model", "A model tester performed this step."],
  not_run: ["Not run", "The scenario stopped before this step."],
  replay_failed: ["Replay failed", "The recorded actions no longer work on this page."],
};

export function ScenarioPanel({ scenario }: { scenario: ScenarioLive }) {
  const r: ScenarioReport | undefined = scenario.report;
  return (
    <article className="sheet">
      <header className="detail-head">
        <h2 style={{ marginTop: 0 }}>{scenario.name}</h2>
        <div className="meta">
          {r ? <Stamp status={r.verdict} /> : <Stamp status="running" />}
          {r?.reason && <span className="state">{r.reason}</span>}
          {r && <span>{plural(r.learned_steps, "step")} replayed without a model, {r.model_steps} by a model</span>}
        </div>
      </header>
      <section className="detail-section">
        <h3>Steps</h3>
        <ol className="steps">
          {(r?.steps ?? scenario.steps.map((s) => ({ ...s, text: `Step ${s.index + 1}`, passed: s.passed ?? false, detail: s.detail ?? "" }))).map((s) => (
            <li key={s.index} className={s.passed ? "" : s.mode === "not_run" ? "" : "fail"}>
              <span>{s.text}{s.detail && <div className="why">{s.detail}</div>}</span>
              <span className="aside" title={MODE[s.mode]?.[1]}><span className="tag">{MODE[s.mode]?.[0] ?? s.mode}</span></span>
            </li>
          ))}
        </ol>
      </section>
      {!!r?.checks.length && (
        <section className="detail-section">
          <h3>Checks</h3>
          <div className="log"><table><tbody>{r.checks.map((c, i) => (
            <tr key={i} className={c.passed ? "" : "bad"}>
              <td className="s">{c.passed ? "Pass" : "Fail"}</td>
              <td><code>{c.check.kind}</code> {c.check.kind === "no_signal" ? "the original defect does not fire" : c.check.value || `${c.check.role} “${c.check.name}”`}{c.detail && <div className="why">{c.detail}</div>}</td>
            </tr>))}</tbody></table></div>
        </section>
      )}
      {!!r?.signals.length && (
        <section className="detail-section">
          <h3>Detected during the run</h3>
          {r.signals.map((s, i) => <p key={i} className="muted"><span className="tag fired">{s.kind.replace("_", " ")}</span> {s.message}</p>)}
        </section>
      )}
    </article>
  );
}
