import { useMutation, useQueryClient } from "@tanstack/react-query";
import { App, Button, Drawer, Form, Input, Segmented } from "antd";
import { useNavigate } from "react-router-dom";
import { api, type NewProject } from "../api";

const POLICIES = [
  { value: "read_only", label: "Look only", says: "Testers navigate and search. They never submit anything that changes data." },
  { value: "safe_writes", label: "Normal use", says: "Testers do what a user does: add to cart, submit forms. They never delete an account or change a password." },
  { value: "full", label: "Anything", says: "No limits. Use only against an environment you can reset." },
];

export function ProjectForm({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form] = Form.useForm<NewProject>();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const client = useQueryClient();
  const policy = Form.useWatch("policy", form) ?? "safe_writes";
  const add = useMutation({
    mutationFn: api.createProject,
    onSuccess: (project) => {
      client.invalidateQueries({ queryKey: ["projects"] });
      onClose();
      form.resetFields();
      navigate(`/app/p/${project.id}/runs`);
    },
    onError: (e: Error) => message.error(e.message),
  });

  return (
    <Drawer title="Add an app to test" open={open} onClose={onClose} size={520}
      footer={
        <div className="drawer-foot">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={add.isPending} onClick={() => form.submit()}>Add app</Button>
        </div>
      }
    >
      <Form form={form} layout="vertical" requiredMark={false} onFinish={add.mutate}
        initialValues={{ name: "", base_url: "", login_path: "/login", username: "", password: "", policy: "safe_writes", knowledge: "", reset_path: "" }}>
        <Form.Item name="name" label="Name" rules={[{ required: true, message: "Give the app a name" }]}><Input placeholder="Storefront, staging…" autoComplete="off" /></Form.Item>
        <Form.Item name="base_url" label="Address" extra="The app must already be running there"
          rules={[{ required: true, message: "Enter the address the app runs at" }, { type: "url", message: "Enter a full address, starting with http:// or https://" }]}>
          <Input type="url" inputMode="url" placeholder="http://localhost:3000" autoComplete="off" spellCheck={false} autoCapitalize="none" />
        </Form.Item>
        <Form.Item name="policy" label="What testers may do" extra={POLICIES.find((p) => p.value === policy)?.says}>
          <Segmented block options={POLICIES.map(({ value, label }) => ({ value, label }))} />
        </Form.Item>
        <Form.Item label="Test account" extra="Optional. With one, a signed-in tester runs beside the signed-out visitor. Use an account made for testing; the password is stored encrypted.">
          <div className="form-row two">
            <Form.Item name="username" noStyle><Input placeholder="Username…" aria-label="Test account username" autoComplete="off" spellCheck={false} autoCapitalize="none" /></Form.Item>
            <Form.Item name="password" noStyle><Input.Password placeholder="Password…" aria-label="Test account password" autoComplete="new-password" /></Form.Item>
          </div>
        </Form.Item>
        <Form.Item name="login_path" label="Sign-in page"><Input autoComplete="off" spellCheck={false} autoCapitalize="none" /></Form.Item>
        <Form.Item name="reset_path" label="Reset endpoint" extra="Optional. A path on the app that puts its test data back. It is called before every replay, so a reproduction never leans on what an earlier one left behind.">
          <Input placeholder="/api/test/reset" autoComplete="off" spellCheck={false} autoCapitalize="none" />
        </Form.Item>
        <Form.Item name="knowledge" label="Notes for the testers" extra="Optional. Anything a new tester would need to be told: what the app is for, what matters most.">
          <Input.TextArea autoSize={{ minRows: 3, maxRows: 8 }} />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
