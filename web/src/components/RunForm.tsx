import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Checkbox, Drawer, Form, Input, InputNumber, Segmented, Select, Slider, Switch } from "antd";
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Brain, type BrowserName, type Project, type RunKind, type RunSpec } from "../api";
import { plural } from "../format";
import { Note } from "./marks";

const KINDS: { value: RunKind; label: string; says: string }[] = [
  { value: "deep_scan", label: "Deep scan", says: "Testers hunt for defects. Each one is replayed in a fresh browser before it is reported." },
  { value: "discovery", label: "Discovery", says: "Testers map the app and draft regression scenarios from what they find" },
  { value: "scenarios", label: "Scenarios", says: "Saved scenarios are replayed. Steps that passed before run without a model." },
];
const DEVICES = ["desktop", "laptop", "tablet", "iphone", "android"];
const ENGINES: { value: BrowserName; label: string; says: string }[] = [
  { value: "chromium", label: "Chromium", says: "The engine of Chrome and Edge" },
  { value: "firefox", label: "Firefox", says: "Firefox’s own engine. It has no mobile mode, so a phone is its viewport and touch only." },
  { value: "webkit", label: "WebKit", says: "The engine Safari is built on. A finding here is replayed here." },
];
const BRAINS: { value: Brain; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "heuristic", label: "No model" },
  { value: "claude", label: "Claude" },
  { value: "ollama", label: "Ollama" },
];

const DEFAULTS: Partial<RunSpec> = {
  kind: "deep_scan", goal: "", areas: [], devices: ["desktop"], browser: "chromium", testers: 6, max_steps: 400, max_minutes: 5,
  brain: "auto", model: "", scenario_ids: [], gate: "P1", verify_runs: 2, record: true, minimize: true,
};

