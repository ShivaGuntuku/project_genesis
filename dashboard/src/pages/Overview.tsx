import { useState } from "react";
import { useApi, type Overview as O } from "../api";
import { LineChart, series } from "../chart";
import { Card, EventRow, pct, Status, Tile } from "../components";

export default function Overview() {
  const { data, error, loading } = useApi<O>("/api/overview");
  if (!data) return <Status error={error} loading={loading} />;
  const d = data;
  const llmOk = d.llm.total ? (d.llm.outcomes.ok ?? 0) / d.llm.total : 0;
  const deaths = Object.entries(d.people.deaths).map(([k, v]) => `${v} ${k}`).join(", ");
  return (
    <>
      <h1>{d.world}</h1>
      <p className="sub">
        {d.clock} · tick {d.tick.toLocaleString()} · seed {d.seed} ·{" "}
        {d.running ? <span className="tag good">● running</span> : <span className="tag">stopped</span>}{" "}
        · LLM mind {d.llmEnabled ? "on" : "off"} · saved {d.savedAt?.replace("T", " ").replace("+00:00", " UTC")}
      </p>
      <div className="grid tiles">
        <Tile label="Alive" value={d.people.alive} note={`${d.people.ever} ever lived`} />
        <Tile label="Generations" value={d.people.generations} note={`${d.people.born} born in the valley`} />
        <Tile label="Deaths" value={d.people.ever - d.people.alive} note={deaths || "none yet"} />
        <Tile label="Messages" value={d.messages.total.toLocaleString()} note={`${d.messages.lastDay} in the last day`} />
        <Tile label="In their own words" value={d.messages.total ? pct(d.messages.voiced / d.messages.total) : "–"}
          note={`${d.messages.voiced.toLocaleString()} spoken lines`} />
        <Tile label="Lies told" value={d.messages.lies} note={d.messages.total ? `${pct(d.messages.lies / d.messages.total)} of messages` : ""} />
        <Tile label="LLM answers valid" value={d.llm.total ? pct(llmOk) : "–"} note={`${d.llm.total.toLocaleString()} calls`} />
        <Tile label="LLM speed" value={d.llm.recentMs ? `${Math.round(d.llm.recentMs)} ms` : "–"} note="last 100 calls" />
        <Tile label="Groups" value={d.conflict.groups} note={`${d.conflict.groupsEver} ever founded`} />
        <Tile label="Fights" value={d.conflict.fights} note={d.conflict.killed ? `${d.conflict.killed} killed` : "nobody killed"} />
        <Tile label="World file" value={`${(d.dbBytes / 1e6).toFixed(1)} MB`} />
      </div>
      <Health />
      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="Population" hint="alive at the end of each day">
          <LineChart loading={loading} lines={[{ name: "alive", color: series(1), area: true,
            data: d.population.map((p) => [p.day, p.alive]) }]} />
        </Card>
        <Card title="What happened lately" hint="births, deaths, beliefs, lies found out, feelings">
          <ul className="feed" style={{ maxHeight: 260, overflow: "auto" }}>
            {d.feed.map((e) => <EventRow key={e.id} e={e} />)}
          </ul>
        </Card>
      </div>
    </>
  );
}

interface HealthData {
  llm: boolean; ollama: boolean | null; callsPerMinute: number; latencyMs: number | null; failed: number; dbMb: number; diskFreeGb: number; limit: number;
  gpu: { name: string; tempC: number; memMb: number; memTotalMb: number; loadPct: number } | null;
}
interface RunData { state?: string; clock?: string; target?: string; eta_hours?: number | null }

function Health() {
  const h = useApi<HealthData>("/api/health");
  const run = useApi<RunData>("/api/run");
  const [note, setNote] = useState<string | null>(null);
  const send = async (c: string) => {
    const r = await fetch(`/api/run/${c}`, { method: "POST" });
    const j = await r.json().catch(() => ({}));
    setNote(r.ok ? `Asked the run to ${c} (at its next daily save).` : j.detail ?? "not sent");
  };
  if (!h.data) return null;
  const d = h.data, st = run.data?.state;
  return (
    <div style={{ marginTop: 14 }}>
      <Card title="Health and the run" hint={st ? `long run: ${st}${run.data?.target ? ` → ${run.data.target}` : ""}${run.data?.eta_hours ? `, about ${run.data.eta_hours} h to go` : ""}` : "no long run (genesis start)"}>
        <div className="grid tiles">
          <Tile label="Ollama" value={!d.llm ? "not used" : d.ollama ? "up" : "down"} />
          <Tile label="LLM calls / minute" value={d.callsPerMinute} note={d.limit ? `limit ${d.limit} (last 10 min)` : "last 10 minutes"} />
          <Tile label="LLM latency" value={d.latencyMs ? `${d.latencyMs} ms` : "–"} note={d.failed ? `${d.failed} failed` : ""} />
          <Tile label="Disk free" value={`${d.diskFreeGb} GB`} note={`world file ${d.dbMb} MB`} />
          {d.gpu && <Tile label="GPU" value={`${d.gpu.loadPct}% · ${d.gpu.tempC}°C`} note={`${(d.gpu.memMb / 1024).toFixed(1)} of ${(d.gpu.memTotalMb / 1024).toFixed(1)} GB · ${d.gpu.name}`} />}
        </div>
        {st && (
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button onClick={() => send("pause")} disabled={st !== "running"}>Pause</button>
            <button onClick={() => send("continue")} disabled={st !== "paused"}>Resume</button>
            <button onClick={() => send("snapshot")} disabled={st !== "running"}>Snapshot now</button>
          </div>
        )}
        {note && <p className="dim">{note}</p>}
      </Card>
    </div>
  );
}
