// The product map the testers built, as a table of routes (see RouteTree). Screens
// that share a route are one row; the bar in a row is how much of it has been
// exercised. Nothing is drawn between rows: choosing a route marks what it leads
// to and what leads to it, and lists both beside the table.

import { useQuery } from "@tanstack/react-query";
import { Button, Skeleton } from "antd";
import { useMemo } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, type ProductMap } from "../api";
import { relate, RouteTree, routesOf, tally, titleOf, type MapScreen } from "../components/RouteTree";
import { Note } from "../components/marks";
import { useShell } from "../components/Shell";
import { plural } from "../format";

function edgeLabel(key: string): string {
  const [kind, rest = ""] = key.split(/:(.*)/s);
  if (kind === "navigate") return `open ${rest}`;
  const [role, name] = rest.split("|");
  return `${kind} ${role} “${name}”`;
}

/** Page titles usually end with the app's name. If every screen's does, it says nothing and is dropped. */
function withoutSharedEnding(titles: string[]): (title: string) => string {
  const named = titles.filter(Boolean);
  const ending = named[0]?.match(/\s[-|·–—]\s[^-|·–—]+$/)?.[0];
  if (!ending || named.length < 2 || !named.every((t) => t.endsWith(ending))) return (t) => t;
  return (t) => t.slice(0, t.length - ending.length) || t;
}

function read(map: ProductMap) {
  const trim = withoutSharedEnding(map.states.map((s) => s.title));
  const screens = [...map.states]
    .sort((a, b) => a.path.localeCompare(b.path) || b.visits - a.visits)
    .map((s): MapScreen => ({ id: s.id, path: s.path, title: trim(s.title), controls: s.controls_total, exercised: s.controls_done, visits: s.visits }));
  const links = Object.entries(map.edges).flatMap(([from, ways]) => Object.entries(ways).map(([key, to]) => ({ from, to, label: edgeLabel(key) })));
  const routes = routesOf(screens);
  return { screens, routes, ...relate(routes, links) };
}

export function MapPage() {
  const { projectId } = useParams();
  const { startRun } = useShell();
  const map = useQuery({ queryKey: ["map", projectId], queryFn: () => api.map(projectId!) });
  const [params, setParams] = useSearchParams();
  const selected = params.get("route");
  const setSelected = (route: string | null) => setParams(route ? { route } : {}, { replace: true });
  const model = useMemo(() => (map.data ? read(map.data) : null), [map.data]);
  const screens = model?.screens ?? [];
  const total = tally(screens);
  const route = model?.routes.find((r) => r.key === selected);
  const leads = route ? model!.out.get(route.key) : undefined;
  const from = route ? model!.into.get(route.key) : undefined;
  const leadKeys = useMemo(() => new Set(leads?.keys() ?? []), [leads]);
  const sum = route ? tally(route.screens) : null;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1 className="title">Map</h1>
          <p className="subtitle">
            {screens.length
              ? <>Every screen the testers have reached, grouped by route. {plural(screens.length, "screen")} on {plural(model!.routes.length, "route")}; {total.exercised} of {plural(total.controls, "control")} exercised. The map is kept between runs, so each run starts where the last one stopped.</>
              : "The map is drawn by the testers as they explore, and kept between runs."}
          </p>
        </div>
      </div>
      {map.isError && <Note type="error" title={<>The map could not be loaded: {(map.error as Error).message}. <button className="link" onClick={() => map.refetch()}>Try again</button></>} />}
      {map.isLoading && <div className="sheet sheet-body"><Skeleton active /></div>}
      {map.data && !screens.length && (
        <div className="sheet blank">
          <h2>Nothing mapped yet</h2>
          <p>The first run draws the map. A discovery run does only that, and drafts scenarios from the flows it finds.</p>
          <Button onClick={() => startRun({ kind: "discovery" })}>Start a discovery run</Button>
        </div>
      )}
      {model && !!screens.length && (
        <div className="sheet map-wrap">
          <div className="map-table">
            <RouteTree routes={model.routes} selected={selected} onSelect={setSelected} leads={leadKeys} from={from} />
          </div>
          <aside className="map-side">
            {route && sum ? (
              <>
                <h2><code>{route.key}</code></h2>
                <p>{titleOf(route.screens)}</p>
                <dl className="facts" style={{ margin: "16px 0", gridTemplateColumns: "1fr 1fr" }}>
                  <div><dt>Controls exercised</dt><dd>{sum.exercised} of {sum.controls}</dd></div>
                  <div><dt>Visits</dt><dd>{sum.visits}</dd></div>
                </dl>
                {sum.pages > 1 && (
                  <>
                    <h3>{sum.pages} pages on this route</h3>
                    <ul className="plain rows">
                      {[...new Set(route.screens.map((s) => s.path))].map((path) => {
                        const states = route.screens.filter((s) => s.path === path), t = tally(states);
                        return <li key={path}><code>{path}</code><span className="ellipsis">{titleOf(states)}</span><span className="faint num">{t.exercised} of {t.controls}</span></li>;
                      })}
                    </ul>
                  </>
                )}
                {sum.pages === 1 && route.screens.length > 1 && (
                  <>
                    <h3>{route.screens.length} states at this address</h3>
                    <ul className="plain rows">
                      {route.screens.map((s, i) => <li key={s.id}><span>State {i + 1}</span><span className="faint">{plural(s.visits, "visit")}</span><span className="faint num">{s.exercised} of {s.controls}</span></li>)}
                    </ul>
                  </>
                )}
                <h3>Leads to</h3>
                {leads?.size ? (
                  <ul className="plain rows">
                    {[...leads].map(([to, how]) => (
                      <li key={to}>
                        <button className="link" onClick={() => setSelected(to)}><code>{to}</code></button>
                        <span className="faint ellipsis">{how[0]}{how.length > 1 ? ` and ${how.length - 1} more` : ""}</span>
                      </li>
                    ))}
                  </ul>
                ) : <p style={{ marginTop: 0 }}>Nothing found that leaves this route</p>}
                <h3>Reached from</h3>
                {from?.size ? (
                  <ul className="plain rows">
                    {[...from].map((key) => <li key={key}><button className="link" onClick={() => setSelected(key)}><code>{key}</code></button></li>)}
                  </ul>
                ) : <p style={{ marginTop: 0 }}>Only by opening its address directly</p>}
              </>
            ) : (
              <>
                <h2>Reading the map</h2>
                <p>Screens are grouped by route. A count beside a route means several pages, or several states of one page, share it.</p>
                <p>The bar fills as a route’s controls are exercised. Choose a route to see what it leads to and how it is reached.</p>
              </>
            )}
          </aside>
        </div>
      )}
    </main>
  );
}
