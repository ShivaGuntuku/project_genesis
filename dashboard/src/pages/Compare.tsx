import { useEffect, useState } from "react";
import { get, useApi } from "../api";
import { LineChart, series } from "../chart";
import { Card, Status } from "../components";

interface Summary { world: string; day: number; branched_from: { world: string; tick: number } | null; deaths_by_cause: Record<string, number>; currencies: string[]; [k: string]: unknown }
interface CompareData { rows: { key: string; label: string }[]; worlds: Summary[]; series: Record<string, [number, number][]> }

export default function Compare() {
  const worlds = useApi<{ current: string; worlds: string[] }>("/api/worlds");
  const [other, setOther] = useState("");
  const [data, setData] = useState<CompareData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!other) return;
    get<CompareData>(`/api/compare?other=${encodeURIComponent(other)}`).then(setData).catch((e) => setErr(String(e)));
  }, [other]);
  if (!worlds.data) return <Status error={worlds.error} loading={worlds.loading} />;
  const choices = worlds.data.worlds.filter((w) => w !== worlds.data!.current);
  return (
    <>
      <h1>Compare worlds</h1>
      <p className="sub">
        This world beside another: a branch made with <code>genesis branch</code> and changed in God Mode, or a control
        run of the same seed. Differences are second minus first.
      </p>
      <label style={{ display: "flex", gap: 8, marginBottom: 12 }}>Compare {worlds.data.current} with
        <select value={other} onChange={(e) => setOther(e.target.value)}>
          <option value="">choose a world</option>{choices.map((w) => <option key={w}>{w}</option>)}
        </select>
      </label>
      {err && <p className="err">{err}</p>}
      {data && (
        <div className="grid two">
          <Card title="Side by side" hint={data.worlds.map((w) => w.branched_from ? `${w.world} branched from ${w.branched_from.world} at tick ${w.branched_from.tick}` : "").filter(Boolean).join("; ")}>
            <table className="data"><thead><tr><th></th>{data.worlds.map((w) => <th key={w.world} className="num">{w.world}</th>)}<th className="num">Difference</th></tr></thead>
              <tbody>{data.rows.map((r) => {
                const [a, b] = data.worlds.map((w) => w[r.key] as number);
                const d = typeof a === "number" && typeof b === "number" ? b - a : null;
                return <tr key={r.key}><td>{r.label}</td><td className="num">{String(a)}</td><td className="num">{String(b)}</td>
                  <td className="num">{d === null ? "" : (d > 0 ? "+" : "") + (Number.isInteger(d) ? d : d.toFixed(3))}</td></tr>;
              })}
                <tr><td>Deaths by cause</td>{data.worlds.map((w) => <td key={w.world}>{Object.entries(w.deaths_by_cause).map(([k, v]) => `${k} ${v}`).join(", ") || "–"}</td>)}<td></td></tr>
                <tr><td>Currencies</td>{data.worlds.map((w) => <td key={w.world}>{w.currencies.join(", ") || "–"}</td>)}<td></td></tr>
              </tbody></table>
          </Card>
          <Card title="Alive over time">
            <LineChart xName="day" lines={Object.entries(data.series).map(([name, pts], i) => ({ name, color: series(i + 1), data: pts }))} />
          </Card>
        </div>
      )}
    </>
  );
}
