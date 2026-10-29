import { useState } from "react";
import { useApi, type AgentRow } from "../api";
import { AgentLink, Status } from "../components";

type Key = keyof AgentRow;
const COLUMNS: [Key, string, boolean][] = [
  ["name", "Name", false], ["generation", "Gen", true], ["ageDays", "Age (days)", true],
  ["stage", "Stage", false], ["partner", "Partner", false], ["children", "Children", true],
  ["messages", "Messages", true], ["voiced", "Own words", true], ["friends", "Friends", true],
  ["rivals", "Rivals", true], ["beliefs", "Beliefs", true],
];

export default function Agents() {
  const { data, error, loading } = useApi<AgentRow[]>("/api/agents");
  const [q, setQ] = useState("");
  const [show, setShow] = useState<"all" | "alive" | "dead">("all");
  const [sort, setSort] = useState<[Key, number]>(["generation", 1]);
  if (!data) return <Status error={error} loading={loading} />;
  const rows = data
    .filter((a) => (show === "all" ? true : show === "alive" ? a.alive : !a.alive))
    .filter((a) => !q || a.name.toLowerCase().includes(q.toLowerCase()) || a.story.toLowerCase().includes(q.toLowerCase()))
    .sort((x, y) => {
      const [k, dir] = sort;
      const a = x[k] ?? "", b = y[k] ?? "";
      return (a < b ? -1 : a > b ? 1 : x.id - y.id) * dir;
    });
  return (
    <>
      <h1>Agents</h1>
      <p className="sub">Everyone who ever lived ({data.length}). Click a name for everything about them.</p>
      <div className="filters">
        <input placeholder="search names and self-stories…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 260 }} />
        {(["all", "alive", "dead"] as const).map((s) => (
          <button key={s} className={show === s ? "on" : ""} onClick={() => setShow(s)}>{s}</button>
        ))}
      </div>
      <div className="card" style={{ overflowX: "auto" }}>
        <table className="data">
          <thead><tr>
            {COLUMNS.map(([k, label, num]) => (
              <th key={k} className={`sort${num ? " num" : ""}`}
                onClick={() => setSort(([sk, d]) => [k, sk === k ? -d : num ? -1 : 1])}>
                {label}{sort[0] === k ? (sort[1] > 0 ? " ↑" : " ↓") : ""}
              </th>
            ))}
            <th>Parents</th><th>Died</th>
          </tr></thead>
          <tbody>{rows.map((a) => (
            <tr key={a.id} className={a.alive ? "" : "dead"}>
              <td><AgentLink id={a.id} name={a.name} />{a.alive ? "" : " †"}</td>
              <td className="num">{a.generation}</td><td className="num">{a.ageDays}</td><td>{a.stage}</td>
              <td>{a.partner ?? ""}</td><td className="num">{a.children || ""}</td>
              <td className="num">{a.messages}</td><td className="num">{a.voiced}</td>
              <td className="num">{a.friends || ""}</td><td className="num">{a.rivals || ""}</td>
              <td className="num">{a.beliefs || ""}</td>
              <td>{a.parents.join(" & ")}</td>
              <td className="dim">{a.died ? `${a.died}, ${a.causeOfDeath}` : ""}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </>
  );
}
