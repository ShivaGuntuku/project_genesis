"""The LLM monitor."""

from fastapi import FastAPI, HTTPException, Query

from genesis.dashboard.common import (
    PAGE,
    World,
    names,
)


def register(app: FastAPI, world: World) -> None:
    @app.get("/api/llm")
    def llm():
        store = world.open()
        conn = store.conn
        try:
            if not store._has_table("llm_calls"):
                return {"kinds": [], "days": [], "slowest": [], "failures": []}
            kinds = []
            for k in conn.execute(
                "SELECT kind, COUNT(*) n, SUM(outcome = 'ok') ok, SUM(outcome = 'invalid') invalid,"
                " SUM(outcome = 'rejected') rejected,"
                " SUM(outcome NOT IN ('ok', 'invalid', 'rejected')) failed,"
                " AVG(latency_ms) avg_ms FROM llm_calls GROUP BY kind ORDER BY n DESC"
            ):
                lat = [
                    x[0]
                    for x in conn.execute(
                        "SELECT latency_ms FROM llm_calls WHERE kind = ? AND outcome = 'ok' "
                        "ORDER BY latency_ms",
                        (k["kind"],),
                    )
                ]
                kinds.append(
                    {
                        **dict(k),
                        "p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
                    }
                )
            cal = world.calendar
            days = [
                {"day": d, "calls": c, "ok": ok, "avgMs": round(ms or 0)}
                for d, c, ok, ms in conn.execute(
                    "SELECT (? + tick * ?) / 24 + 1 d, COUNT(*), SUM(outcome = 'ok'), "
                    "AVG(latency_ms) FROM llm_calls GROUP BY d ORDER BY d",
                    (cal.start_hour, cal.hours_per_tick),
                )
            ]
            n = names(conn)

            def short(c):
                return {
                    "id": c["id"],
                    "clock": world.clock(c["tick"]),
                    "agent": n.get(c["agent_id"]),
                    "other": n.get(c["other_id"]),
                    "kind": c["kind"],
                    "model": c["model"],
                    "outcome": c["outcome"],
                    "error": c["error"],
                    "ms": c["latency_ms"],
                    "choice": c["choice"],
                    "say": c["say"],
                    "thought": c["thought"],
                }

            slowest = [
                short(c)
                for c in conn.execute("SELECT * FROM llm_calls ORDER BY latency_ms DESC LIMIT 10")
            ]
            failures = [
                short(c)
                for c in conn.execute(
                    "SELECT * FROM llm_calls WHERE outcome != 'ok' ORDER BY id DESC LIMIT 20"
                )
            ]
            # Real-time load: calls per wall-clock minute over the last two hours of calls.
            minutes = [
                {"minute": m, "calls": c}
                for m, c in conn.execute(
                    "SELECT substr(created_at, 1, 16) m, COUNT(*) FROM llm_calls "
                    "WHERE id > (SELECT MAX(id) FROM llm_calls) - 20000 "
                    "GROUP BY m ORDER BY m DESC LIMIT 120"
                )
            ][::-1]
            return {
                "kinds": kinds,
                "days": days,
                "slowest": slowest,
                "failures": failures,
                "perMinute": minutes,
                "maxPerMinute": world.config.llm.max_calls_per_minute,
            }
        finally:
            store.close()

    @app.get("/api/llm/calls")
    def llm_calls(agent: int | None = None, kind: str | None = None, page: int = Query(0, ge=0)):
        store = world.open()
        try:
            if not store._has_table("llm_calls"):
                return {"total": 0, "rows": []}
            where, params = [], []
            if agent is not None:
                where.append("agent_id = ?")
                params.append(agent)
            if kind:
                where.append("kind = ?")
                params.append(kind)
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            total = store.conn.execute(
                f"SELECT COUNT(*) FROM llm_calls{clause}", params
            ).fetchone()[0]
            n = names(store.conn)
            rows = store.conn.execute(
                f"SELECT * FROM llm_calls{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, PAGE, page * PAGE],
            ).fetchall()
            return {
                "total": total,
                "pageSize": PAGE,
                "rows": [
                    {
                        "id": c["id"],
                        "clock": world.clock(c["tick"]),
                        "agent": n.get(c["agent_id"]),
                        "other": n.get(c["other_id"]),
                        "kind": c["kind"],
                        "model": c["model"],
                        "outcome": c["outcome"],
                        "error": c["error"],
                        "ms": c["latency_ms"],
                        "choice": c["choice"],
                        "say": c["say"],
                        "thought": c["thought"],
                        "eventId": c["event_id"],
                    }
                    for c in rows
                ],
            }
        finally:
            store.close()

    @app.get("/api/llm/calls/{call_id}")
    def llm_call(call_id: int):
        store = world.open()
        try:
            c = store.conn.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
            if c is None:
                raise HTTPException(404, "No such call")
            return {**dict(c), "clock": world.clock(c["tick"])}
        finally:
            store.close()
