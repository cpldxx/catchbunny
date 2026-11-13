import { ApartmentOutlined, ApiOutlined, AppstoreOutlined, FileDoneOutlined, LogoutOutlined, MenuOutlined, PlayCircleOutlined, ReadOutlined, TeamOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button, Modal, Select } from "antd";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { api, type Meta, type RunSpec } from "../api";
import { forgetInstall } from "../install";
import { Bunny } from "./marks";
import { RunForm } from "./RunForm";

export interface ShellContext { startRun: (preset?: Partial<RunSpec>) => void }
export const useShell = () => useOutletContext<ShellContext>();

export function Shell({ meta }: { meta: Meta }) {
  const { projectId, runId } = useParams();
  const signOut = useMutation({ mutationFn: api.signOut, onSuccess: () => { forgetInstall(); window.location.assign("/app/sign-in"); } });
  const navigate = useNavigate();
  const location = useLocation();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId!), enabled: !!runId });
  const current = projectId ?? run.data?.project_id;
  const project = projects.data?.find((p) => p.id === current);
  const runs = useQuery({ queryKey: ["runs", current], queryFn: () => api.runs(current!), enabled: !!current });
  const scenarios = useQuery({ queryKey: ["scenarios", current], queryFn: () => api.scenarios(current!), enabled: !!current });
  const [form, setForm] = useState<{ open: boolean; preset?: Partial<RunSpec> }>({ open: false });
  const [menu, setMenu] = useState(false);
  const [help, setHelp] = useState(false);
  useEffect(() => setMenu(false), [location.pathname]);
  useEffect(() => {
    document.title = project ? `${project.name} · Catchbunny` : "Catchbunny";
    return () => { document.title = "Catchbunny"; };
  }, [project]);

  // Shortcuts. "g" starts a two-key jump, as in the tools this console sits beside.
  const lead = useRef(0);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey || el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || document.querySelector(".ant-drawer-open, .ant-modal-wrap:not([style*='display: none'])")) return;
      const jumping = Date.now() - lead.current < 1500;
      lead.current = 0;
      if (e.key === "?") setHelp(true);
      else if (e.key === "g") lead.current = Date.now();
      else if (jumping && e.key === "a") navigate("/app");
      else if (jumping && e.key === "c") navigate("/app/connect");
      else if (jumping && current && e.key === "r") navigate(`/app/p/${current}/runs`);
      else if (jumping && current && e.key === "s") navigate(`/app/p/${current}/scenarios`);
      else if (jumping && current && e.key === "m") navigate(`/app/p/${current}/map`);
      else if (e.key === "n" && project) setForm({ open: true });
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, project, navigate]);

  const row = (active: boolean) => `nav-row ${active ? "on" : ""}`;

  return (
    <div className="app">
      <a className="skip" href="#content">Skip to the content</a>
      <aside className={`side ${menu ? "open" : ""}`}>
        <div className="side-top">
          <Link to="/" className="brand" aria-label="Catchbunny, home">
            <Bunny size={22} />
            <span translate="no">Catchbunny</span>
          </Link>
          <button className="side-menu" aria-expanded={menu} aria-controls="side-body" onClick={() => setMenu(!menu)}>
            <MenuOutlined aria-hidden="true" />
            Menu
          </button>
        </div>
        <div className="side-body" id="side-body">
          <nav className="side-nav" aria-label="Workspace">
            <NavLink to="/app" end className={({ isActive }) => row(isActive)}>
              <AppstoreOutlined aria-hidden="true" />
              All apps
              <span className="count">{projects.data?.length ?? ""}</span>
            </NavLink>
            <NavLink to="/app/connect" className={({ isActive }) => row(isActive)}>
              <ApiOutlined aria-hidden="true" />
              Connect an agent
            </NavLink>
            {meta.mode === "team" && (
              <NavLink to="/app/team" className={({ isActive }) => row(isActive)}>
                <TeamOutlined aria-hidden="true" />
                Team
              </NavLink>
            )}
          </nav>
          {!!projects.data?.length && (
            <>
              <p className="side-label">App under test</p>
              <Select
                aria-label="App under test"
                value={current}
                placeholder="Choose an app…"
                options={projects.data.map((p) => ({ value: p.id, label: p.name }))}
                onChange={(id) => navigate(`/app/p/${id}/runs`)}
              />
            </>
          )}
          {current && (
            <nav aria-label="This app">
              <NavLink to={`/app/p/${current}/runs`} className={({ isActive }) => row(isActive || !!runId)}>
                <PlayCircleOutlined aria-hidden="true" />
                Runs
                <span className="count">{runs.data?.length ?? ""}</span>
              </NavLink>
              <NavLink to={`/app/p/${current}/scenarios`} className={({ isActive }) => row(isActive)}>
                <FileDoneOutlined aria-hidden="true" />
                Scenarios
                <span className="count">{scenarios.data?.length ?? ""}</span>
              </NavLink>
              <NavLink to={`/app/p/${current}/map`} className={({ isActive }) => row(isActive)}>
                <ApartmentOutlined aria-hidden="true" />
                Map
              </NavLink>
            </nav>
          )}
          <div className="side-foot">
            <Link to="/docs" className="nav-row"><ReadOutlined aria-hidden="true" />Docs</Link>
            <button type="button" className="nav-row" onClick={() => setHelp(true)}><kbd>?</kbd>Keyboard shortcuts</button>
            {meta.user && (
              <div className="side-user">
                <span className="ellipsis" title={meta.user.email}><strong>{meta.user.name}</strong><br /><span className="faint">{meta.team}</span></span>
                <button type="button" className="copy" onClick={() => signOut.mutate()} disabled={signOut.isPending}><LogoutOutlined aria-hidden="true" />Sign out</button>
              </div>
            )}
          </div>
        </div>
      </aside>
      <div className="main" id="content">
        {project && (
          <header className="topbar">
            <span className="app-name">{project.name}</span>
            <code className="faint ellipsis">{project.environments[0]?.base_url}</code>
            <span className="spacer" />
            <Button type="primary" onClick={() => setForm({ open: true })}>Start a run</Button>
          </header>
        )}
        <Outlet context={{ startRun: (preset) => setForm({ open: true, preset }) } satisfies ShellContext} />
      </div>
      {project && <RunForm project={project} open={form.open} preset={form.preset} onClose={() => setForm({ open: false })} />}
      <Modal title="Keyboard shortcuts" open={help} onCancel={() => setHelp(false)} footer={null} width={440}>
        <dl className="keys">
          <div><dt>Start a run</dt><dd><kbd>n</kbd></dd></div>
          <div><dt>All apps</dt><dd><kbd>g</kbd> then <kbd>a</kbd></dd></div>
          <div><dt>Connect an agent</dt><dd><kbd>g</kbd> then <kbd>c</kbd></dd></div>
          <div><dt>Runs</dt><dd><kbd>g</kbd> then <kbd>r</kbd></dd></div>
          <div><dt>Scenarios</dt><dd><kbd>g</kbd> then <kbd>s</kbd></dd></div>
          <div><dt>Map</dt><dd><kbd>g</kbd> then <kbd>m</kbd></dd></div>
          <div><dt>Next or previous finding</dt><dd><kbd>j</kbd> <kbd>k</kbd></dd></div>
          <div><dt>Back to the run summary</dt><dd><kbd>Esc</kbd></dd></div>
          <div><dt>This list</dt><dd><kbd>?</kbd></dd></div>
        </dl>
      </Modal>
    </div>
  );
}
