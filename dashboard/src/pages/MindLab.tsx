import { useEffect, useState } from "react";
import { get, useApi } from "../api";
import { LineChart, series } from "../chart";
import { Card, Status } from "../components";

interface Year {
  year: number; people: number; beliefsPerPerson: number; ownGoalShare: number | null; leakPerThousand: number | null;
  bestSkill: number | null; specialistShare: number | null; storyWords: number | null; valleysApart: number | null;
  professions: Record<string, number>;
}
interface Lab { world: string; years: Year[] }

const MEASURES: [keyof Year, string, string, boolean][] = [
  ["beliefsPerPerson", "New beliefs per person", "what reflection adds each year", false],
  ["ownGoalShare", "Goals in their own words", "share of goals set that year", true],
  ["leakPerThousand", "Words of our world", "per 1,000 words they say", false],
  ["specialistShare", "Specialists", "share seen as a profession", true],
  ["bestSkill", "Best specialist skill", "average, foraging aside", false],
  ["valleysApart", "Valleys apart", "share of ideas not shared by every valley", true],
  ["storyWords", "Self-story length", "words, average", false],
];

export default function MindLab() {
  const { data, error, loading } = useApi<{ worlds: Lab[] }>("/api/mindlab");
  const worlds = useApi<{ current: string; worlds: string[] }>("/api/worlds");
  const [other, setOther] = useState("");
  const [cmp, setCmp] = useState<Lab | null>(null);
  useEffect(() => {
    if (!other) { setCmp(null); return; }
    get<{ worlds: Lab[] }>(`/api/mindlab/compare?other=${encodeURIComponent(other)}`).then((d) => setCmp(d.worlds[1])).catch(() => setCmp(null));
  }, [other]);
  if (!data) return <Status error={error} loading={loading} />;
  const labs = [data.worlds[0], ...(cmp ? [cmp] : [])];
  return (
    <>
      <h1>Mind lab</h1>
      <p className="sub">
        The experiment's measures, year by year, from the yearly snapshots: are their minds their own, do they specialise,
        do the valleys drift apart? Compare with another world, such as a rules-only control or a branch.
      </p>
      <label style={{ display: "flex", gap: 8, marginBottom: 12 }}>Compare with
        <select value={other} onChange={(e) => setOther(e.target.value)}>
          <option value="">nothing</option>
          {worlds.data?.worlds.filter((w) => w !== worlds.data?.current).map((w) => <option key={w}>{w}</option>)}
        </select>
      </label>
      {labs[0].years.length === 0 && <p className="dim">No yearly snapshot yet: the first comes when year 2 begins.</p>}
      <div className="grid three">
        {MEASURES.map(([key, title, hint, pct]) => (
          <Card key={key} title={title} hint={hint}>
            <LineChart loading={loading} xName="year" percent={pct} yMin={0} yMax={pct ? 1 : undefined}
              lines={labs.map((lab, i) => ({
                name: lab.world, color: series(i + 1),
                data: lab.years.filter((y) => y[key] !== null && y[key] !== undefined).map((y) => [y.year, Number(y[key])] as [number, number]),
              }))} />
          </Card>
        ))}
      </div>
    </>
  );
}
