import "./styles.css";

import { Component, lazy, StrictMode, Suspense, type ComponentType, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { installTokens } from "./theme";

installTokens();

// Three surfaces, three chunks. The landing page and the docs never load the
// console's component library; the console never loads the recorded demo run.
//
// A tab left open across a new build asks for a chunk that no longer exists. The
// fix for that is the new page, so the first failure reloads once; a second one
// is a real error and is shown as one.
function surface<T extends ComponentType>(load: () => Promise<{ default: T }>) {
  return lazy(async () => {
    try {
      const module = await load();
      sessionStorage.removeItem("reloaded-for-chunk");
      return module;
    } catch (error) {
      if (!sessionStorage.getItem("reloaded-for-chunk")) {
        sessionStorage.setItem("reloaded-for-chunk", "1");
        window.location.reload();
        return new Promise<{ default: T }>(() => {}); // the reload is on its way; render nothing until it lands
      }
      throw error;
    }
  });
}

const Landing = surface(() => import("./landing/LandingPage").then((m) => ({ default: m.LandingPage })));
const Docs = surface(() => import("./docs/DocsPage"));
const Console = surface(() => import("./console"));
const NotFound = surface(() => import("./site/Site").then((m) => ({ default: m.NotFound })));

/** The last resort: a page that says what happened and how to recover, instead of a blank one. */
class Fallback extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="crashed">
        <h1 className="display">This page did not load</h1>
        <p>Part of the site could not be fetched. The server may have been restarted or rebuilt while this tab was open.</p>
        <button className="btn primary" onClick={() => { sessionStorage.removeItem("reloaded-for-chunk"); window.location.reload(); }}>Reload the page</button>
      </main>
    );
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Fallback>
      <Suspense fallback={<div className="boot" role="status" aria-label="Loading" />}>
        <Routes>
          <Route index element={<Landing />} />
          <Route path="docs" element={<Docs />} />
          <Route path="docs/:slug" element={<Docs />} />
          <Route path="app/*" element={<Console />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
      </Fallback>
    </BrowserRouter>
  </StrictMode>,
);
