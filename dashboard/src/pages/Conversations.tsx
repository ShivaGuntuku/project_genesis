import { query, setParams, useApi, type AgentRow, type Threads } from "../api";
import { Pager, Status, ThreadView } from "../components";

const ACTS = ["greet", "chat", "share_info", "request", "give", "gossip", "praise", "insult", "threaten",
  "reply", "propose", "found", "invite", "order", "challenge", "attack"];

/** Every conversation, filterable. State lives in the URL so views can be shared and kept. */
export default function Conversations({ params, base = "#/conversations", fixed = {} }: {
  params: URLSearchParams; base?: string; fixed?: Record<string, number>;
}) {
  const p = {
    agent: params.get("agent") ?? "", other: params.get("other") ?? "", act: params.get("act") ?? "",
    source: params.get("source") ?? "", lies: params.get("lies") === "true", q: params.get("q") ?? "",
    day_from: params.get("day_from") ?? "", day_to: params.get("day_to") ?? "",
    page: Number(params.get("page") ?? 0),
  };
  const agents = useApi<AgentRow[]>("/api/agents", false);
  const { data, error, loading } = useApi<Threads>("/api/conversations" + query({ ...p, ...fixed }));
  const update = (change: Partial<typeof p>) => setParams(base, { ...p, page: 0, ...change });
  const people = agents.data ?? [];
  const pick = (value: string, set: (v: string) => void, label: string) => (
    <select value={value} onChange={(e) => set(e.target.value)} aria-label={label}>
      <option value="">{label}: anyone</option>
      {people.map((a) => <option key={a.id} value={a.id}>{a.name}{a.alive ? "" : " †"}</option>)}
    </select>
  );
  return (
    <>
      {!fixed.agent && (
        <>
          <h1>Conversations</h1>
          <p className="sub">
            Every message, grouped into conversations (the same two people in the same hour). Under each line:
            the speech act, the speaker's private thought, lies, and how feelings moved because of it.
          </p>
        </>
      )}
      <div className="filters">
        {!fixed.agent && pick(p.agent, (v) => update({ agent: v }), "Person")}
        {!fixed.other && pick(p.other, (v) => update({ other: v }), "With")}
        <select value={p.act} onChange={(e) => update({ act: e.target.value })} aria-label="Act">
          <option value="">Any act</option>
          {ACTS.map((a) => <option key={a} value={a}>{a.replace("_", " ")}</option>)}
        </select>
        <select value={p.source} onChange={(e) => update({ source: e.target.value })} aria-label="Source">
          <option value="">Own words or rules</option>
          <option value="llm">Own words (LLM)</option>
          <option value="rules">Rules only</option>
        </select>
        <label><input type="checkbox" checked={p.lies} onChange={(e) => update({ lies: e.target.checked })} /> lies only</label>
        <input placeholder="words said…" value={p.q} onChange={(e) => update({ q: e.target.value })} style={{ width: 160 }} />
        <label>days <input type="number" min={1} value={p.day_from} placeholder="from" style={{ width: 70 }}
          onChange={(e) => update({ day_from: e.target.value })} />
          – <input type="number" min={1} value={p.day_to} placeholder="to" style={{ width: 70 }}
          onChange={(e) => update({ day_to: e.target.value })} /></label>
      </div>
      <Status error={error} loading={loading} empty={data?.threads.length === 0} />
      {data && data.total > 0 && <Pager page={p.page} total={data.total} size={data.pageSize} onPage={(n) => setParams(base, { ...p, page: n })} />}
      <div style={{ opacity: loading ? 0.6 : 1 }}>
        {data?.threads.map((t) => <ThreadView key={t.key} t={t} />)}
      </div>
      {data && data.total > data.pageSize && <Pager page={p.page} total={data.total} size={data.pageSize} onPage={(n) => setParams(base, { ...p, page: n })} />}
    </>
  );
}
