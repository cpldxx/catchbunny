// The console: everything under /app. Its providers live here so that only this
// chunk carries the component library and the query cache.
//
// It first asks what is behind the page (see install.ts). With no server there is
// no console, and the page says how to get one. On a team server, everything but
// the ways in waits for a signed-in person.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp, ConfigProvider } from "antd";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { session } from "./api";
import { antTheme } from "./antTheme";
import { Shell } from "./components/Shell";
import { useInstall } from "./install";
import { ConnectPage } from "./pages/ConnectPage";
import { AuthorizePage, JoinPage, NoServerPage, SetUpPage, SignInPage } from "./pages/DoorPages";
import { MapPage } from "./pages/MapPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { RunPage } from "./pages/RunPage";
import { RunsPage } from "./pages/RunsPage";
import { ScenariosPage } from "./pages/ScenariosPage";
import { TeamPage } from "./pages/TeamPage";

const queries = new QueryClient({ defaultOptions: { queries: { staleTime: 2_000, retry: 1, refetchOnWindowFocus: false } } });
const DOORS = ["/app/sign-in", "/app/join", "/app/setup"];

// A session can end while the console is open (it expired, or the owner removed this person).
// The next request says so, and the person is sent to sign in and brought back to where they were.
session.onSignedOut = () => {
  const here = window.location.pathname + window.location.search;
  if (!DOORS.some((door) => window.location.pathname.startsWith(door))) window.location.assign(`/app/sign-in?next=${encodeURIComponent(here)}`);
};

export default function Console() {
  const install = useInstall();
  const location = useLocation();
  if (install.state === "checking") return <div className="boot" role="status" aria-label="Loading" />;
  if (install.state === "none") return <NoServerPage />;
  const { meta } = install;
  const team = meta.mode === "team";
  const here = location.pathname + location.search;
  // On a team server nothing inside is shown to a browser that has not signed in.
  const inside = (page: React.ReactNode) => {
    if (team && meta.needs_owner) return <Navigate to="/app/setup" replace />;
    if (team && !meta.user) return <Navigate to={`/app/sign-in?next=${encodeURIComponent(here)}`} replace />;
    return page;
  };

  return (
    <ConfigProvider theme={antTheme}>
      <AntApp>
        <QueryClientProvider client={queries}>
          <Routes>
            {team && <Route path="setup" element={<SetUpPage meta={meta} />} />}
            {team && <Route path="join" element={meta.user ? <Navigate to="/app" replace /> : <JoinPage meta={meta} />} />}
            {team && <Route path="sign-in" element={meta.user ? <Navigate to="/app" replace /> : <SignInPage meta={meta} />} />}
            {team && <Route path="authorize" element={inside(<AuthorizePage meta={meta} />)} />}
            <Route element={inside(<Shell meta={meta} />)}>
              <Route index element={<ProjectsPage />} />
              <Route path="p/:projectId" element={<Navigate to="runs" replace />} />
              <Route path="p/:projectId/runs" element={<RunsPage />} />
              <Route path="p/:projectId/scenarios" element={<ScenariosPage />} />
              <Route path="p/:projectId/map" element={<MapPage />} />
              <Route path="runs/:runId" element={<RunPage />} />
              <Route path="connect" element={<ConnectPage meta={meta} />} />
              {team && <Route path="team" element={<TeamPage meta={meta} />} />}
              <Route path="*" element={<Navigate to="/app" replace />} />
            </Route>
          </Routes>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
