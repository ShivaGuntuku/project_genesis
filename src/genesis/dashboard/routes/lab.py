"""M10: the mind lab, comparing worlds, interviews, God Mode and SQL."""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

from genesis.config import WorldConfig
from genesis.dashboard.common import (
    World,
    event_item_of,
    names,
)
from genesis.history import events as ev
from genesis.persistence.db import WorldStore
from genesis.simulation.calendar import calendar_of


def _mind_rows(path: Path) -> dict:
    """M10 mind lab: year by year, what the minds of a world are like."""
    import re
    import sqlite3
    from collections import Counter

    from genesis.culture.words import leak_words

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        cal = calendar_of(WorldConfig.from_dict(json.loads(meta["config_json"])))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        years: dict[int, dict] = {}

        def row(y):
            return years.setdefault(
                y,
                {
                    "year": y,
                    "people": 0,
                    "beliefs": 0,
                    "goals": 0,
                    "ownGoals": 0,
                    "words": 0,
                    "leaks": 0,
                    "skill": 0.0,
                    "professions": Counter(),
                    "specialists": 0,
                    "storyWords": 0,
                    "valleyIdeas": {},
                },
            )

        if "yearly" in tables:
            for y, aid, data in conn.execute("SELECT year, agent_id, data_json FROM yearly"):
                d = json.loads(data)
                r = row(y)
                if aid == 0:
                    r["valleyIdeas"] = {
                        k: v.get("ideas", []) for k, v in d.get("valleys", {}).items()
                    }
                    continue
                r["people"] += 1
                skills = d.get("skills", {})
                r["skill"] += max([v for k, v in skills.items() if k != "foraging"] or [0])
                r["professions"][d.get("profession") or "none"] += 1
                r["specialists"] += bool(d.get("profession"))
                r["storyWords"] += len((d.get("story") or "").split())
        if "memories" in tables:
            for (tick,) in conn.execute("SELECT tick FROM memories WHERE kind = 'belief'"):
                row(cal.year(tick))["beliefs"] += 1
        for tick, data in conn.execute(
            "SELECT tick, data_json FROM events WHERE type = 'goal_set'"
        ):
            r = row(cal.year(tick))
            r["goals"] += 1
            r["ownGoals"] += json.loads(data).get("how") == "own words"
        for tick, line in conn.execute("SELECT tick, line FROM messages WHERE line IS NOT NULL"):
            r = row(cal.year(tick))
            r["words"] += len(re.findall(r"[a-z]+", line.lower()))
            r["leaks"] += len(leak_words(line))
        out = []
        for y in sorted(years):
            r = years[y]
            people = max(1, r["people"])
            ideas = [set(v) for v in r["valleyIdeas"].values()]
            every = set().union(*ideas) if ideas else set()
            shared = set.intersection(*ideas) if len(ideas) > 1 else every
            out.append(
                {
                    "year": y,
                    "people": r["people"],
                    "beliefsPerPerson": round(r["beliefs"] / people, 2),
                    "ownGoalShare": round(r["ownGoals"] / r["goals"], 3) if r["goals"] else None,
                    "leakPerThousand": round(1000 * r["leaks"] / r["words"], 2)
                    if r["words"]
                    else None,
                    "bestSkill": round(r["skill"] / people, 3) if r["people"] else None,
                    "specialistShare": round(r["specialists"] / people, 3) if r["people"] else None,
                    "storyWords": round(r["storyWords"] / people, 1) if r["people"] else None,
                    "valleysApart": round(1 - len(shared) / len(every), 3) if every else None,
                    "professions": dict(r["professions"]),
                }
            )
        return {"world": path.stem, "years": out}
    finally:
        conn.close()


