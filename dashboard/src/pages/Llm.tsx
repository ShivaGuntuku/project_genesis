import { useState } from "react";
import { useApi, type LlmCall } from "../api";
import { ColumnChart, LineChart, series, token } from "../chart";
import { CallModal, Card, pct, Status } from "../components";

interface LlmData {
  kinds: { kind: string; n: number; ok: number; invalid: number; rejected: number; failed: number; avg_ms: number; p95_ms: number | null }[];
  days: { day: number; calls: number; ok: number; avgMs: number }[];
  slowest: LlmCall[]; failures: LlmCall[];
  perMinute: { minute: string; calls: number }[]; maxPerMinute: number;
}

const KIND_WORDS: Record<string, string> = {
  greet: "greeting", turn: "conversation turn", reply: "reply", ask: "asking for help",
  request: "give or refuse", info: "truth or lie", threat: "give in or stand firm",
  reflect: "nightly reflection", mate: "have a child?", name: "naming a child",
};

export default function Llm() {
  const { data, error, loading } = useApi<LlmData>("/api/llm");
  const [open, setOpen] = useState<number | null>(null);
  if (!data) return <Status error={error} loading={loading} />;
  const calls = (list: LlmCall[]) => (
    <table className="data">
      <thead><tr><th>When</th><th>Who</th><th>Kind</th><th>Result</th><th className="num">ms</th></tr></thead>
      <tbody>{list.map((c) => (
        <tr key={c.id} onClick={() => setOpen(c.id)} style={{ cursor: "pointer" }}>
          <td className="dim">{c.clock}</td><td>{c.agent}{c.other ? ` → ${c.other}` : ""}</td><td>{c.kind}</td>
          <td>{c.outcome === "ok" ? `“${c.say ?? ""}”` : <><span className="tag bad">{c.outcome}</span> <span className="dim">{c.error}</span></>}</td>
          <td className="num">{c.ms}</td>
        </tr>
      ))}</tbody>
    </table>
  );
  return (
    <>
      <h1>LLM monitor</h1>
      <p className="sub">Every call to the local model: what it was asked, whether the answer was usable, and how fast. Click a call to see its full prompt and raw answer.</p>
      <div className="card" style={{ overflowX: "auto" }}>
        <table className="data">
          <thead><tr><th>Decision</th><th className="num">Calls</th><th className="num">Valid</th><th className="num">Invalid</th>
            <th className="num">Rejected by the world</th><th className="num">Failed</th><th className="num">Average ms</th><th className="num">95% under ms</th></tr></thead>
          <tbody>{data.kinds.map((k) => (
            <tr key={k.kind}><td>{KIND_WORDS[k.kind] ?? k.kind} <span className="dim">({k.kind})</span></td>
              <td className="num">{k.n}</td><td className="num">{pct(k.ok / k.n)}</td><td className="num">{k.invalid || ""}</td>
              <td className="num">{k.rejected || ""}</td><td className="num">{k.failed || ""}</td>
              <td className="num">{Math.round(k.avg_ms ?? 0)}</td><td className="num">{k.p95_ms ?? ""}</td></tr>
          ))}</tbody>
        </table>
        {data.kinds.length === 0 && <p className="dim">No LLM calls: this world runs on rules (create one with `genesis new --llm`).</p>}
      </div>
      <div style={{ marginTop: 14 }}>
        <Card title="Load: calls per real minute" hint={data.maxPerMinute
          ? `governor maximum ${data.maxPerMinute}/min: chatter up to 90%, hard choices up to 100%, never above`
          : "no governor limit set (llm.max_calls_per_minute = 0)"}>
          <LineChart loading={loading} xName="minute" yMin={0} lines={[
            { name: "calls", color: series(1), data: data.perMinute.map((m, i) => [i + 1, m.calls]) },
            ...(data.maxPerMinute ? [
              { name: "maximum", color: token("--critical"), data: data.perMinute.map((_, i) => [i + 1, data.maxPerMinute] as [number, number]) },
              { name: "90%", color: token("--muted"), data: data.perMinute.map((_, i) => [i + 1, Math.floor(data.maxPerMinute * 0.9)] as [number, number]) },
            ] : []),
          ]} />
          {data.perMinute.length > 0 && <p className="dim" style={{ fontSize: 12 }}>
            {data.perMinute[0].minute.replace("T", " ")} to {data.perMinute[data.perMinute.length - 1].minute.replace("T", " ")} (last {data.perMinute.length} minutes with calls)</p>}
        </Card>
      </div>
      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="Calls per day"><ColumnChart loading={loading} bars={[{ name: "calls", color: series(1), data: data.days.map((d) => [d.day, d.calls]) }]} /></Card>
        <Card title="Average answer time per day" hint="ms"><LineChart loading={loading} yMin={0} lines={[{ name: "ms", color: series(1), data: data.days.map((d) => [d.day, d.avgMs]) }]} /></Card>
        <Card title="Slowest calls">{calls(data.slowest)}</Card>
        <Card title="Recent problems" hint="invalid, rejected or failed: the rules decided instead">
          {data.failures.length ? calls(data.failures) : <p className="dim">None.</p>}
        </Card>
      </div>
      {open !== null && <CallModal id={open} onClose={() => setOpen(null)} />}
    </>
  );
}