export function RunForm({ project, open, preset, onClose }: { project: Project; open: boolean; preset?: Partial<RunSpec>; onClose: () => void }) {
  const [form] = Form.useForm<Partial<RunSpec>>();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const client = useQueryClient();
  const models = useQuery({ queryKey: ["models"], queryFn: api.models, enabled: open, staleTime: 10_000 });
  const scenarios = useQuery({ queryKey: ["scenarios", project.id], queryFn: () => api.scenarios(project.id), enabled: open });
  const kind = Form.useWatch("kind", form) ?? "deep_scan";
  const brain = Form.useWatch("brain", form) ?? "auto";
  const engine = Form.useWatch("browser", form) ?? "chromium";
  const provider = models.data?.providers.find((p) => p.id === brain);
  const ollama = models.data?.providers.find((p) => p.id === "ollama");

  useEffect(() => {
    if (open) form.setFieldsValue({ ...DEFAULTS, ...preset });
  }, [open, preset, form]);

  useEffect(() => {
    if (brain === "ollama" && ollama?.models.length && !form.getFieldValue("model")) {
      // No configured default: the smallest model that is still a capable tool caller (about 7B and up) is the quickest useful one.
      const bySize = [...ollama.models].sort((a, b) => (a.size_gb ?? 0) - (b.size_gb ?? 0));
      form.setFieldValue("model", ollama.default_model || (bySize.find((m) => (m.size_gb ?? 0) >= 4) ?? bySize[0]).name);
    }
  }, [brain, ollama, form]);

  const start = useMutation({
    mutationFn: (spec: Partial<RunSpec>) => api.startRun(project.id, spec),
    onSuccess: (run) => {
      client.invalidateQueries({ queryKey: ["runs", project.id] });
      onClose();
      navigate(`/app/runs/${run.id}`);
    },
    onError: (e: Error) => message.error(e.message),
  });

  const brainNote =
    brain === "auto" ? `Auto uses Claude when a key is configured on the server. Right now that means ${models.data?.auto === "claude" ? "Claude" : "no model"}.`
    : brain === "heuristic" ? "Detectors and form fuzzing only. Fast and free; finds crashes, dead controls and broken pages, not wrong totals."
    : brain === "claude" ? "Claude plans what to probe and drives goal-directed testers, on top of the detectors"
    : "A model on this machine plans and drives the goal-directed testers. Nothing leaves it and nothing is billed.";

  return (
    <Drawer title={`Start a run on ${project.name}`} open={open} onClose={onClose} size={560} destroyOnHidden
      footer={
        <div className="drawer-foot">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={start.isPending} disabled={!!provider && !provider.available} onClick={() => form.submit()}>Start run</Button>
        </div>
      }
    >
      <Form form={form} layout="vertical" requiredMark={false} initialValues={DEFAULTS}
        onFinish={(v) => start.mutate({ ...v, model: v.brain === "ollama" ? v.model : "", scenario_ids: v.kind === "scenarios" ? (v.scenario_ids?.length ? v.scenario_ids : scenarios.data?.map((s) => s.id) ?? []) : [] })}>
        <Form.Item name="kind" label="What to run" extra={KINDS.find((k) => k.value === kind)?.says}>
          <Segmented block options={KINDS.map(({ value, label }) => ({ value, label }))} />
        </Form.Item>

        {kind === "scenarios" ? (
          <Form.Item name="scenario_ids" label="Scenarios" extra={scenarios.data?.length ? `Leave empty to run all ${scenarios.data.length}` : "This app has no scenarios yet. Keep a finding as a scenario first."}>
            <Select mode="multiple" allowClear placeholder="All scenarios…" options={scenarios.data?.map((s) => ({ value: s.id, label: s.name }))} />
          </Form.Item>
        ) : (
          <>
            <Form.Item name="goal" label="What should the testers look into?" extra="Optional. Model-driven testers use it to decide what to probe.">
              <Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} placeholder="Quantities, prices and totals in the cart…" />
            </Form.Item>
            <Form.Item name="areas" label="Stay within" extra="Path prefixes. Leave empty to test the whole app.">
              <Select mode="tags" open={false} suffix={null} placeholder="/checkout" tokenSeparators={[",", " "]} />
            </Form.Item>
            <Form.Item name="devices" label="Devices" rules={[{ required: true, message: "Choose at least one device" }]}>
              <Checkbox.Group options={DEVICES.map((d) => ({ value: d, label: d }))} />
            </Form.Item>
          </>
        )}

        <Form.Item name="browser" label="Browser" extra={ENGINES.find((e) => e.value === engine)?.says}>
          <Segmented block options={ENGINES.map(({ value, label }) => ({ value, label }))} />
        </Form.Item>

        <Form.Item label="Testers at once" extra={plural(project.personas.length, "persona") + ": " + project.personas.map((p) => p.name).join(", ")}>
          <Form.Item name="testers" noStyle><Slider min={1} max={12} marks={{ 1: "1", 4: "4", 8: "8", 12: "12" }} /></Form.Item>
        </Form.Item>
        <div className="form-row">
          <Form.Item name="max_steps" label="Step budget"><InputNumber min={10} max={5000} step={50} style={{ width: "100%" }} /></Form.Item>
          <Form.Item name="max_minutes" label="Time limit, minutes"><InputNumber min={0.5} max={60} step={0.5} style={{ width: "100%" }} /></Form.Item>
          <Form.Item name="gate" label="Fail the run at"><Select options={["P0", "P1", "P2", "P3"].map((p) => ({ value: p, label: `${p} or worse` }))} /></Form.Item>
        </div>

        <Form.Item label="Who drives the testers">
          <Form.Item name="brain" noStyle>
            <Segmented block options={BRAINS.map((b) => ({ ...b, disabled: models.data ? !models.data.providers.find((p) => p.id === b.value || b.value === "auto")?.available && b.value !== "auto" : false }))} />
          </Form.Item>
          <p className="brain-note">{brainNote}</p>
          {provider && !provider.available && <Note type="warning" title={provider.reason} style={{ marginTop: 12 }} />}
                  </Form.Item>
        {brain === "ollama" && ollama?.available && (
          <Form.Item name="model" label="Ollama model" rules={[{ required: true, message: "Choose a model" }]} extra="Only models that can call tools are listed. Larger models reason better and answer slower.">
            <Select options={[...ollama.models].sort((a, b) => (a.size_gb ?? 0) - (b.size_gb ?? 0)).map((m) => ({ value: m.name, label: `${m.name}  (${[m.parameters, m.size_gb ? `${m.size_gb} GB` : ""].filter(Boolean).join(", ")})` }))} />
          </Form.Item>
        )}

        {kind !== "scenarios" && (
          <div className="form-row">
            <Form.Item name="verify_runs" label="Replays per finding"><InputNumber min={1} max={5} style={{ width: "100%" }} /></Form.Item>
            <Form.Item name="record" label="Record video and trace" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item name="minimize" label="Shorten reproductions" valuePropName="checked"><Switch /></Form.Item>
          </div>
        )}
      </Form>
    </Drawer>
  );
}
