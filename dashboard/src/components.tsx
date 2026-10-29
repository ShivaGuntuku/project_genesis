import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { get, go, type EventItem, type Message, type Thread } from "./api";

// ------------------------------------------------------------------ small pieces

export function Tile({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="card tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note !== undefined && <div className="note">{note}</div>}
    </div>
  );
}

export function Card({ title, hint, children }: { title: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <h2>{title}{hint && <span className="hint">{hint}</span>}</h2>
      {children}
    </section>
  );
}

export function Pager({ page, total, size, onPage }: { page: number; total: number; size: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="pager">
      <button disabled={page <= 0} onClick={() => onPage(page - 1)}>← Newer</button>
      <span>page {page + 1} of {pages} · {total.toLocaleString()} total</span>
      <button disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}>Older →</button>
    </div>
  );
}

export function AgentLink({ id, name }: { id: number; name: string }) {
  return <a href={`#/agent/${id}`}>{name}</a>;
}

export function Status({ error, loading, empty }: { error?: string | null; loading?: boolean; empty?: boolean }) {
  if (error) return <p className="err">Could not load: {error}</p>;
  if (empty && !loading) return <p className="dim">Nothing here yet.</p>;
  return null;
}

export const pct = (x: number) => `${Math.round(x * 100)}%`;

// ------------------------------------------------------------------ "why?" (global modal)

const WhyContext = createContext<(id: number) => void>(() => {});
export const useWhy = () => useContext(WhyContext);

export function WhyProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ id: number; chain: EventItem[]; consequences: EventItem[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const open = (id: number) => {
    setError(null);
    get<{ chain: EventItem[]; consequences: EventItem[] }>(`/api/why/${id}`)
      .then((d) => setState({ id, ...d }))
      .catch((e) => setError(String(e)));
  };
  return (
    <WhyContext.Provider value={open}>
      {children}
      {(state || error) && (
        <div className="modal-bg" onClick={() => { setState(null); setError(null); }}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Why did this happen?</h2>
            {error && <p className="err">{error}</p>}
            {state && (
              <>
                <p className="sub">Each step was caused by the one above it (oldest first).</p>
                <ul className="feed">
                  {state.chain.map((e) => (
                    <li key={e.id} className={e.god ? "god" : ""}><span className="when">{e.clock}</span>
                      {e.god && <><span className="tag god">GOD</span> </>}
                      {e.id === state.id ? <b>{e.text}</b> : e.text}</li>
                  ))}
                </ul>
                {state.consequences.length > 0 && (
                  <>
                    <h2 style={{ marginTop: 14 }}>What it led to</h2>
                    <ul className="feed">
                      {state.consequences.map((e) => (
                        <li key={e.id}><span className="when">{e.clock}</span>{e.text}
                          {" "}<a onClick={() => open(e.id)}>why?</a></li>
                      ))}
                    </ul>
                  </>
                )}
              </>
            )}
            <p><button onClick={() => { setState(null); setError(null); }}>Close</button></p>
          </div>
        </div>
      )}
    </WhyContext.Provider>
  );
}

export function EventRow({ e, names }: { e: EventItem; names?: Record<number, string> }) {
  const why = useWhy();
  void names;
  return (
    <li>
      <span className="when">{e.clock}</span>
      {e.god && <><span className="tag god">GOD</span> </>}
      {e.text.replace(" [LIE]", "")}
      {e.lie && <> <span className="tag lie">LIE</span></>}
      {e.thought && <> <span className="thought">(thinks: {e.thought})</span></>}
      {e.hasCause && <> <a onClick={() => why(e.id)}>why?</a></>}
    </li>
  );
}

// ------------------------------------------------------------------ conversations

const ACT_WORDS: Record<string, string> = {
  greet: "greets", chat: "talks", share_info: "tells where", request: "asks", give: "gives",
  gossip: "gossips", praise: "praises", insult: "insults", threaten: "threatens",
  reply: "answers", propose: "proposes a child", found: "founds a group", invite: "invites",
  order: "orders", challenge: "challenges", attack: "attacks",
};

function MessageView({ m }: { m: Message }) {
  const why = useWhy();
  return (
    <div className="msg">
      <div className="who"><AgentLink id={m.from} name={m.fromName} /></div>
      <div>
        <div className="line">
          {m.line ? <>“{m.line}”</> : <span className="ink2">{m.text.replace(" [LIE]", "")}</span>}
        </div>
        <div className="meta">
          <span className="tag">{ACT_WORDS[m.act] ?? m.act}{m.answer ? `: ${m.answer}` : ""}</span>
          {m.line && <span className="dim" style={{ fontSize: 12 }}>{m.text.replace(" [LIE]", "")}</span>}
          {!m.truthful && <span className="tag lie" title="The speaker knew better. The listener can't tell.">LIE</span>}
          {m.source === "rules" && <span className="tag" title="Decided by the rules, without the LLM">rules</span>}
          {m.thought && <span className="thought">thinks: {m.thought}</span>}
          <a style={{ fontSize: 12 }} onClick={() => why(m.id)}>why?</a>
        </div>
        {m.effects.length > 0 && (
          <div className="meta">
            {m.effects.map((f, i) => (
              <span key={i} className="effect">
                {f.who} → {f.about}: {f.dimension}{" "}
                <span className={f.delta >= 0 ? "up" : "down"}>{f.delta >= 0 ? "+" : ""}{f.delta.toFixed(2)}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function ThreadView({ t }: { t: Thread }) {
  return (
    <div className="thread">
      <header>
        <span className="dim">{t.clock}</span>
        <b><AgentLink id={t.a.id} name={t.a.name} /> ↔ <AgentLink id={t.b.id} name={t.b.name} /></b>
        <span className="dim">{t.messages.length} turn{t.messages.length === 1 ? "" : "s"}</span>
        <a style={{ marginLeft: "auto", fontSize: 12 }} onClick={() => go(`#/pair/${t.a.id}/${t.b.id}`)}>
          their whole story →
        </a>
      </header>
      {t.messages.map((m) => <MessageView key={m.id} m={m} />)}
    </div>
  );
}

// ------------------------------------------------------------------ LLM call inspector

export function CallModal({ id, onClose }: { id: number; onClose: () => void }) {
  const [call, setCall] = useState<Record<string, string | number | null> | null>(null);
  useEffect(() => {
    get<Record<string, string | number | null>>(`/api/llm/calls/${id}`).then(setCall).catch(() => onClose());
  }, [id, onClose]);
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {!call ? <p className="dim">Loading…</p> : (
          <>
            <h2>{String(call.kind)} · {String(call.clock)} · {String(call.model)} · {String(call.latency_ms)} ms · {String(call.outcome)}</h2>
            {call.error && <p className="err">{String(call.error)}</p>}
            <h2>Prompt</h2><pre>{String(call.prompt)}</pre>
            <h2>Answer (raw)</h2><pre>{String(call.response ?? "")}</pre>
          </>
        )}
        <p><button onClick={onClose}>Close</button></p>
      </div>
    </div>
  );
}
