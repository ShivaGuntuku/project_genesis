"""The overview page."""

import json
from pathlib import Path

from fastapi import FastAPI

from genesis.dashboard.common import (
    MAJOR_EVENTS,
    World,
    event_item_of,
    names,
)
from genesis.history import events as ev


def register(app: FastAPI, world: World) -> None:
    event_item = event_item_of(world)

    @app.get("/api/overview")
    def overview():
        store = world.open()
        conn = store.conn
        try:
            meta = store.meta()
            tick = int(meta["tick"])
            last_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            saved_at = meta.get("saved_at")
            running = bool(last_run and last_run["ended_at"] is None)
            people = conn.execute(
                "SELECT COUNT(*) total, SUM(alive) alive, MAX(generation) gens, "
                "SUM(parent_a IS NOT NULL) born FROM agents"
            ).fetchone()
            deaths = dict(
                conn.execute(
                    "SELECT cause_of_death, COUNT(*) FROM agents WHERE alive = 0 "
                    "GROUP BY cause_of_death"
                ).fetchall()
            )
            msg = conn.execute(
                "SELECT COUNT(*) n, SUM(line IS NOT NULL) voiced, SUM(truthful = 0) lies "
                "FROM messages"
            ).fetchone()
            today = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE tick >= ?", (max(0, tick - 24),)
            ).fetchone()[0]
            llm = store.llm_stats()
            recent_llm = (
                conn.execute(
                    "SELECT AVG(latency_ms) FROM (SELECT latency_ms FROM llm_calls "
                    "WHERE outcome IN ('ok','rejected') ORDER BY id DESC LIMIT 100)"
                ).fetchone()[0]
                if store._has_table("llm_calls")
                else None
            )
            n = names(conn)
            placeholders = ",".join("?" * len(MAJOR_EVENTS))
            feed_rows = conn.execute(
                f"SELECT * FROM events WHERE type IN ({placeholders}) ORDER BY id DESC LIMIT 40",
                MAJOR_EVENTS,
            ).fetchall()
            feed = [event_item(store._row_to_event(r), n) for r in feed_rows]
            population = [
                {"day": world.day(t), "alive": v.get("population.alive", 0)}
                for t, v in sorted(store.metrics(["population.alive"], max_points=400).items())
            ]
            counts = json.loads(meta.get("event_counts", "{}"))
            groups_now = (
                conn.execute("SELECT COUNT(*) FROM groups WHERE ended_tick IS NULL").fetchone()[0]
                if store._has_table("groups")
                else 0
            )
        finally:
            store.close()
        size = sum(
            Path(str(world.path) + s).stat().st_size
            for s in ("", "-wal")
            if Path(str(world.path) + s).exists()
        )
        return {
            "world": world.config.name,
            "seed": world.config.seed,
            "tick": tick,
            "clock": world.clock(tick),
            "day": world.day(tick),
            "savedAt": saved_at,
            "running": running,
            "lastRun": dict(last_run) if last_run else None,
            "llmEnabled": world.config.llm.enabled,
            "people": {
                "alive": people["alive"] or 0,
                "ever": people["total"],
                "born": people["born"] or 0,
                "generations": (people["gens"] or 0) + 1,
                "deaths": deaths,
            },
            "messages": {
                "total": msg["n"],
                "voiced": msg["voiced"] or 0,
                "lies": msg["lies"] or 0,
                "lastDay": today,
            },
            "llm": {**llm, "recentMs": recent_llm},
            "dbBytes": size,
            "conflict": {
                "fights": counts.get(ev.FIGHT, 0),
                "killed": deaths.get("killed", 0),
                "groups": groups_now,
                "groupsEver": counts.get(ev.FOUNDED_GROUP, 0),
            },
            "feed": feed,
            "population": population,
        }