def register(app: FastAPI, world: World) -> None:
    event_item = event_item_of(world)

    @app.get("/api/mindlab")
    def mind_lab():
        return {"worlds": [_mind_rows(world.path)]}

    @app.get("/api/mindlab/compare")
    def mind_lab_compare(other: str):
        other_path = _sibling(other)
        return {"worlds": [_mind_rows(world.path), _mind_rows(other_path)]}

    def _sibling(name: str) -> Path:
        path = world.path.parent / f"{name}.sqlite"
        if not path.exists() or path.resolve().parent != world.path.resolve().parent:
            raise HTTPException(404, f"No world {name!r} next to this one")
        return path

    @app.get("/api/worlds")
    def worlds_here():
        return {
            "current": world.path.stem,
            "worlds": sorted(p.stem for p in world.path.parent.glob("*.sqlite")),
        }

    @app.get("/api/compare")
    def compare(other: str):
        from genesis.god.compare import ROWS, summary

        other_path = _sibling(other)
        series = {}
        for path in (world.path, other_path):
            st = WorldStore(path, readonly=True)
            try:
                cal = calendar_of(WorldConfig.from_dict(json.loads(st.meta()["config_json"])))
                series[path.stem] = [
                    [cal.day(t), v.get("population.alive", 0)]
                    for t, v in sorted(st.metrics(["population.alive"], max_points=400).items())
                ]
            finally:
                st.close()
        return {
            "rows": [{"key": k, "label": label} for k, label in ROWS],
            "worlds": [summary(world.path), summary(other_path)],
            "series": series,
        }

    @app.post("/api/agents/{agent_id}/interview")
    def interview(agent_id: int, body: dict):
        from genesis.dashboard.interview import ask

        question = " ".join(str(body.get("question", "")).split())[:300]
        if not question:
            raise HTTPException(400, "ask something")
        return ask(world, agent_id, question, body.get("year"))

    @app.get("/api/god")
    def god_view():
        from genesis.god import interventions as god

        store = world.open()
        try:
            n = names(store.conn)
            queue = []
            if store._has_table("god_queue"):
                queue = [
                    {
                        "id": r[0],
                        "created": r[1],
                        "command": json.loads(r[2]),
                        "appliedTick": r[3],
                        "applied": world.clock(r[3]),
                        "event": r[4],
                        "error": r[5],
                    }
                    for r in store.conn.execute("SELECT * FROM god_queue ORDER BY id DESC")
                ]
            done = [
                event_item(store._row_to_event(r), n)
                for r in store.conn.execute(
                    "SELECT * FROM events WHERE type = ? ORDER BY id DESC LIMIT 100",
                    (ev.INTERVENTION,),
                )
            ]
            from genesis.simulation import service

            return {
                "queue": queue,
                "done": done,
                "commands": list(god.COMMANDS),
                "valleys": [s.name for s in world.config.starts] or ["valley"],
                "running": service.read_status(world.path).get("state") == "running",
            }
        finally:
            store.close()

    @app.post("/api/god")
    def god_command(body: dict):
        import sqlite3

        from genesis.god import interventions as god

        if body.get("act") not in god.COMMANDS:
            raise HTTPException(400, f"commands: {', '.join(god.COMMANDS)}")
        conn = sqlite3.connect(str(world.path), timeout=30)
        try:
            qid = god.enqueue(conn, body)
        finally:
            conn.close()
        return {"queued": qid}

    @app.post("/api/sql")
    def sql_console(body: dict):
        import sqlite3
        import time as _time

        query = str(body.get("query", "")).strip().rstrip(";")
        if not query.lower().startswith(("select", "with")) or ";" in query:
            raise HTTPException(400, "one SELECT (or WITH ... SELECT) at a time")
        conn = sqlite3.connect(f"file:{world.path}?mode=ro", uri=True, timeout=10)
        deadline = _time.monotonic() + 5
        conn.set_progress_handler(lambda: 1 if _time.monotonic() > deadline else 0, 10_000)
        try:
            cur = conn.execute(query)
            columns = [c[0] for c in cur.description or []]
            rows = cur.fetchmany(501)
        except sqlite3.Error as e:
            raise HTTPException(400, str(e)) from e
        finally:
            conn.close()
        return {"columns": columns, "rows": [list(r) for r in rows[:500]], "more": len(rows) > 500}
