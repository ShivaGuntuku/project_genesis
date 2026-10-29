import { useState } from "react";
import { useApi } from "../api";
import { ColumnChart, LineChart, series } from "../chart";
import { Card, Status, Tile } from "../components";

interface Valley { people: number; ideas: number; currency: string | null }
interface Year { year: number; valleys: Record<string, Valley>; apart: number; concepts: number; lost: number }
interface Disaster { year: number; kind: string; region: string; days: number; people: number; text: string }
interface Leak { year: number; words: number; earth: number; perThousand: number }
interface HistoryData {
  years: Year[]; disasters: Disaster[]; migrations: { year: number; count: number }[]; leaks: Leak[]; valleys: string[];
}

export default function History() {
  const { data, error, loading } = useApi<HistoryData>("/api/history");
  const [range, setRange] = useState<[number, number] | null>(null);
  if (!data) return <Status error={error} loading={loading} />;
  const first = data.years[0]?.year ?? 1, lastYear = data.years[data.years.length - 1]?.year ?? 1;
  const [from, to] = range ?? [first, lastYear];
  const ys = data.years.filter((y) => y.year >= from && y.year <= to);
  const valleys = data.valleys;
  const per = (k: keyof Valley) => valleys.map((v, i) => ({
    name: v, color: series(i + 1),
    data: ys.map((y) => [y.year, Number(y.valleys[v]?.[k] ?? 0)] as [number, number]),
  }));
  const last = ys[ys.length - 1];
  const words = data.leaks.reduce((a, l) => a + l.words, 0);
  const earth = data.leaks.reduce((a, l) => a + l.earth, 0);
  return (
    <>
      <h1>History of the valleys</h1>
      <p className="sub">
        The long view, from a snapshot taken at the start of every year: how many live in each valley, what each
        knows, whether they drift apart, the disasters they lived through, who moved, and how much of our world
        leaks into their words.
      </p>
      <div className="grid tiles">
        <Tile label="Years recorded" value={ys.length} note={last ? `up to year ${last.year}` : "a snapshot comes each new year"} />
        <Tile label="Disasters" value={data.disasters.length} note={data.disasters.length ? `last: ${data.disasters[data.disasters.length - 1].kind}` : "every 15-25 years"} />
        <Tile label="Moved valleys" value={data.migrations.reduce((a, m) => a + m.count, 0)} />
        <Tile label="Leak rate" value={words ? `${((1000 * earth) / words).toFixed(1)}‰` : "–"} note={words ? `${earth} words from our world in ${words}` : "needs the LLM"} />
      </div>
      {data.years.length > 1 && (
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 14 }}>
          <span className="dim">Years</span>
          <input type="range" min={first} max={lastYear} value={from} onChange={(e) => setRange([Math.min(Number(e.target.value), to), to])} />
          <input type="range" min={first} max={lastYear} value={to} onChange={(e) => setRange([from, Math.max(Number(e.target.value), from)])} />
          <span>{from}–{to}</span>
        </div>
      )}
      {ys.length === 0 && <p className="dim" style={{ marginTop: 14 }}>No yearly snapshot yet: the first comes when year 2 begins.</p>}
      {ys.length > 0 && (
        <>
          <div className="grid two" style={{ marginTop: 14 }}>
            <Card title="People in each valley" hint="at the start of each year">
              <LineChart loading={loading} xName="year" lines={per("people")} />
            </Card>
            <Card title="Ideas known in each valley" hint="ideas someone alive there knows">
              <LineChart loading={loading} xName="year" lines={per("ideas")} />
            </Card>
          </div>
          <div className="grid two" style={{ marginTop: 14 }}>
            <Card title="How far apart the valleys are" hint="share of known ideas that not every valley has (0 = alike)">
              <LineChart loading={loading} xName="year" percent yMin={0} yMax={1}
                lines={[{ name: "apart", color: series(1), area: true, data: ys.map((y) => [y.year, y.apart] as [number, number]) }]} />
            </Card>
            <Card title="Leak rate by year" hint="words from our world per 1,000 they say or believe">
              <ColumnChart loading={loading} xName="year" bars={[{ name: "per 1,000 words", color: series(2),
                data: data.leaks.map((l) => [l.year, l.perThousand] as [number, number]) }]} />
            </Card>
          </div>
        </>
      )}
      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="Disasters" hint="one valley or all, every 15-25 years">
          {data.disasters.length === 0 ? <p className="dim">None yet.</p> : (
            <table className="data"><tbody>{data.disasters.map((d, i) => (
              <tr key={i}><td className="num">year {d.year}</td><td>{d.text}</td><td className="num">{d.days} days</td></tr>
            ))}</tbody></table>
          )}
        </Card>
        <Card title="Valleys now" hint={last ? `year ${last.year}` : ""}>
          {last ? (
            <table className="data"><thead><tr><th>Valley</th><th className="num">People</th><th className="num">Ideas</th><th>Money</th></tr></thead>
              <tbody>{valleys.map((v) => (
                <tr key={v}><td>{v}</td><td className="num">{last.valleys[v]?.people ?? 0}</td>
                  <td className="num">{last.valleys[v]?.ideas ?? 0}</td><td>{last.valleys[v]?.currency ?? "–"}</td></tr>
              ))}</tbody></table>
          ) : <p className="dim">–</p>}
        </Card>
      </div>
    </>
  );
}
