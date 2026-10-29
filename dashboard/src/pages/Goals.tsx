import { useApi, type EventItem } from "../api";
import { AgentLink, Card, EventRow, Status, Tile } from "../components";

interface Current { id: number; name: string; need: string; text: string; since: string; ownWords: boolean }
interface Need { need: string; set: number; reached: number; gaveUp: number; own: number; medianDays: number | null }
interface GoalsData { current: Current[]; needs: Need[]; recent: EventItem[] }

export default function Goals() {
  const { data, error, loading } = useApi<GoalsData>("/api/goals");
  if (!data) return <Status error={error} loading={loading} />;
  const total = (k: "set" | "reached" | "gaveUp" | "own") => data.needs.reduce((a, n) => a + n[k], 0);
  const max = Math.max(1, ...data.needs.map((n) => n.set));
  return (
    <>
      <h1>Drives and goals</h1>
      <p className="sub">
        Drives come from genes, family, group and age. A goal is what someone wants to achieve, in plain words
        (or their own, written in their nightly reflection), plus what it is about, so the world can tell when
        it is reached. Free time goes into working on it; a goal not reached in two years is given up.
      </p>
      <div className="grid tiles">
        <Tile label="Striving now" value={data.current.length} note="living people with a goal" />
        <Tile label="Goals set" value={total("set")} note={`${total("own")} in their own words`} />
        <Tile label="Reached" value={total("reached")} note={total("set") ? `${Math.round((100 * total("reached")) / total("set"))}% of those set` : "–"} />
        <Tile label="Given up" value={total("gaveUp")} />
      </div>
      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="What people strive for" hint="goals set, reached and given up, by what they are about">
          {data.needs.length === 0 ? <p className="dim">No goals yet.</p> : (
            <table className="data"><thead><tr>
              <th>About</th><th></th><th className="num">Set</th><th className="num">Reached</th>
              <th className="num">Given up</th><th className="num">Days to reach</th>
            </tr></thead><tbody>
              {data.needs.slice().sort((a, b) => b.set - a.set).map((n) => (
                <tr key={n.need}>
                  <td>{n.need}</td>
                  <td style={{ width: "30%" }}>
                    <div style={{ display: "flex", height: 10, width: `${(100 * n.set) / max}%`, minWidth: 4, gap: 1 }}>
                      <div style={{ flex: n.reached, background: "var(--series-1)", borderRadius: 2 }} title="reached" />
                      <div style={{ flex: n.gaveUp, background: "var(--series-2)", borderRadius: 2 }} title="given up" />
                      <div style={{ flex: Math.max(0, n.set - n.reached - n.gaveUp), background: "var(--grid)", borderRadius: 2 }} title="open" />
                    </div>
                  </td>
                  <td className="num">{n.set}</td><td className="num">{n.reached}</td>
                  <td className="num">{n.gaveUp}</td><td className="num">{n.medianDays ?? "–"}</td>
                </tr>
              ))}
            </tbody></table>
          )}
          <p className="dim" style={{ marginTop: 8 }}>
            <span style={{ color: "var(--series-1)" }}>■</span> reached · <span style={{ color: "var(--series-2)" }}>■</span> given up · <span style={{ color: "var(--grid)" }}>■</span> still open
          </p>
        </Card>
        <Card title="Goals now">
          {data.current.length === 0 ? <p className="dim">Nobody is working towards a goal right now.</p> : (
            <table className="data"><tbody>
              {data.current.map((c) => (
                <tr key={c.id}>
                  <td><AgentLink id={c.id} name={c.name} /></td>
                  <td>“{c.text}”<div className="dim">{c.need} · since {c.since}{c.ownWords ? " · own words" : ""}</div></td>
                </tr>
              ))}
            </tbody></table>
          )}
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
