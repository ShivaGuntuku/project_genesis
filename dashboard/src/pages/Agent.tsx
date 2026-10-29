import { useState } from "react";
import { query, setParams, useApi, type AgentDetail, type EventItem, type LlmCall } from "../api";
import { AgentLink, CallModal, Card, EventRow, Pager, Status, Tile } from "../components";
import Conversations from "./Conversations";

const TABS = ["overview", "mind", "relationships", "conversations", "thoughts", "memories", "genes", "timeline", "interview"];

export default function Agent({ id, tab = "overview", params }: { id: number; tab?: string; params: URLSearchParams }) {
  const { data, error, loading } = useApi<AgentDetail>(`/api/agents/${id}`);
  if (!data) return <Status error={error} loading={loading} />;
  const a = data;
  return (
    <>
      <h1>{a.name}{a.alive ? "" : " †"}</h1>
      <p className="sub">
        {a.alive ? `${a.stage}, ${a.ageDays} days old` : `died ${a.died} of ${a.causeOfDeath}, aged ${a.ageDays} days`}
        {" "}· generation {a.generation}{a.born ? ` · born ${a.born}` : " · founder"}
        {a.pregnant && <> · <span className="tag">expecting a child</span></>}
      </p>
      <div className="tabs">
        {TABS.map((t) => <a key={t} href={`#/agent/${id}/${t}`} className={t === tab ? "active" : ""}>{t}</a>)}
      </div>
      {tab === "overview" && <OverviewTab a={a} />}
      {tab === "mind" && <MindTab a={a} />}
      {tab === "relationships" && <RelationsTab a={a} />}
      {tab === "conversations" && <Conversations params={params} base={`#/agent/${id}/conversations`} fixed={{ agent: id }} />}
      {tab === "thoughts" && <ThoughtsTab id={id} params={params} />}
      {tab === "memories" && <MemoriesTab id={id} params={params} />}
      {tab === "genes" && <GenesTab a={a} />}
      {tab === "timeline" && <TimelineTab id={id} params={params} />}
      {tab === "interview" && <InterviewTab id={id} name={a.name} />}
    </>
  );
}

function OverviewTab({ a }: { a: AgentDetail }) {
  return (
    <>
      <div className="grid tiles">
        {Object.entries(a.needs).map(([k, v]) => <Tile key={k} label={k} value={v} />)}
        <Tile label="Carrying" value={`${a.carrying.food ?? 0} / ${a.carrying.water ?? 0}`} note="food units / drinks" />
        <Tile label="Messages sent" value={a.said.messages} note={`${a.said.voiced} in own words · ${a.said.lies} lies`} />
      </div>
      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="In their own words" hint="self-story from the latest nightly reflection">
          {a.story ? <p style={{ fontStyle: "italic", fontSize: 15 }}>“{a.story}”</p> : <p className="dim">No reflection yet (needs the LLM mind).</p>}
          {a.talent && <p style={{ marginTop: 10 }}>Talent: <b>{a.talent}</b>{Object.keys(a.work).length ? <span className="dim"> · work lately: {Object.entries(a.work).sort((x, y) => y[1] - x[1]).map(([k, v]) => `${k} ${v}`).join(", ")}</span> : null}</p>}
          {a.owes.length > 0 && <p>Owes: {a.owes.map((d, i) => <span key={i}>{d.good} to <a href={`#/agent/${d.to}`}>{d.name}</a>{i < a.owes.length - 1 ? ", " : ""}</span>)}</p>}
          {a.goal && <p style={{ marginTop: 10 }}>Goal{a.goal.ownWords ? " (own words)" : ""}: <b>“{a.goal.text}”</b> <span className="dim">· {a.goal.need} · since {a.goal.since}</span></p>}
          {a.goals.length > 0 && (
            <ul className="feed" style={{ maxHeight: 160, overflow: "auto", marginTop: 8 }}>
              {a.goals.slice().reverse().map((g) => <li key={g.eventId}><span className="dim">{g.clock}</span> {g.text}</li>)}
            </ul>
          )}
        </Card>
        <Card title="Family">
          {a.family.length === 0 ? <p className="dim">No family.</p> : (
            <table className="data"><tbody>{a.family.map((f) => (
              <tr key={f.id} className={f.alive ? "" : "dead"}><td>{f.relation}</td>
                <td><AgentLink id={f.id} name={f.name} />{f.alive ? "" : " †"}</td></tr>
            ))}</tbody></table>
          )}
        </Card>
      </div>
    </>
  );
}

