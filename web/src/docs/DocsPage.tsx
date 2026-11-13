// The documentation. A document first: a rail that says where you are, a calm
// column at a readable measure, and a way forward at the bottom of every page.

import { Navigate, NavLink, Link, useParams } from "react-router-dom";
import { useEffect } from "react";
import { Copy } from "../components/controls";
import { DOCS, SiteFoot, SiteNav } from "../site/Site";
import { PAGES, type Block } from "./pages";
import "./docs.css";

const anchor = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function Piece({ block }: { block: Block }) {
  if ("h" in block) return <h2 id={anchor(block.h)}>{block.h}</h2>;
  if ("p" in block) return <p>{block.p}</p>;
  if ("code" in block) {
    return (
      <div className="doc-code">
        <pre tabIndex={0} translate="no">{block.code}</pre>
        <Copy text={block.code} />
      </div>
    );
  }
  if ("list" in block) return <ul>{block.list.map((item, i) => <li key={i}>{item}</li>)}</ul>;
  if ("steps" in block) return <ol>{block.steps.map((item, i) => <li key={i}>{item}</li>)}</ol>;
  if ("note" in block) return <aside className="doc-note">{block.note}</aside>;
  return (
    <div className="doc-table">
      <table>
        <thead><tr>{block.table.head.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
        <tbody>{block.table.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

export default function DocsPage() {
  const { slug } = useParams();
  const at = PAGES.findIndex((p) => p.slug === slug);
  const page = PAGES[at];
  useEffect(() => {
    if (page) document.title = `${page.title} · Catchbunny docs`;
    window.scrollTo(0, 0);
    return () => { document.title = "Catchbunny"; };
  }, [page]);
  if (!slug) return <Navigate to={`/docs/${PAGES[0].slug}`} replace />;
  if (!page) return <Navigate to="/docs" replace />;
  const prev = PAGES[at - 1], next = PAGES[at + 1];
  const sections = page.blocks.filter((b): b is { h: string } => "h" in b);

  return (
    <div className="site">
      <SiteNav />
      <div className="frame docs">
        <nav className="docs-rail" aria-label="Documentation">
          <h2>Docs</h2>
          {DOCS.map(([to, label]) => <NavLink key={to} to={`/docs/${to}`}>{label}</NavLink>)}
        </nav>
        <main id="main" className="doc">
          <h1 className="display">{page.title}</h1>
          <p className="doc-lead">{page.lead}</p>
          {page.blocks.map((block, i) => <Piece key={i} block={block} />)}
          <nav className="doc-next" aria-label="More documentation">
            {prev ? <Link to={`/docs/${prev.slug}`}><span>Before this</span>{prev.title}</Link> : <span />}
            {next && <Link to={`/docs/${next.slug}`} className="forward"><span>Next</span>{next.title}</Link>}
          </nav>
        </main>
        {sections.length > 1 && (
          <nav className="docs-here" aria-label="On this page">
            <h2>On this page</h2>
            {sections.map((s) => <a key={s.h} href={`#${anchor(s.h)}`}>{s.h}</a>)}
          </nav>
        )}
      </div>
      <SiteFoot />
    </div>
  );
}
