import { useState } from "react";
import { useApi, type EventItem } from "../api";
import { Card, EventRow, Status } from "../components";

interface Queued { id: number; created: string; command: Record<string, unknown>; applied: string | null; event: number | null; error: string | null }
interface GodData { queue: Queued[]; done: EventItem[]; commands: string[]; valleys: string[]; running: boolean }

const FIELDS: Record<string, [string, string][]> = {
  add_food: [["x", "x"], ["y", "y"], ["regen", "growth (1-5)"]],
  remove_food: [["food_id", "patch id"]],
  lake: [["x", "x"], ["y", "y"], ["size", "size"]],
  dry: [["x", "x"], ["y", "y"], ["size", "size"]],
  disaster: [["kind", "kind"], ["region", "valley"], ["days", "days"]],
  migrants: [["count", "how many"], ["region", "valley"]],
  strike: [["agent_id", "person id"]],
  whisper: [["agent_id", "person id"], ["text", "words"]],
};
const KINDS = ["drought", "winter", "flood", "sickness", "predators", "wildfire"];

export default function God() {
  const { data, error, loading } = useApi<GodData>("/api/god");
  const [act, setAct] = useState("disaster");
  const [form, setForm] = useState<Record<string, string>>({ kind: "drought", region: "all" });
  const [note, setNote] = useState<string | null>(null);
  if (!data) return <Status error={error} loading={loading} />;
  const send = async () => {
    const body: Record<string, unknown> = { act };
    for (const [k] of FIELDS[act]) {
      const v = form[k];
      if (v === undefined || v === "") continue;
      body[k] = ["x", "y", "regen", "size", "days", "count", "agent_id", "food_id"].includes(k) ? Number(v) : v;
    }
    const r = await fetch("/api/god", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    setNote(r.ok ? `Queued (#${j.queued}). ${data.running ? "It happens at the run's next daily save." : "It happens when the world next runs (genesis resume / run)."}` : j.detail ?? "Not sent");
  };
  return (
    <>
      <h1>God Mode</h1>
      <p className="sub">
        Change the world as its creator. Every change is an event with origin "god", shown in every cause chain; the
        people never learn who did it. Commands are queued and applied by the running world at its next daily save. To
        try something without touching this world, branch it first: <code>genesis branch WORLD NEW</code>.
      </p>
      <div className="grid two">
        <Card title="Intervene">
          <div style={{ display: "grid", gap: 8 }}>
            <label>Command <select value={act} onChange={(e) => { setAct(e.target.value); setForm({}); }}>
              {data.commands.map((c) => <option key={c}>{c}</option>)}
            </select></label>
            {FIELDS[act]?.map(([k, label]) => (
              <label key={k}>{label}{" "}
                {k === "kind" ? (
                  <select value={form.kind ?? ""} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                    <option value="">choose</option>{KINDS.map((x) => <option key={x}>{x}</option>)}
                  </select>
                ) : k === "region" ? (
                  <select value={form.region ?? ""} onChange={(e) => setForm({ ...form, region: e.target.value })}>
                    {act === "disaster" && <option value="all">all</option>}
                    {data.valleys.map((v) => <option key={v}>{v}</option>)}
                  </select>
                ) : (
                  <input value={form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} style={{ width: k === "text" ? 320 : 90 }} />
                )}
              </label>
            ))}
            <div><button onClick={send}>Queue it</button></div>
            {note && <p className="dim">{note}</p>}
          </div>
        </Card>
        <Card title="Queue" hint="newest first">
          {data.queue.length === 0 ? <p className="dim">Nothing queued yet.</p> : (
            <table className="data"><tbody>{data.queue.map((q) => (
              <tr key={q.id}><td className="num">#{q.id}</td><td><code>{JSON.stringify(q.command)}</code></td>
                <td>{q.error ? <span className="err">{q.error}</span> : q.applied ? `done ${q.applied}` : "waiting"}</td></tr>
            ))}</tbody></table>
          )}
        </Card>
      </div>
      <div style={{ marginTop: 14 }}>
        <Card title="What God has done">
          {data.done.length === 0 ? <p className="dim">Nothing yet: the world has been left to itself.</p> : (
            <ul className="feed">{data.done.map((e) => <EventRow key={e.id} e={e} />)}</ul>
          )}
        </Card>
      </div>
    </>
  );
}
