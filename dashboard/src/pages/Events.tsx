import { query, setParams, useApi, type EventItem } from "../api";
import { EventRow, Status } from "../components";

export default function Events({ params }: { params: URLSearchParams }) {
  const type = params.get("type") ?? "";
  const page = Number(params.get("page") ?? 0);
  const { data, error, loading } = useApi<{ rows: EventItem[]; types: string[] }>(`/api/events${query({ type, page })}`);
  return (
    <>
      <h1>Events</h1>
      <p className="sub">The world's whole history, newest first. "why?" follows the chain of causes back.</p>
      <div className="filters">
        <select value={type} onChange={(e) => setParams("#/events", { type: e.target.value })}>
          <option value="">All types</option>
          {data?.types.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
        </select>
        <button disabled={page === 0} onClick={() => setParams("#/events", { type, page: page - 1 })}>← Newer</button>
        <button disabled={!data || data.rows.length < 50} onClick={() => setParams("#/events", { type, page: page + 1 })}>Older →</button>
      </div>
      <Status error={error} loading={loading} empty={data?.rows.length === 0} />
      <div className="card"><ul className="feed">{data?.rows.map((e) => <EventRow key={e.id} e={e} />)}</ul></div>
    </>
  );
}
