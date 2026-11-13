// Who is on this server, and the code a colleague joins with. Only a team server
// has this page.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Popconfirm, Skeleton } from "antd";
import type { Meta } from "../api";
import { api } from "../api";
import { Copy } from "../components/controls";
import { Note } from "../components/marks";
import { ago, plural } from "../format";

export function TeamPage({ meta }: { meta: Meta }) {
  const { message } = App.useApp();
  const client = useQueryClient();
  const team = useQuery({ queryKey: ["team"], queryFn: api.team });
  const refresh = () => client.invalidateQueries({ queryKey: ["team"] });
  const fail = (e: Error) => message.error(e.message);
  const recode = useMutation({ mutationFn: api.newTeamCode, onSuccess: refresh, onError: fail });
  const remove = useMutation({ mutationFn: api.removeMember, onSuccess: refresh, onError: fail });
  const owner = meta.user?.role === "owner";
  const joinLink = `${meta.url}/app/join`;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1 className="title">Team</h1>
          <p className="subtitle">Everyone here shares the same apps, runs and findings. {owner ? "You are the owner: you hand out the team code and can remove people." : "The owner hands out the team code and can remove people."}</p>
        </div>
      </div>
      {team.isError && <Note type="error" title={<>{(team.error as Error).message} <button className="link" onClick={() => team.refetch()}>Try again</button></>} />}
      {team.isLoading && <div className="sheet sheet-body"><Skeleton active paragraph={{ rows: 3 }} /></div>}
      {team.data && owner && (
        <section className="sheet" aria-labelledby="code-title">
          <div className="sheet-head"><h2 id="code-title">Team code</h2></div>
          <div className="sheet-body invite">
            <div>
              <p className="invite-code" translate="no">{team.data.code}</p>
              <p className="muted">A colleague creates their own account with this code at</p>
              <div className="cmd"><code translate="no">{joinLink}</code><Copy text={joinLink} /></div>
            </div>
            <div className="invite-actions">
              <Copy text={team.data.code} label="Copy the code" />
              <Popconfirm title="Make a new code?" description="The current code stops working. People who already joined stay." okText="Make a new code" onConfirm={() => recode.mutate()}>
                <Button loading={recode.isPending}>Make a new code</Button>
              </Popconfirm>
            </div>
          </div>
        </section>
      )}
      {team.data && (
        <section className="sheet" aria-labelledby="people-title">
          <div className="sheet-head"><h2 id="people-title">People</h2><span className="tag">{plural(team.data.members.length, "person", "people")}</span></div>
          <ul className="plain people">
            {team.data.members.map((m) => (
              <li key={m.id}>
                <span className="ellipsis"><strong>{m.name}</strong>{m.you && <span className="faint"> (you)</span>}</span>
                <span className="muted ellipsis">{m.email}</span>
                <span className="tag">{m.role === "owner" ? "Owner" : "Member"}</span>
                <span className="faint">Joined {ago(m.created_at)}</span>
                {owner && !m.you ? (
                  <Popconfirm title={`Remove ${m.name}?`} description="Their sign-ins, keys and connectors stop working at once. Runs they started stay." okText="Remove" okButtonProps={{ danger: true }} onConfirm={() => remove.mutate(m.id)}>
                    <Button size="small">Remove</Button>
                  </Popconfirm>
                ) : <span />}
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
