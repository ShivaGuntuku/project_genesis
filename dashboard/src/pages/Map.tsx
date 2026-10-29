import { useEffect, useRef, useState } from "react";
import { get, useApi } from "../api";
import { Card, Status } from "../components";
import { token } from "../chart";

interface Person { id: number; name: string; x: number; y: number; ideas: number; talent: string | null; group?: number | null }
interface MapData {
  width: number; height: number; tiles: string[]; clock: string; years: number[];
  food: { id: number; x: number; y: number; quantity: number }[];
  structures: { x: number; y: number }[]; valleys: { name: string; x: number; y: number }[]; people: Person[];
}
interface YearData { year: number; people: Person[]; fights: { x: number; y: number }[] }

const TERRAIN: Record<string, string> = { p: "#a7c27a", f: "#4f7f3a", m: "#9a8f7e", w: "#4b8fc9", k: "#5b5f68" };

export default function WorldMap() {
  const { data, error, loading } = useApi<MapData>("/api/map");
  const [year, setYear] = useState<number | null>(null);
  const [past, setPast] = useState<YearData | null>(null);
  const [layers, setLayers] = useState({ food: true, people: true, ideas: false, fights: true, valleys: true });
  const [hover, setHover] = useState<Person | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (year === null) { setPast(null); return; }
    get<YearData>(`/api/map/year/${year}`).then(setPast).catch(() => setPast(null));
  }, [year]);

  const T = data ? Math.max(3, Math.min(10, Math.floor(900 / Math.max(data.width, data.height)))) : 6;
  const people = past ? past.people : data?.people ?? [];

  useEffect(() => {
    const c = canvas.current;
    if (!c || !data) return;
    const ctx = c.getContext("2d")!;
    data.tiles.forEach((row, y) => [...row].forEach((code, x) => {
      ctx.fillStyle = TERRAIN[code] ?? "#888"; ctx.fillRect(x * T, y * T, T, T);
    }));
    if (layers.food) data.food.forEach((f) => {
      ctx.fillStyle = f.quantity > 0 ? "#c0392b" : "#6d4c41";
      ctx.beginPath(); ctx.arc(f.x * T + T / 2, f.y * T + T / 2, T * 0.45, 0, 7); ctx.fill();
    });
    data.structures.forEach((s) => { ctx.fillStyle = "#e1b382"; ctx.fillRect(s.x * T, s.y * T, T, T); });
    if (layers.fights && past) past.fights.forEach((f) => {
      ctx.strokeStyle = "#ff4757"; ctx.lineWidth = 1.5; ctx.beginPath();
      ctx.moveTo(f.x * T, f.y * T); ctx.lineTo(f.x * T + T, f.y * T + T);
      ctx.moveTo(f.x * T + T, f.y * T); ctx.lineTo(f.x * T, f.y * T + T); ctx.stroke();
    });
    if (layers.people) people.forEach((p) => {
      const r = layers.ideas ? T * (0.35 + Math.min(1.2, p.ideas * 0.25)) : T * 0.5;
      ctx.fillStyle = "#fdfdfd"; ctx.strokeStyle = "#111"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(p.x * T + T / 2, p.y * T + T / 2, r, 0, 7); ctx.fill(); ctx.stroke();
    });
    if (layers.valleys) data.valleys.forEach((v) => {
      ctx.font = `${Math.max(11, T * 1.6)}px system-ui`; ctx.fillStyle = "#111";
      ctx.fillText(v.name, v.x * T - 20, v.y * T - T * 3);
    });
  }, [data, past, people, layers, T]);

  if (!data) return <Status error={error} loading={loading} />;
  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = Math.floor((e.clientX - r.left) / T), y = Math.floor((e.clientY - r.top) / T);
    setHover(people.find((p) => Math.abs(p.x - x) <= 1 && Math.abs(p.y - y) <= 1) ?? null);
  };
  return (
    <>
      <h1>World map</h1>
      <p className="sub">
        The land as it is now (God's changes and burned woods included), its berry patches and shelters, and where
        everyone is. Move the slider to see where people were at the start of any year; crosses are that year's fights.
      </p>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
        {(Object.keys(layers) as (keyof typeof layers)[]).map((k) => (
          <label key={k} style={{ display: "flex", gap: 4 }}>
            <input type="checkbox" checked={layers[k]} onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} />
            {k === "ideas" ? "size by ideas known" : k}
          </label>
        ))}
        {data.years.length > 0 && (
          <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input type="range" min={data.years[0]} max={data.years[data.years.length - 1] + 1}
              value={year ?? data.years[data.years.length - 1] + 1}
              onChange={(e) => { const v = Number(e.target.value); setYear(v > data.years[data.years.length - 1] ? null : v); }} />
            <b>{year === null ? `now (${data.clock})` : `start of year ${year}`}</b>
          </span>
        )}
      </div>
      <Card title={`${people.length} people`} hint={hover ? `${hover.name}${hover.talent ? ` · ${hover.talent}` : ""} · ${hover.ideas} ideas — click to open` : "hover a person"}>
        <div style={{ overflow: "auto" }}>
          <canvas ref={canvas} width={data.width * T} height={data.height * T} onMouseMove={onMove}
            onClick={() => { if (hover) location.hash = `#/agent/${hover.id}`; }}
            style={{ display: "block", cursor: hover ? "pointer" : "default", background: token("--surface-1") }} />
        </div>
      </Card>
    </>
  );
}
