import { useState } from "react";
import { useApi } from "../api";
import { Card, Status, Tile } from "../components";

interface RunStatus {
  state?: string; world?: string; clock?: string; target?: string; alive?: number; llm?: boolean;
  ticks_per_second?: number; eta_hours?: number | null; updated?: string; pid?: number | null;
  control?: string | null; snapshots?: string[]; resumed?: number;
}
const EXPORTS: [string, string][] = [
  ["agents", "Everyone who ever lived"], ["events", "Every event"], ["messages", "Every message"],
  ["llm_calls", "Every LLM call"], ["ideas", "Every invention"], ["yearly", "Yearly snapshots"],
  ["metrics", "Daily numbers"],
];

export default function Run() {
  const { data, error, loading } = useApi<RunStatus>("/api/run");
  const [note, setNote] = useState<string | null>(null);
  if (!data) return <Status error={error} loading={loading} />;
  const send = async (command: string) => {
    const r = await fetch(`/api/run/${command}`, { method: "POST" });
    const body = await r.json().catch(() => ({}));
    setNote(r.ok ? `Asked the run to ${command}; it does so at its next daily save.` : body.detail ?? "Could not send");
  };
  const has = Boolean(data.state);
  return (
    <>
      <h1>The long run</h1>
      <p className="sub">
        A long run saves once a simulated day, writes every LLM answer to a journal the moment it arrives, and takes a
        snapshot every hour. After a crash or a reboot it resumes from where it was, with the same history.
      </p>
      {!has ? (
        <p className="dim">This world has no long run. Start one with <code>genesis start WORLD --years N</code>.</p>
      ) : (
        <>
          <div className="grid tiles">
            <Tile label="State" value={data.state} note={data.control === "pause" && data.state !== "paused" ? "pausing…" : data.llm ? "with the LLM" : "rules only"} />
            <Tile label="Now" value={data.clock ?? "–"} note={`target: ${data.target ?? "–"}`} />
            <Tile label="Alive" value={data.alive ?? "–"} />
            <Tile label="Speed" value={data.ticks_per_second ? `${data.ticks_per_second} watches/s` : "–"}
              note={data.eta_hours != null ? `about ${data.eta_hours} h to go` : ""} />
          </div>
          <div style={{ marginTop: 14 }}>
            <Card title="Control" hint={`last update ${data.updated ?? "–"}${data.resumed ? ` · resumed with ${data.resumed} journaled answers` : ""}`}>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={() => send("pause")} disabled={data.state !== "running"}>Pause</button>
                <button onClick={() => send("continue")} disabled={data.state !== "paused"}>Continue</button>
                <button onClick={() => send("stop")} disabled={!["running", "paused"].includes(data.state ?? "")}>Stop</button>
              </div>
              {note && <p className="dim" style={{ marginTop: 8 }}>{note}</p>}
              <p className="dim" style={{ marginTop: 8 }}>
                Snapshots kept: {data.snapshots?.length ? data.snapshots.join(", ") : "none yet"}
              </p>
            </Card>
          </div>
        </>
      )}
      <Sql />
      <div style={{ marginTop: 14 }}>
        <Card title="Export" hint="CSV files of the whole history, to study elsewhere">
          <ul>{EXPORTS.map(([name, label]) => (
            <li key={name}><a href={`/api/export/${name}.csv`}>{label}</a> <span className="dim">({name}.csv)</span></li>
          ))}</ul>
        </Card>
      </div>
    </>
  );
}

function Sql() {
  const [query, setQuery] = useState("SELECT type, COUNT(*) AS n FROM events GROUP BY type ORDER BY n DESC");
  const [out, setOut] = useState<{ columns: string[]; rows: unknown[][]; more: boolean } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setErr(null);
    const r = await fetch("/api/sql", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query }) });
    const j = await r.json().catch(() => ({}));
    if (r.ok) setOut(j); else { setOut(null); setErr(j.detail ?? "error"); }
  };
  return (
    <div style={{ marginTop: 14 }}>
      <Card title="Audit (read-only SQL)" hint="one SELECT at a time, up to 500 rows, 5 seconds; the world file is opened read-only">
        <textarea value={query} onChange={(e) => setQuery(e.target.value)} rows={3} style={{ width: "100%", fontFamily: "monospace" }} />
        <div><button onClick={run}>Run</button></div>
        {err && <p className="err">{err}</p>}
        {out && (
          <div style={{ overflow: "auto", maxHeight: 360, marginTop: 8 }}>
            <table className="data"><thead><tr>{out.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>{out.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>)}</tbody></table>
            {out.more && <p className="dim">More than 500 rows: showing the first 500.</p>}
          </div>
        )}
      </Card>
    </div>
  );
}
