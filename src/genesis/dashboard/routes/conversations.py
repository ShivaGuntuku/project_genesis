"""Conversations, pairs and the communication statistics."""

import json

from fastapi import FastAPI, HTTPException, Query

from genesis.dashboard.common import (
    PAGE,
    World,
    names,
)
from genesis.history.describe import describe_message


def register(app: FastAPI, world: World) -> None:
    @app.get("/api/conversations")
    def conversations(
        agent: int | None = None,
        other: int | None = None,
        act: str | None = None,
        source: str | None = None,
        lies: bool = False,
        q: str | None = None,
        day_from: int | None = None,
        day_to: int | None = None,
        page: int = Query(0, ge=0),
    ):
        where, params = [], []
        if agent is not None and other is not None:
            where.append("((from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?))")
            params += [agent, other, other, agent]
        elif agent is not None:
            where.append("(from_id = ? OR to_id = ?)")
            params += [agent, agent]
        if act:
            where.append("act = ?")
            params.append(act)
        if source in ("llm", "rules"):
            where.append("source = ?")
            params.append(source)
        if lies:
            where.append("truthful = 0")
        if q:
            where.append("line LIKE ?")
            params.append(f"%{q}%")
        cal = world.calendar
        if day_from is not None:  # first tick of that day
            where.append("tick >= ?")
            params.append(max(0, -(-((day_from - 1) * 24 - cal.start_hour) // cal.hours_per_tick)))
        if day_to is not None:
            where.append("tick < ?")
            params.append(-(-(day_to * 24 - cal.start_hour) // cal.hours_per_tick))
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        store = world.open()
        conn = store.conn
        try:
            grouped = (
                "SELECT tick, MIN(from_id, to_id) lo, MAX(from_id, to_id) hi, "
                f"MIN(event_id) first FROM messages{clause} GROUP BY tick, lo, hi"
            )
            total = conn.execute(f"SELECT COUNT(*) FROM ({grouped})", params).fetchone()[0]
            keys = conn.execute(
                grouped + " ORDER BY first DESC LIMIT ? OFFSET ?", [*params, PAGE, page * PAGE]
            ).fetchall()
            threads = thread_details(conn, keys)
        finally:
            store.close()
        return {"total": total, "page": page, "pageSize": PAGE, "threads": threads}

    def thread_details(conn, keys) -> list[dict]:
        if not keys:
            return []
        n = names(conn)
        conds, params = [], []
        for k in keys:
            conds.append(
                "(m.tick = ? AND MIN(m.from_id, m.to_id) = ? AND MAX(m.from_id, m.to_id) = ?)"
            )
            params += [k["tick"], k["lo"], k["hi"]]
        rows = conn.execute(
            "SELECT m.*, e.data_json FROM messages m JOIN events e ON e.id = m.event_id "
            f"WHERE {' OR '.join(conds)} ORDER BY m.event_id",
            params,
        ).fetchall()
        ids = [r["event_id"] for r in rows]
        effects: dict[int, list] = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for h in conn.execute(
                f"SELECT * FROM relationships_history WHERE event_id IN ({marks}) ORDER BY rowid",
                ids,
            ):
                effects.setdefault(h["event_id"], []).append(
                    {
                        "who": n.get(h["source_id"], "?"),
                        "about": n.get(h["target_id"], "?"),
                        "dimension": h["dimension"],
                        "delta": round(h["new"] - h["old"], 3),
                    }
                )
        by_key: dict[tuple, list] = {}
        for r in rows:
            lo, hi = sorted((r["from_id"], r["to_id"]))
            data = json.loads(r["data_json"])
            payload = json.loads(r["payload_json"])
            by_key.setdefault((r["tick"], lo, hi), []).append(
                {
                    "id": r["event_id"],
                    "from": r["from_id"],
                    "to": r["to_id"],
                    "fromName": n.get(r["from_id"], "?"),
                    "toName": n.get(r["to_id"], "?"),
                    "act": r["act"],
                    "answer": payload.get("answer"),
                    "text": describe_message(
                        r["act"],
                        payload,
                        n.get(r["from_id"], "?"),
                        n.get(r["to_id"], "?"),
                        bool(r["truthful"]),
                        n,
                    ),
                    "line": r["line"],
                    "thought": data.get("thought"),
                    "source": r["source"],
                    "truthful": bool(r["truthful"]),
                    "replyTo": r["reply_to"],
                    "effects": effects.get(r["event_id"], []),
                }
            )
        out = []
        for k in keys:
            messages = by_key.get((k["tick"], k["lo"], k["hi"]), [])
            out.append(
                {
                    "key": f"{k['tick']}-{k['lo']}-{k['hi']}",
                    "tick": k["tick"],
                    "clock": world.clock(k["tick"]),
                    "a": {"id": k["lo"], "name": n.get(k["lo"], "?")},
                    "b": {"id": k["hi"], "name": n.get(k["hi"], "?")},
                    "messages": messages,
                    "voiced": sum(1 for m in messages if m["line"]),
                }
            )
        return out

    @app.get("/api/pair")
    def pair(a: int, b: int):
        """Two agents' feelings for each other over time, and their history in numbers."""
        store = world.open()
        conn = store.conn
        try:
            n = names(conn)
            if a not in n or b not in n:
                raise HTTPException(404, "No such agent")
            curves = {}
            for src, dst in ((a, b), (b, a)):
                for dim in ("affinity", "trust", "grievance"):
                    rows = conn.execute(
                        "SELECT tick, old, new FROM relationships_history WHERE source_id = ? "
                        "AND target_id = ? AND dimension = ? ORDER BY rowid",
                        (src, dst, dim),
                    ).fetchall()
                    points = []
                    if rows:
                        points.append([rows[0]["tick"], round(rows[0]["old"], 3)])
                    points += [[r["tick"], round(r["new"], 3)] for r in rows]
                    curves[f"{src}-{dst}-{dim}"] = points
            current = {
                f"{r['source_id']}-{r['target_id']}": {
                    "label": r["label"],
                    "affinity": r["affinity"],
                    "trust": r["trust"],
                }
                for r in conn.execute(
                    "SELECT * FROM relationships WHERE (source_id = ? AND target_id = ?) "
                    "OR (source_id = ? AND target_id = ?)",
                    (a, b, b, a),
                )
            }
            counts = dict(
                conn.execute(
                    "SELECT act, COUNT(*) FROM messages WHERE (from_id = ? AND to_id = ?) "
                    "OR (from_id = ? AND to_id = ?) GROUP BY act",
                    (a, b, b, a),
                ).fetchall()
            )
            tick = int(store.meta()["tick"])
        finally:
            store.close()
        return {
            "a": {"id": a, "name": n[a]},
            "b": {"id": b, "name": n[b]},
            "curves": curves,
            "current": current,
            "acts": counts,
            "startHour": world.config.start_hour,
            "hoursPerTick": world.calendar.hours_per_tick,
            "tick": tick,
        }

    # =================================================================
    # Communication over time
    # =================================================================

    @app.get("/api/language")
    def language():
        store = world.open()
        try:
            with world.lock:
                world.language.update(store.conn)
                agent_names = set(names(store.conn).values())
                return {
                    "series": world.language.series(),
                    "words": world.language.words(agent_names),
                    "names": {i: nm for i, nm in names(store.conn).items()},
                }
        finally:
            store.close()