function MindTab({ a }: { a: AgentDetail }) {
  return (
    <Card title="How their mind changed" hint="each night's reflection: new beliefs and the self-story, oldest first">
      {a.stories.length === 0 ? <p className="dim">No reflections yet (needs the LLM mind).</p> :
        a.stories.map((s) => (
          <div key={s.eventId} className="story-step">
            <div className="dim" style={{ fontSize: 12 }}>{s.clock}{s.thought ? ` · feels: ${s.thought}` : ""}</div>
            {s.beliefs.map((b, i) => <div key={i}>• <b>{b}</b></div>)}
            {s.story && <div className="ink2" style={{ fontStyle: "italic" }}>“{s.story}”</div>}
          </div>
        ))}
    </Card>
  );
}

function RelationsTab({ a }: { a: AgentDetail }) {
  const dims = ["affinity", "trust", "gratitude", "grievance", "fear", "respect"] as const;
  return (
    <div className="card" style={{ overflowX: "auto" }}>
      <table className="data">
        <thead><tr><th>Person</th><th>I see them as</th><th>They see me as</th>
          {dims.map((d) => <th key={d} className="num">{d}</th>)}<th>Family</th><th></th></tr></thead>
        <tbody>{a.relationships.map((r) => (
          <tr key={r.id} className={r.alive ? "" : "dead"}>
            <td><AgentLink id={r.id} name={r.name} />{r.alive ? "" : " †"}</td>
            <td>{r.label}</td><td className="dim">{r.theirLabel ?? "–"}</td>
            {dims.map((d) => <td key={d} className="num">{r[d] >= 0 && d === "affinity" ? "+" : ""}{r[d].toFixed(2)}</td>)}
            <td>{r.family ?? ""}</td>
            <td><a href={`#/pair/${a.id}/${r.id}`}>history →</a></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function ThoughtsTab({ id, params }: { id: number; params: URLSearchParams }) {
  const page = Number(params.get("page") ?? 0);
  const { data, error, loading } = useApi<{ total: number; pageSize: number; rows: LlmCall[] }>(
    `/api/llm/calls${query({ agent: id, page })}`);
  const [open, setOpen] = useState<number | null>(null);
  if (!data) return <Status error={error} loading={loading} />;
  return (
    <>
      <p className="sub">Every call to the local model for this agent: what they chose, said and privately thought. Click for the full prompt.</p>
      <Status empty={data.rows.length === 0} />
      <Pager page={page} total={data.total} size={data.pageSize} onPage={(n) => setParams(`#/agent/${id}/thoughts`, { page: n })} />
      <div className="card"><table className="data">
        <thead><tr><th>When</th><th>Kind</th><th>With</th><th>Choice</th><th>Said</th><th>Thought</th><th className="num">ms</th></tr></thead>
        <tbody>{data.rows.map((c) => (
          <tr key={c.id} onClick={() => setOpen(c.id)} style={{ cursor: "pointer" }}>
            <td className="dim">{c.clock}</td><td>{c.kind}</td><td>{c.other ?? ""}</td>
            <td>{c.outcome === "ok" ? (c.choice ?? "").split(":")[0] : <span className="tag bad">{c.outcome}</span>}</td>
            <td>{c.say ? `“${c.say}”` : <span className="dim">{c.error}</span>}</td>
            <td className="thought">{c.thought}</td><td className="num">{c.ms}</td>
          </tr>
        ))}</tbody>
      </table></div>
      {open !== null && <CallModal id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function MemoriesTab({ id, params }: { id: number; params: URLSearchParams }) {
  const p = { kind: params.get("kind") ?? "", q: params.get("q") ?? "", forgotten: params.get("forgotten") === "true", page: Number(params.get("page") ?? 0) };
  const { data, error, loading } = useApi<{ total: number; pageSize: number; rows: { id: number; clock: string; kind: string; text: string; importance: number; forgotten: string | null; eventId: number | null }[] }>(
    `/api/agents/${id}/memories${query(p)}`);
  const base = `#/agent/${id}/memories`;
  return (
    <>
      <div className="filters">
        <select value={p.kind} onChange={(e) => setParams(base, { ...p, kind: e.target.value, page: 0 })}>
          <option value="">All kinds</option>
          {["observation", "conversation", "told", "belief", "summary"].map((k) => <option key={k}>{k}</option>)}
        </select>
        <input placeholder="search memories…" value={p.q} onChange={(e) => setParams(base, { ...p, q: e.target.value, page: 0 })} />
        <label><input type="checkbox" checked={p.forgotten} onChange={(e) => setParams(base, { ...p, forgotten: e.target.checked, page: 0 })} /> include forgotten</label>
      </div>
      <Status error={error} loading={loading} empty={data?.rows.length === 0} />
      {data && <Pager page={p.page} total={data.total} size={data.pageSize} onPage={(n) => setParams(base, { ...p, page: n })} />}
      {data && <div className="card"><table className="data">
        <thead><tr><th>When</th><th className="num">Importance</th><th>Kind</th><th>Memory</th></tr></thead>
        <tbody>{data.rows.map((m) => (
          <tr key={m.id} className={m.forgotten ? "dead" : ""}>
            <td className="dim">{m.clock}</td><td className="num">{m.importance}</td><td>{m.kind}</td>
            <td>{m.kind === "belief" ? <b>{m.text}</b> : m.text}{m.forgotten && <span className="dim"> (forgotten {m.forgotten})</span>}</td>
          </tr>
        ))}</tbody>
      </table></div>}
    </>
  );
}

function GenesTab({ a }: { a: AgentDetail }) {
  return (
    <Card title="Genes" hint={a.parentGenes.length ? "inherited: each gene from one parent or their average, plus a small mutation" : "founder: random at the start"}>
      <table className="data">
        <thead><tr><th>Gene</th><th>Value</th>{a.parentGenes.map((p) => <th key={p.name}>from {p.name}</th>)}</tr></thead>
        <tbody>{Object.entries(a.genes).map(([g, v]) => (
          <tr key={g}><td>{g}</td>
            <td><div className="bar-cell"><div className="bar" style={{ width: `${v * 140}px` }} /><span>{v.toFixed(2)}</span></div></td>
            {a.parentGenes.map((p) => (
              <td key={p.name}><div className="bar-cell"><div className="bar p" style={{ width: `${(p.genes[g] ?? 0) * 100}px` }} />
                <span className="dim">{(p.genes[g] ?? 0).toFixed(2)}</span></div></td>
            ))}
          </tr>
        ))}</tbody>
      </table>
      {Object.keys(a.skills).length > 0 && (
        <>
          <h2 style={{ marginTop: 16 }}>Skills (learned)</h2>
          <div className="words">{Object.entries(a.skills).filter(([, v]) => v >= 0.05).map(([k, v]) => (
            <span key={k} className="word">{k.replace("_", " ")}<i>{v.toFixed(2)}</i></span>))}</div>
        </>
      )}
    </Card>
  );
}

function TimelineTab({ id, params }: { id: number; params: URLSearchParams }) {
  const page = Number(params.get("page") ?? 0);
  const major = params.get("major") === "true";
  const { data, error, loading } = useApi<EventItem[]>(`/api/agents/${id}/timeline${query({ page, major })}`);
  const base = `#/agent/${id}/timeline`;
  return (
    <>
      <div className="filters">
        <label><input type="checkbox" checked={major} onChange={(e) => setParams(base, { major: e.target.checked })} /> life events only</label>
        <button disabled={page === 0} onClick={() => setParams(base, { major, page: page - 1 })}>← Newer</button>
        <button disabled={!data || data.length < 50} onClick={() => setParams(base, { major, page: page + 1 })}>Older →</button>
      </div>
      <Status error={error} loading={loading} empty={data?.length === 0} />
      <div className="card"><ul className="feed">{data?.map((e) => <EventRow key={e.id} e={e} />)}</ul></div>
    </>
  );
}


interface Answer {
  name: string; year: number; asOf: string; question: string; answer: string | null; thought: string;
  leaked: string[]; memoriesUsed: number; beliefsUsed: number; prompt: string;
}

function InterviewTab({ id, name }: { id: number; name: string }) {
  const [question, setQuestion] = useState("");
  const [year, setYear] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<Answer[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const ask = async () => {
    setBusy(true); setErr(null);
    const r = await fetch(`/api/agents/${id}/interview`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, year: year ? Number(year) : null }),
    });
    const j = await r.json().catch(() => ({}));
    setBusy(false);
    if (r.ok) { setLog([j, ...log]); setQuestion(""); } else setErr(j.detail ?? "No answer");
  };
  return (
    <Card title={`Interview ${name}`} hint="answers come only from their own memories up to that year; the world never knows">
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What do you remember of your mother?"
          style={{ flex: "1 1 320px" }} onKeyDown={(e) => e.key === "Enter" && question && !busy && ask()} />
        <input value={year} onChange={(e) => setYear(e.target.value)} placeholder="as of year (blank: now)" style={{ width: 170 }} />
        <button onClick={ask} disabled={!question || busy}>{busy ? "thinking…" : "Ask"}</button>
      </div>
      {err && <p className="err">{err}</p>}
      {log.map((a, i) => (
        <div key={i} style={{ marginTop: 14 }}>
          <p className="dim">{a.asOf} · {a.memoriesUsed} memories, {a.beliefsUsed} beliefs</p>
          <p><b>Q:</b> {a.question}</p>
          {a.answer ? <p style={{ fontStyle: "italic" }}>“{a.answer}”</p>
            : <p className="err">The answer used words from outside the valley ({a.leaked.join(", ")}) and was not kept.</p>}
          <details><summary className="dim">thought, and the prompt they were given</summary>
            <p className="dim">(thinks: {a.thought})</p><pre style={{ whiteSpace: "pre-wrap" }}>{a.prompt}</pre></details>
        </div>
      ))}
    </Card>
  );
}
