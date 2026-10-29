import { useMemo, useState } from "react";
import { useApi } from "../api";
import { Card, Status } from "../components";

interface Person {
  id: number; name: string; generation: number; parents: number[]; alive: boolean;
  born: string | null; died: string | null; cause: string | null; talent: string | null; partner: number | null;
}

const W = 112, H = 40, GAP_X = 12, GAP_Y = 46;

export default function Family() {
  const { data, error, loading } = useApi<{ people: Person[] }>("/api/family");
  const [living, setLiving] = useState(false);
  const layout = useMemo(() => {
    if (!data) return null;
    const byId = new Map(data.people.map((p) => [p.id, p]));
    let shown = data.people;
    if (living) { // the living and everyone they descend from
      const keep = new Set<number>();
      const up = (id: number) => { if (keep.has(id)) return; keep.add(id); byId.get(id)?.parents.forEach(up); };
      data.people.filter((p) => p.alive).forEach((p) => up(p.id));
      shown = data.people.filter((p) => keep.has(p.id));
    }
    const rows = new Map<number, Person[]>();
    shown.forEach((p) => rows.set(p.generation, [...(rows.get(p.generation) ?? []), p]));
    const pos = new Map<number, { x: number; y: number }>();
    const gens = [...rows.keys()].sort((a, b) => a - b);
    gens.forEach((g, gi) => {
      const row = rows.get(g)!;
      if (gi > 0) row.sort((a, b) => avg(a, pos) - avg(b, pos) || a.id - b.id);
      row.forEach((p, i) => pos.set(p.id, { x: i * (W + GAP_X), y: gi * (H + GAP_Y) }));
    });
    const width = Math.max(...[...rows.values()].map((r) => r.length)) * (W + GAP_X);
    return { shown, pos, width, height: gens.length * (H + GAP_Y) };
  }, [data, living]);
  if (!data || !layout) return <Status error={error} loading={loading} />;
  const { shown, pos, width, height } = layout;
  return (
    <>
      <h1>Family tree</h1>
      <p className="sub">Every person, by generation, joined to their parents. Faded: no longer alive. Click a name to open them.</p>
      <label style={{ display: "flex", gap: 6, marginBottom: 10 }}>
        <input type="checkbox" checked={living} onChange={(e) => setLiving(e.target.checked)} /> only the living and their ancestors
      </label>
      <Card title={`${shown.length} people, ${shown.filter((p) => p.alive).length} alive`}>
        <div style={{ overflow: "auto", maxHeight: "70vh" }}>
          <svg width={width} height={height} style={{ display: "block" }}>
            {shown.flatMap((p) => p.parents.filter((q) => pos.has(q)).map((q) => {
              const a = pos.get(q)!, b = pos.get(p.id)!;
              return <path key={`${q}-${p.id}`} d={`M${a.x + W / 2},${a.y + H} C${a.x + W / 2},${a.y + H + GAP_Y / 2} ${b.x + W / 2},${b.y - GAP_Y / 2} ${b.x + W / 2},${b.y}`}
                fill="none" stroke="var(--grid)" strokeWidth={1.2} />;
            }))}
            {shown.map((p) => {
              const { x, y } = pos.get(p.id)!;
              return (
                <a key={p.id} href={`#/agent/${p.id}`}>
                  <g opacity={p.alive ? 1 : 0.5}>
                    <rect x={x} y={y} width={W} height={H} rx={6} fill="var(--surface-2)" stroke={p.alive ? "var(--series-1)" : "var(--grid)"} />
                    <text x={x + 8} y={y + 16} fontSize={12} fill="var(--ink-1)">{p.name}{p.alive ? "" : " †"}</text>
                    <text x={x + 8} y={y + 31} fontSize={10} fill="var(--ink-2)">{p.talent ?? ""}{p.cause ? ` · ${p.cause}` : ""}</text>
                  </g>
                </a>
              );
            })}
          </svg>
        </div>
      </Card>
    </>
  );
}

function avg(p: Person, pos: Map<number, { x: number; y: number }>): number {
  const xs = p.parents.map((q) => pos.get(q)?.x).filter((x): x is number => x !== undefined);
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
}
