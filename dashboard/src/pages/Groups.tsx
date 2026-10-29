import { useApi, type EventItem } from "../api";
import { LineChart, series } from "../chart";
import { AgentLink, Card, EventRow, Status, Tile } from "../components";

interface Group {
  id: number; name: string; founder: { id: number; name: string }; leader: { id: number; name: string };
  members: { id: number; name: string }[]; home: [number, number]; store: number; founded: string;
  ended: string | null; history: EventItem[];
  orders: { obeyed: number; refused: number; done: number; lapsed: number };
  fights: { inside: number; with_outsiders: number }; trespass: number;
}
interface GroupsData { groups: Group[]; series: Record<string, number>[] }

export default function Groups() {
  const { data, error, loading } = useApi<GroupsData>("/api/groups");
  if (!data) return <Status error={error} loading={loading} />;
  const s = data.series;
  const line = (k: string) => s.map((d) => [d.day, d[k] ?? 0] as [number, number]);
  const active = data.groups.filter((g) => !g.ended);
  return (
    <>
      <h1>Groups and conflict</h1>
      <p className="sub">
        Groups form only when people agree; every join, departure, leader change and order is a recorded
        event. Authority is respect: members obey or refuse, and can challenge the leader.
      </p>
      <div className="grid tiles">
        <Tile label="Groups now" value={active.length} note={`${data.groups.length} ever founded`} />
        <Tile label="In a group" value={s.length ? `${Math.round((s[s.length - 1]["groups.share_in_groups"] ?? 0) * 100)}%` : "–"} note="of the living" />
        <Tile label="Fights" value={s.reduce((a, d) => a + (d["today.fight"] ?? 0), 0)} note="in all" />
        <Tile label="Friend communities" value={s.length ? s[s.length - 1]["social.communities"] ?? 0 : "–"} note="found in the network" />
      </div>
      <div className="grid three" style={{ marginTop: 14 }}>
        <Card title="Groups and their size">
          <LineChart loading={loading} lines={[
            { name: "groups", color: series(1), data: line("groups.count") },
            { name: "largest group", color: series(2), data: line("groups.largest") },
          ]} />
        </Card>
        <Card title="Share of the living in a group">
          <LineChart loading={loading} percent yMin={0} yMax={1}
            lines={[{ name: "in a group", color: series(1), area: true, data: line("groups.share_in_groups") }]} />
        </Card>
        <Card title="Friction per day" hint="fights, and outsiders entering a group's home area">
          <LineChart loading={loading} lines={[
            { name: "fights", color: series(1), data: line("today.fight") },
            { name: "trespasses", color: series(2), data: line("today.trespass") },
          ]} />
        </Card>
      </div>
      {data.groups.length === 0 && <p className="dim" style={{ marginTop: 14 }}>No group has been founded yet.</p>}
      {data.groups.map((g) => (
        <div key={g.id} style={{ marginTop: 14 }}>
          <Card title={<>{g.name}{g.ended ? <span className="dim"> (ended {g.ended})</span> : ""}</>}
            hint={`founded ${g.founded} by ${g.founder.name} · home (${g.home[0]},${g.home[1]}) · store ${g.store} food`}>
            <div className="grid two">
              <div>
                <p>Leader: {g.ended ? "–" : <AgentLink id={g.leader.id} name={g.leader.name} />} · {g.members.length} members</p>
                <div className="words">{g.members.map((m) => (
                  <a key={m.id} className="word" href={`#/agent/${m.id}`}>{m.name}{m.id === g.leader.id ? <i>leader</i> : null}</a>
                ))}</div>
                <table className="data" style={{ marginTop: 12 }}><tbody>
                  <tr><td>Orders obeyed / refused</td><td className="num">{g.orders.obeyed} / {g.orders.refused}</td></tr>
                  <tr><td>Orders carried out / lapsed</td><td className="num">{g.orders.done} / {g.orders.lapsed}</td></tr>
                  <tr><td>Fights inside / with outsiders</td><td className="num">{g.fights.inside} / {g.fights.with_outsiders}</td></tr>
                  <tr><td>Outsiders in their home area</td><td className="num">{g.trespass}</td></tr>
                </tbody></table>
              </div>
              <div>
                <h2>History</h2>
                <ul className="feed" style={{ maxHeight: 260, overflow: "auto" }}>
                  {g.history.slice().reverse().map((e) => <EventRow key={e.id} e={e} />)}
                </ul>
              </div>
            </div>
          </Card>
        </div>
      ))}
    </>
  );
}
