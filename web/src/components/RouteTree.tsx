// The product map as a table of routes. Screens that share a route (the pages of
// /products/:id, or two states of one cart) are one row, and rows are indented by
// address, so the table has the shape of the site and nothing has to be drawn
// between rows. What a chosen route leads to is marked in the last column.

import { ArrowLeftOutlined, ArrowRightOutlined, SwapOutlined } from "@ant-design/icons";

export interface MapScreen { id: string; path: string; title: string; controls: number; exercised: number; visits: number }
export interface MapLink { from: string; to: string; label: string }
type Guide = "pipe" | "tee" | "elbow" | "none";
export interface Route<S extends MapScreen = MapScreen> { key: string; depth: number; screens: S[]; guides: Guide[] }

/** The route a path belongs to: an id in the address becomes a placeholder. */
export function routeKey(path: string): string {
  const parts = path.split("/").filter(Boolean).map((part) => (/^\d+$/.test(part) || (/^[0-9a-f-]{8,}$/i.test(part) && /\d/.test(part)) ? ":id" : part));
  return "/" + parts.join("/");
}

/** Screens grouped into routes and arranged as a tree by address, in the order it is drawn. Siblings keep the order their first screen came in. */
export function routesOf<S extends MapScreen>(screens: S[]): Route<S>[] {
  const groups = new Map<string, S[]>();
  for (const s of screens) {
    const key = routeKey(s.path);
    groups.set(key, [...(groups.get(key) ?? []), s]);
  }
  const parentOf = (key: string): string | null => {
    const parts = key.split("/").filter(Boolean);
    for (let n = parts.length - 1; n > 0; n--) {
      const up = "/" + parts.slice(0, n).join("/");
      if (groups.has(up)) return up;
    }
    return key !== "/" && groups.has("/") ? "/" : null;
  };
  const children = new Map<string | null, string[]>();
  for (const key of groups.keys()) {
    const parent = parentOf(key);
    children.set(parent, [...(children.get(parent) ?? []), key]);
  }
  const out: Route<S>[] = [];
  // `trail` says, for each ancestor below the top, whether it has siblings still to come: that is where a line carries on down.
  const walk = (key: string, depth: number, guides: Guide[], trail: boolean[]) => {
    out.push({ key, depth, screens: groups.get(key)!, guides });
    const kids = children.get(key) ?? [];
    kids.forEach((kid, i) => {
      const last = i === kids.length - 1;
      walk(kid, depth + 1, [...trail.map((more): Guide => (more ? "pipe" : "none")), last ? "elbow" : "tee"], [...trail, !last]);
    });
  };
  for (const key of children.get(null) ?? []) walk(key, 0, [], []);
  return out;
}

/** Links between screens, read as links between routes: where each route leads and how, and what leads to it. */
export function relate(routes: Route[], links: MapLink[]): { out: Map<string, Map<string, string[]>>; into: Map<string, Set<string>> } {
  const routeOf = new Map<string, string>();
  for (const r of routes) for (const s of r.screens) routeOf.set(s.id, r.key);
  const out = new Map<string, Map<string, string[]>>(), into = new Map<string, Set<string>>();
  for (const link of links) {
    const a = routeOf.get(link.from), b = routeOf.get(link.to);
    if (!a || !b || a === b) continue;
    const ways = out.get(a) ?? new Map<string, string[]>();
    const labels = ways.get(b) ?? [];
    if (link.label && !labels.includes(link.label)) labels.push(link.label);
    out.set(a, ways.set(b, labels));
    into.set(b, (into.get(b) ?? new Set<string>()).add(a));
  }
  return { out, into };
}

export function tally(screens: MapScreen[]): { controls: number; exercised: number; visits: number; pages: number } {
  return {
    controls: screens.reduce((n, s) => n + s.controls, 0),
    exercised: screens.reduce((n, s) => n + s.exercised, 0),
    visits: screens.reduce((n, s) => n + s.visits, 0),
    pages: new Set(screens.map((s) => s.path)).size,
  };
}

/** What several screens on one route are called together. */
export function titleOf(screens: MapScreen[]): string {
  const titles = [...new Set(screens.map((s) => s.title).filter(Boolean))];
  if (!titles.length) return "No title";
  return titles.length === 1 ? titles[0] : `${titles[0]} and ${titles.length - 1} more`;
}

function several(screens: MapScreen[]): string {
  const { pages } = tally(screens);
  return pages > 1 ? `${pages} pages` : screens.length > 1 ? `${screens.length} states` : "";
}

interface Props<S extends MapScreen> {
  routes: Route<S>[];
  selected: string | null;
  onSelect: (key: string | null) => void;
  /** Routes the chosen one leads to, and routes that lead to it. */
  leads?: Set<string>;
  from?: Set<string>;
  /** The route and its meter only, for a narrow column. */
  compact?: boolean;
  /** Pointing at a row chooses it. Without this a row is chosen by clicking, and clicking it again lets go. */
  hover?: boolean;
  /** Count only these screens. A route with none of its screens shown keeps its place but stays blank. */
  shown?: (screen: S) => boolean;
}

export function RouteTree<S extends MapScreen>({ routes, selected, onSelect, leads, from, compact, hover, shown }: Props<S>) {
  return (
    <>
      {!compact && (
        <div className="routes-head">
          <span>Route</span><span className="route-title">Screen</span><span className="route-span">Controls exercised</span><span className="route-visits">Visits</span><span />
        </div>
      )}
      <ul className={`routes ${compact ? "compact" : ""}`}>
        {routes.map((r) => {
          const screens = shown ? r.screens.filter(shown) : r.screens;
          const sum = tally(screens);
          const chosen = r.key === selected;
          const to = !!leads?.has(r.key), back = !!from?.has(r.key);
          const count = several(screens);
          return (
            <li key={r.key} className={screens.length ? "" : "wait"} aria-hidden={screens.length ? undefined : true}>
              <button
                type="button" className={`route ${chosen ? "sel" : ""}`} aria-pressed={chosen} tabIndex={screens.length ? 0 : -1}
                aria-label={compact ? `${r.key}, ${sum.exercised} of ${sum.controls} controls exercised` : undefined}
                onClick={() => onSelect(chosen && !hover ? null : r.key)}
                onPointerEnter={hover ? (e) => { if (e.pointerType === "mouse") onSelect(r.key); } : undefined}
              >
                <span className="route-name">
                  {r.guides.map((g, i) => <i key={i} className={`guide ${g}`} />)}
                  <code className="ellipsis">{r.key}</code>
                  {count && <span className="tag">{count}</span>}
                </span>
                {!compact && <span className="route-title ellipsis">{titleOf(screens)}</span>}
                <span className="route-meter" aria-hidden="true"><i style={{ width: `${sum.controls ? (sum.exercised / sum.controls) * 100 : 0}%` }} /></span>
                {!compact && <span className="route-num num">{sum.exercised} of {sum.controls}</span>}
                {!compact && <span className="route-num route-visits num">{sum.visits}</span>}
                <span className="route-rel">
                  {to && back ? <SwapOutlined aria-label="The chosen route leads here, and this leads back" />
                    : to ? <ArrowRightOutlined aria-label="The chosen route leads here" />
                    : back ? <ArrowLeftOutlined aria-label="This leads to the chosen route" /> : null}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}
