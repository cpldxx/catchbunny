// What every page outside the console shares: the masthead, the footer, and a few
// small controls. None of it needs the component library the console uses.

import { MenuOutlined } from "@ant-design/icons";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { Copy } from "../components/controls";
import { Bunny } from "../components/marks";
import { consoleDoor, useInstall } from "../install";
import "./site.css";

export const DOCS = [
  ["quick-start", "Quick start"],
  ["how-a-run-works", "How a run works"],
  ["scenarios", "Regression scenarios"],
  ["agents", "Coding agents"],
  ["teams", "Teams"],
  ["models", "Models"],
  ["reference", "Reference"],
  ["limits", "Limits"],
] as const;

const SECTIONS = [
  ["how", "How it works"],
  ["agents", "For agents"],
  ["bench", "Benchmark"],
  ["team", "For teams"],
] as const;

export function Wordmark({ size = 22 }: { size?: number }) {
  return <><Bunny size={size} /><span translate="no">Catchbunny</span></>;
}

export function SiteNav() {
  const [menu, setMenu] = useState(false);
  const { pathname, hash } = useLocation();
  useEffect(() => setMenu(false), [pathname, hash]);
  // A copy of the site on a static host has no console behind it. There the way in is the quick start.
  const door = consoleDoor(useInstall()) ?? { to: "/docs/quick-start", label: "Get started" };
  return (
    <header className={`site-nav ${menu ? "open" : ""}`}>
      <a className="skip" href="#main">Skip to the content</a>
      <Link to="/" className="brand" aria-label="Catchbunny, home"><Wordmark /></Link>
      <nav aria-label="Site" id="site-links">
        {SECTIONS.map(([id, label]) => <Link key={id} to={{ pathname: "/", hash: `#${id}` }}>{label}</Link>)}
        <NavLink to="/docs">Docs</NavLink>
        <Link to={door.to} className="only-small">{door.label}</Link>
      </nav>
      <span className="spacer" />
      <Link to={door.to} className="btn nav-cta">{door.label}</Link>
      <button className="site-menu" aria-expanded={menu} aria-controls="site-links" onClick={() => setMenu(!menu)}>
        <MenuOutlined aria-hidden="true" />
        Menu
      </button>
    </header>
  );
}

export function SiteFoot() {
  const door = consoleDoor(useInstall());
  return (
    <footer className="site-foot">
      <div className="frame">
        <div className="foot-brand">
          <span className="brand"><Wordmark size={20} /></span>
          <p>Browser testers that prove a bug before they report it. It runs on your machine or your team’s server, and needs nothing installed in the app it tests.</p>
        </div>
        <nav aria-label="Product">
          <h2>Product</h2>
          {SECTIONS.map(([id, label]) => <Link key={id} to={{ pathname: "/", hash: `#${id}` }}>{label}</Link>)}
          <Link to="/docs/limits">What it does not do</Link>
        </nav>
        <nav aria-label="Documentation">
          <h2>Docs</h2>
          {DOCS.slice(0, 5).map(([slug, label]) => <Link key={slug} to={`/docs/${slug}`}>{label}</Link>)}
          <Link to="/docs/reference">Reference</Link>
        </nav>
        <nav aria-label="Use it">
          <h2>Use it</h2>
          {door && <Link to={door.to}>{door.label}</Link>}
          <Link to="/docs/quick-start">Run a first scan</Link>
          <Link to="/docs/agents">Connect an agent</Link>
          <Link to="/docs/teams">Run it for a team</Link>
        </nav>
      </div>
      <div className="frame foot-base">
        <span>Built on Playwright, FastAPI and React</span>
        <span>Self-hosted. Nothing is sent anywhere</span>
      </div>
    </footer>
  );
}

/** A panel with its own title bar holding something to copy: a command, a file. */
export function Code({ label, text }: { label: ReactNode; text: string }) {
  return (
    <div className="window">
      <div className="window-bar"><strong>{label}</strong><span className="spacer" /><Copy text={text} /></div>
      <pre className="code flat" tabIndex={0}>{text}</pre>
    </div>
  );
}

/** One command on one line, ready to copy. */
export function Command({ text }: { text: string }) {
  return (
    <div className="cmd">
      <span className="faint" aria-hidden="true">$</span>
      <code translate="no">{text}</code>
      <Copy text={text} />
    </div>
  );
}

export function NotFound() {
  return (
    <div className="site">
      <SiteNav />
      <main id="main" className="lost">
        <div className="frame">
          <Bunny size={56} hollow />
          <h1 className="display">This one got away</h1>
          <p>There is no page at this address. It may have moved, or the link may be wrong.</p>
          <div className="actions">
            <Link to="/" className="btn primary">Go to the home page</Link>
            <Link to="/docs" className="btn">Read the docs</Link>
          </div>
        </div>
      </main>
      <SiteFoot />
    </div>
  );
}
