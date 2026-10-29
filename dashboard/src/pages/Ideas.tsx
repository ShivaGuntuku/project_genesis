import { useApi, type EventItem } from "../api";
import { ColumnChart, series } from "../chart";
import { AgentLink, Card, EventRow, Status, Tile } from "../components";

interface Concept {
  id: number; name: string; function: string; functions: string[]; recipe: [string[], string[]];
  inventor: { id: number; name: string }; invented: string; idea: string; sameAs: string | null;
  lost: string | null; holders: { id: number; name: string }[]; made: number; taught: number; structures: number;
}
interface Year { year: number; tried: number; invented: number; made: number; learned: number; lost: number }
interface IdeasData { concepts: Concept[]; years: Year[]; recent: EventItem[]; structures: number }

export default function Ideas() {
  const { data, error, loading } = useApi<IdeasData>("/api/ideas");
  if (!data) return <Status error={error} loading={loading} />;
  const y = data.years;
  const col = (k: keyof Year) => y.map((d) => [d.year, d[k]] as [number, number]);
  const sum = (k: keyof Year) => y.reduce((a, d) => a + d[k], 0);
  const living = data.concepts.filter((c) => !c.lost);
  const ranked = data.concepts.slice().sort((a, b) => Number(!!a.lost) - Number(!!b.lost) || b.holders.length - a.holders.length);
  return (
    <>
      <h1>Ideas and inventions</h1>
      <p className="sub">
        People gather materials and tinker with what keeps going wrong. Whether a thing works is decided by hidden
        properties of the materials, never by its name; every name is a new word of the valley. Knowledge spreads only
        by teaching, and is lost when the last person who knows it dies.
      </p>
      <div className="grid tiles">
        <Tile label="Known ideas" value={living.length} note={`${data.concepts.length} ever invented`} />
        <Tile label="Tries" value={sum("tried")} note={`${sum("invented")} worked`} />
        <Tile label="Lessons learned" value={sum("learned")} note="by teaching" />
        <Tile label="Lost" value={sum("lost")} note={`${data.structures} structures built`} />
      </div>
      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="Inventing and teaching by year">
          <ColumnChart loading={loading} xName="year" bars={[
            { name: "invented", color: series(1), data: col("invented") },
            { name: "taught", color: series(2), data: col("learned") },
            { name: "made again", color: series(3), data: col("made") },
          ]} />
        </Card>
        <Card title="Failed tries by year" hint="a try that did not do what was hoped for">
          <ColumnChart loading={loading} xName="year" bars={[{ name: "failed tries", color: series(1), data: col("tried") }]} />
        </Card>
      </div>
      <div style={{ marginTop: 14 }}>
        <Card title="Every idea" hint="what it does comes from the physics; the idea in quotes is the inventor's own">
          {ranked.length === 0 ? <p className="dim">Nothing has been invented yet.</p> : (
            <table className="data"><thead><tr>
              <th>Name</th><th>What it does</th><th>Made from</th><th>Inventor</th><th>When</th>
              <th className="num">Know it</th><th className="num">Taught</th><th className="num">Made</th>
            </tr></thead><tbody>
              {ranked.map((c) => (
                <tr key={c.id} className={c.lost ? "dim" : ""}>
                  <td><b>{c.name}</b>{c.sameAs ? <div className="dim">does what {c.sameAs} does</div> : null}</td>
                  <td>{c.functions.join(", ")}{c.idea ? <div className="dim">“{c.idea}”</div> : null}</td>
                  <td>{c.recipe[0].join(" + ")}<div className="dim">{c.recipe[1].join(", ")}</div></td>
                  <td><AgentLink id={c.inventor.id} name={c.inventor.name} /></td>
                  <td>{c.invented}{c.lost ? <div>lost {c.lost}</div> : null}</td>
                  <td className="num" title={c.holders.map((h) => h.name).join(", ")}>{c.holders.length}</td>
                  <td className="num">{c.taught}</td>
                  <td className="num">{c.made + c.structures}</td>
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
