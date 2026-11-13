// Is there a Catchbunny server behind this page?
//
// The same site is served two ways. `catchbunny start` serves it together with the
// API, the console and the MCP endpoint: an install. A copy on a static host is
// only the site: there is no console to open, and saying "Open the console" there
// would lead nowhere. So the pages ask, once, and offer the console only where
// one exists.

import { useEffect, useState } from "react";
import type { Meta } from "./api";

export type Install =
  | { state: "checking" }
  | { state: "none" } // a static copy of the site, or the server is not answering
  | { state: "here"; meta: Meta };

let asked: Promise<Install> | null = null;

async function ask(): Promise<Install> {
  try {
    const res = await fetch("/v1/meta", { headers: { accept: "application/json" } });
    // A static host answers any path with the page itself. Only a real server says what it is.
    if (!res.ok || !(res.headers.get("content-type") ?? "").includes("json")) return { state: "none" };
    const meta = (await res.json()) as Meta;
    return meta.product === "catchbunny" ? { state: "here", meta } : { state: "none" };
  } catch {
    return { state: "none" };
  }
}

/** Ask again on the next use: after signing in or out, what the server says has changed. */
export function forgetInstall(): void {
  asked = null;
}

export function useInstall(): Install {
  const [install, setInstall] = useState<Install>({ state: "checking" });
  useEffect(() => {
    let live = true;
    (asked ??= ask()).then((found) => { if (live) setInstall(found); });
    return () => { live = false; };
  }, []);
  return install;
}

/** Where the console's front door is for this visitor, and what to call it. */
export function consoleDoor(install: Install): { to: string; label: string } | null {
  if (install.state !== "here") return null;
  const { meta } = install;
  if (meta.mode === "team" && meta.needs_owner) return { to: "/app/setup", label: "Set up this server" };
  if (meta.mode === "team" && !meta.user) return { to: "/app/sign-in", label: "Sign in" };
  return { to: "/app", label: "Open the console" };
}
