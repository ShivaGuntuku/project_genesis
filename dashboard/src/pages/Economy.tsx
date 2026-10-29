import { useApi, type EventItem } from "../api";
import { ColumnChart, LineChart, series } from "../chart";
import { AgentLink, Card, EventRow, Status, Tile } from "../components";

interface Currency { id: number; name: string; good: string; region: string; since: string; share: number }
interface Year { year: number; offers: number; accepted: number; indirect: number; promises: number; repaid: number; broken: number; healed: number }
interface Good { good: string; given: number; indirect: number }
interface Price { year: number; day: number; currency: number; good: string; amount: number; label: string }
interface Person { id: number; name: string; talent: string | null; profession: string | null; work: Record<string, number>; owes: number }
interface EconomyData { currencies: Currency[]; years: Year[]; goods: Good[]; prices: Price[]; people: Person[]; recent: EventItem[] }

export default function Economy() {
  const { data, error, loading } = useApi<EconomyData>("/api/economy");
  if (!data) return <Status error={error} loading={loading} />;
  const y = data.years;
  const col = (k: keyof Year) => y.map((d) => [d.year, d[k]] as [number, number]);
  const sum = (k: keyof Year) => y.reduce((a, d) => a + d[k], 0);
  const priced = [...new Set(data.prices.map((p) => p.good))].slice(0, 4);
  const jobs = data.people.reduce<Record<string, number>>((a, p) => { const k = p.profession ?? "none yet"; a[k] = (a[k] ?? 0) + 1; return a; }, {});
  return (
    <>
      <h1>Trade and money</h1>
      <p className="sub">
        Nothing called money exists in the code. People barter, take things on a promise, and judge goods by
        their use and by how often they have seen them taken. Taking something mostly to pass it on is an
        indirect exchange; when one good makes up most of a valley's indirect exchanges in a season, a currency has
        emerged, and its users name it.
      </p>
      <div className="grid tiles">
        <Tile label="Trades" value={sum("accepted")} note={`of ${sum("offers")} offers`} />
        <Tile label="To pass on" value={sum("indirect")} note="indirect exchanges" />
        <Tile label="Promises" value={sum("promises")} note={`${sum("repaid")} kept · ${sum("broken")} broken`} />
        <Tile label="Money" value={data.currencies.length ? data.currencies.map((c) => c.name).join(", ") : "none yet"}
          note={data.currencies.length ? data.currencies.map((c) => `${c.good} in ${c.region}`).join("; ") : "it may never emerge"} />
      </div>
      {data.currencies.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <Card title="Money of their own" hint="when it emerged, from what, and where">
            <table className="data"><tbody>{data.currencies.map((c) => (
              <tr key={c.id}><td><b>{c.name}</b></td><td>{c.good}</td><td>{c.region}</td><td>since {c.since}</td>
                <td className="num">{Math.round(c.share * 100)}% of indirect exchanges</td></tr>
            ))}</tbody></table>
          </Card>
        </div>
      )}
      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="Trades by year">
          <ColumnChart loading={loading} xName="year" bars={[
            { name: "traded", color: series(1), data: col("accepted") },
            { name: "to pass on", color: series(2), data: col("indirect") },
            { name: "on a promise", color: series(3), data: col("promises") },
          ]} />
        </Card>
        <Card title="Promises by year" hint="brought back, or broken (trust falls)">
          <ColumnChart loading={loading} xName="year" stacked bars={[
            { name: "kept", color: series(1), data: col("repaid") },
            { name: "broken", color: series(2), data: col("broken") },
          ]} />
        </Card>
      </div>
      {priced.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <Card title="Prices in their money" hint="how much of the currency one lot of each good went for">
            <LineChart loading={loading} lines={priced.map((g, i) => ({
              name: data.prices.find((p) => p.good === g)?.label.replace(/^1 /, "") ?? g, color: series(i + 1),
              data: data.prices.filter((p) => p.good === g).map((p) => [p.day, p.amount] as [number, number]),
            }))} />
          </Card>
        </div>
      )}
      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="What changes hands" hint="goods given in trade, and how often only to pass them on">
          <table className="data"><thead><tr><th>Good</th><th className="num">Given</th><th className="num">To pass on</th></tr></thead>
            <tbody>{data.goods.slice(0, 12).map((g) => (
              <tr key={g.good}><td>{g.good}</td><td className="num">{g.given}</td>
                <td className="num">{g.indirect} ({Math.round((100 * g.indirect) / Math.max(1, g.given))}%)</td></tr>
            ))}</tbody></table>
        </Card>
        <Card title="Talents and work" hint={Object.entries(jobs).map(([k, v]) => `${v} ${k}`).join(" · ")}>
          <table className="data"><thead><tr><th>Person</th><th>Talent</th><th>Seen as</th><th className="num">Owes</th></tr></thead>
            <tbody>{data.people.map((p) => (
              <tr key={p.id}><td><AgentLink id={p.id} name={p.name} /></td><td>{p.talent ?? "–"}</td>
                <td>{p.profession ?? <span className="dim">–</span>}</td><td className="num">{p.owes || ""}</td></tr>
            ))}</tbody></table>
        </Card>
      </div>
      <div style={{ marginTop: 14 }}>
        <Card title="Latest">
          <ul className="feed" style={{ maxHeight: 320, overflow: "auto" }}>
            {data.recent.slice().reverse().map((e) => <EventRow key={e.id} e={e} />)}
          </ul>
        </Card>
      </div>
    </>
  );
}
