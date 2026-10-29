import { useApi } from "../api";
import { LineChart, series } from "../chart";
import { AgentLink, Card, Status } from "../components";
import Conversations from "./Conversations";

interface PairData {
  a: { id: number; name: string }; b: { id: number; name: string };
  curves: Record<string, [number, number][]>; current: Record<string, { label: string; affinity: number; trust: number }>;
  acts: Record<string, number>; startHour: number; hoursPerTick: number; tick: number;
}

export default function Pair({ a, b, params }: { a: number; b: number; params: URLSearchParams }) {
  const { data, error, loading } = useApi<PairData>(`/api/pair?a=${a}&b=${b}`);
  if (!data) return <Status error={error} loading={loading} />;
  const day = (tick: number) => (data.startHour + tick * (data.hoursPerTick ?? 1)) / 24 + 1;
  const curve = (src: number, dst: number, dim: string): [number, number][] => {
    const pts = (data.curves[`${src}-${dst}-${dim}`] ?? []).map(([t, v]) => [+day(t).toFixed(2), v] as [number, number]);
    if (pts.length) pts.push([+day(data.tick).toFixed(2), pts[pts.length - 1][1]]); // hold to now
    return pts;
  };
  const feels = (src: { id: number; name: string }, dst: { id: number; name: string }) => {
    const now = data.current[`${src.id}-${dst.id}`];
    return (
      <Card title={<>How {src.name} feels about {dst.name}</>} hint={now ? `now: ${now.label}` : "they have not met"}>
        <LineChart loading={loading} yMin={-1} yMax={1} lines={[
          { name: "affinity", color: series(1), data: curve(src.id, dst.id, "affinity") },
          { name: "trust", color: series(2), data: curve(src.id, dst.id, "trust") },
          { name: "grievance", color: series(3), data: curve(src.id, dst.id, "grievance") },
        ]} />
      </Card>
    );
  };
  const acts = Object.entries(data.acts).sort((x, y) => y[1] - x[1]);
  return (
    <>
      <h1><AgentLink id={data.a.id} name={data.a.name} /> and <AgentLink id={data.b.id} name={data.b.name} /></h1>
      <p className="sub">
        Their whole story: how each feels about the other over time (every change comes from something said or
        done), and every conversation between them.{" "}
        {acts.map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(" · ")}
      </p>
      <div className="grid two">
        {feels(data.a, data.b)}
        {feels(data.b, data.a)}
      </div>
      <h2 style={{ marginTop: 18 }}>Conversations between them</h2>
      <Conversations params={params} base={`#/pair/${a}/${b}`} fixed={{ agent: a, other: b }} />
    </>
  );
}
