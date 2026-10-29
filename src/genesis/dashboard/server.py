"""`genesis dashboard <world>`: the observer dashboard (doc 12), first version.

A separate, read-only process: it opens the world file read-only (WAL lets it read while
`genesis run` or `genesis view` writes), so it can start, stop or crash without touching
the simulation. Every page is a few indexed SQL queries; communication statistics are
updated incrementally.

Conversations: every message between the same two agents in the same tick belongs to one
conversation (a greeting and its replies, a request and its answer).
"""

import asyncio
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from genesis.config import WorldConfig
from genesis.dashboard.language import LanguageStats, load_dictionary
from genesis.history import events as ev
from genesis.history.describe import clock, describe, describe_message
from genesis.persistence.db import WorldStore
from genesis.simulation.calendar import calendar_of
from genesis.society import family

PAGE = 25
MAJOR_EVENTS = (
    ev.DISASTER,
    ev.MIGRATED,
    ev.INVENTED,
    ev.CONCEPT_LOST,
    ev.GOAL_REACHED,
    ev.BUILT,
    ev.FIGHT,
    ev.FOUNDED_GROUP,
    ev.LEFT_GROUP,
    ev.LEADER_CHALLENGED,
    ev.LEADER_CHANGED,
    ev.WORLD_CHANGED,
    ev.BIRTH,
    ev.DIED,
    ev.CONCEIVED,
    ev.GRIEVED,
    ev.LIE_DISCOVERED,
    ev.REFLECTED,
    ev.LIFE_STAGE,
    ev.PREGNANCY_LOST,
    ev.RELATION_CHANGED,
)


def dashboard_dist() -> Path:
    override = os.environ.get("GENESIS_DASHBOARD_DIST")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "dashboard" / "dist"


class World:
    """Read-only access to one world file, plus cached statistics."""

    def __init__(self, path: Path):
        self.path = Path(path)
        store = self.open()
        meta = store.meta()
        self.config = WorldConfig.from_dict(json.loads(meta["config_json"]))
        store.close()
        self.calendar = calendar_of(self.config)
        self.language = LanguageStats(self.calendar, load_dictionary())
        self.lock = threading.Lock()

    def open(self) -> WorldStore:
        if not self.path.exists():
            raise HTTPException(404, f"No world file at {self.path}")
        return WorldStore(self.path, readonly=True)

    def clock(self, tick: int | None) -> str | None:
        return None if tick is None else clock(tick, self.calendar)

    def day(self, tick: int) -> int:
        return self.calendar.day(tick)


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


def create_app(path: Path) -> FastAPI:
    world = World(path)
    app = FastAPI(title="Genesis dashboard")

    # =================================================================
    # Helpers
    # =================================================================

    def names(conn) -> dict[int, str]:
        return {r[0]: r[1] for r in conn.execute("SELECT id, name FROM agents")}

    def event_item(e, names_):
        act = e.data.get("act") if e.type == ev.MESSAGE else None
        return {
            "id": e.id,
            "tick": e.tick,
            "clock": world.clock(e.tick),
            "type": e.type,
            "act": act,
            "text": describe(e, names_),
            "actors": list(e.actors),
            "targets": list(e.targets),
            "lie": e.type == ev.MESSAGE and e.data.get("truthful") is False,
            "thought": e.data.get("thought"),
            "hasCause": e.cause_id is not None,
            "god": e.origin == ev.GOD,  # M9: the creator's hand (highlighted)
        }

    def lineage_objects(conn) -> dict[int, SimpleNamespace]:
        out = {}
        for r in conn.execute("SELECT id, parent_a, parent_b, state_json FROM agents"):
            state = json.loads(r["state_json"])
            out[r["id"]] = SimpleNamespace(
                id=r["id"],
                parents=tuple(p for p in (r["parent_a"], r["parent_b"]) if p),
                partner=state.get("partner"),
            )
        return out

    # =================================================================
    # Overview
    # =================================================================

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

    # =================================================================
    # Conversations
    # =================================================================

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

    # =================================================================
    # Agents
    # =================================================================

    @app.get("/api/agents")
    def agents():
        store = world.open()
        conn = store.conn
        try:
            tick = int(store.meta()["tick"])
            n = names(conn)
            sent = dict(conn.execute("SELECT from_id, COUNT(*) FROM messages GROUP BY from_id"))
            voiced = dict(
                conn.execute(
                    "SELECT from_id, COUNT(*) FROM messages WHERE line IS NOT NULL GROUP BY from_id"
                )
            )
            friends = dict(
                conn.execute(
                    "SELECT source_id, COUNT(*) FROM relationships "
                    "WHERE label IN ('friend', 'close friend') GROUP BY source_id"
                )
            )
            rivals = dict(
                conn.execute(
                    "SELECT source_id, COUNT(*) FROM relationships "
                    "WHERE label IN ('rival', 'enemy') GROUP BY source_id"
                )
            )
            beliefs = (
                dict(
                    conn.execute(
                        "SELECT agent_id, COUNT(*) FROM memories WHERE kind = 'belief' "
                        "GROUP BY agent_id"
                    )
                )
                if store._has_table("memories")
                else {}
            )
            rows = []
            for r in conn.execute("SELECT * FROM agents ORDER BY id"):
                state = json.loads(r["state_json"])
                end = r["died_tick"] if r["died_tick"] is not None else tick
                rows.append(
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "alive": bool(r["alive"]),
                        "stage": r["stage"],
                        "generation": r["generation"],
                        "ageDays": world.calendar.age_days(end - r["born_tick"]),
                        "parents": [n.get(p) for p in (r["parent_a"], r["parent_b"]) if p],
                        "partner": n.get(state.get("partner")),
                        "children": len(state.get("children", [])),
                        "causeOfDeath": r["cause_of_death"],
                        "died": world.clock(r["died_tick"]),
                        "messages": sent.get(r["id"], 0),
                        "voiced": voiced.get(r["id"], 0),
                        "friends": friends.get(r["id"], 0),
                        "rivals": rivals.get(r["id"], 0),
                        "beliefs": beliefs.get(r["id"], 0),
                        "story": state.get("story", ""),
                    }
                )
        finally:
            store.close()
        return rows

    @app.get("/api/agents/{agent_id}")
    def agent(agent_id: int):
        store = world.open()
        conn = store.conn
        try:
            r = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if r is None:
                raise HTTPException(404, "No such agent")
            tick = int(store.meta()["tick"])
            n = names(conn)
            state = json.loads(r["state_json"])

            def genes_of(i):
                return {
                    g["gene"]: g["value"]
                    for g in conn.execute("SELECT gene, value FROM genes WHERE agent_id = ?", (i,))
                }

            parents = [p for p in (r["parent_a"], r["parent_b"]) if p]
            lineage = lineage_objects(conn)
            kin = family.family(lineage[agent_id], lineage)
            alive = dict(conn.execute("SELECT id, alive FROM agents"))
            skills = {
                s["skill"]: round(s["value"], 3)
                for s in conn.execute(
                    "SELECT skill, value FROM abilities WHERE agent_id = ?", (agent_id,)
                )
            }
            stories = [
                {
                    "clock": world.clock(e["tick"]),
                    "story": json.loads(e["data_json"]).get("story"),
                    "beliefs": json.loads(e["data_json"]).get("beliefs", []),
                    "thought": json.loads(e["data_json"]).get("thought"),
                    "eventId": e["id"],
                }
                for e in conn.execute(
                    "SELECT e.* FROM events e JOIN event_agents ea ON ea.event_id = e.id "
                    "WHERE ea.agent_id = ? AND ea.role = 'actor' AND e.type = ? ORDER BY e.id",
                    (agent_id, ev.REFLECTED),
                )
            ]
            relations = []
            for rel in conn.execute(
                "SELECT * FROM relationships WHERE source_id = ? ORDER BY affinity DESC",
                (agent_id,),
            ):
                back = conn.execute(
                    "SELECT label, affinity FROM relationships "
                    "WHERE source_id = ? AND target_id = ?",
                    (rel["target_id"], agent_id),
                ).fetchone()
                relations.append(
                    {
                        "id": rel["target_id"],
                        "name": rel["target_name"],
                        "alive": bool(alive.get(rel["target_id"], 0)),
                        "label": rel["label"],
                        "theirLabel": back["label"] if back else None,
                        "theirAffinity": back["affinity"] if back else None,
                        **{
                            k: round(rel[k], 3)
                            for k in (
                                "affinity",
                                "trust",
                                "respect",
                                "fear",
                                "gratitude",
                                "grievance",
                                "familiarity",
                            )
                        },
                        "family": kin.get(rel["target_id"]),
                    }
                )
            counts = conn.execute(
                "SELECT COUNT(*) n, SUM(line IS NOT NULL) voiced, SUM(truthful = 0) lies "
                "FROM messages WHERE from_id = ?",
                (agent_id,),
            ).fetchone()
            end = r["died_tick"] if r["died_tick"] is not None else tick
            return {
                "id": agent_id,
                "name": r["name"],
                "alive": bool(r["alive"]),
                "stage": r["stage"],
                "generation": r["generation"],
                "ageDays": world.calendar.age_days(end - r["born_tick"]),
                "born": world.clock(max(r["born_tick"], 0)) if r["born_tick"] >= 0 else None,
                "died": world.clock(r["died_tick"]),
                "causeOfDeath": r["cause_of_death"],
                "position": [r["x"], r["y"]],
                "action": r["current_action"],
                "needs": {k: round(r[k]) for k in ("health", "hunger", "thirst", "energy")},
                "carrying": state.get("inventory", {}),
                "story": state.get("story", ""),
                "stories": stories,
                "talent": state.get("talent"),
                "work": {k: round(v, 1) for k, v in state.get("work", {}).items()},
                "owes": [
                    {"to": d["to"], "name": n.get(d["to"]), "good": d["good"]}
                    for d in state.get("owes", [])
                ],
                "goal": (
                    {
                        **{k: state["goal"][k] for k in ("need", "text")},
                        "since": world.clock(state["goal"]["tick"]),
                        "ownWords": state["goal"].get("how") == "own words",
                    }
                    if state.get("goal")
                    else None
                ),
                "goals": [
                    {
                        "clock": world.clock(e["tick"]),
                        "type": e["type"],
                        "text": describe(store._row_to_event(e), n),
                        "eventId": e["id"],
                    }
                    for e in conn.execute(
                        "SELECT e.* FROM events e JOIN event_agents ea ON ea.event_id = e.id "
                        "WHERE ea.agent_id = ? AND ea.role = 'actor' AND e.type IN (?, ?, ?) "
                        "ORDER BY e.id",
                        (agent_id, ev.GOAL_SET, ev.GOAL_REACHED, ev.GOAL_ABANDONED),
                    )
                ],
                "partner": state.get("partner"),
                "pregnant": state.get("pregnant", False),
                "family": [
                    {
                        "id": i,
                        "name": n.get(i),
                        "relation": rel,
                        "alive": bool(alive.get(i, 0)),
                    }
                    for i, rel in sorted(kin.items(), key=lambda kv: kv[0])
                ],
                "genes": genes_of(agent_id),
                "parentGenes": [{"name": n.get(p), "genes": genes_of(p)} for p in parents],
                "skills": skills,
                "relationships": relations,
                "said": {
                    "messages": counts["n"],
                    "voiced": counts["voiced"] or 0,
                    "lies": counts["lies"] or 0,
                },
            }
        finally:
            store.close()

    @app.get("/api/agents/{agent_id}/memories")
    def memories(
        agent_id: int,
        kind: str | None = None,
        q: str | None = None,
        forgotten: bool = False,
        page: int = Query(0, ge=0),
    ):
        store = world.open()
        try:
            if not store._has_table("memories"):
                return {"total": 0, "rows": []}
            where, params = ["agent_id = ?"], [agent_id]
            if kind:
                where.append("kind = ?")
                params.append(kind)
            if q:
                where.append("text LIKE ?")
                params.append(f"%{q}%")
            if not forgotten:
                where.append("forgotten_tick IS NULL")
            clause = " AND ".join(where)
            total = store.conn.execute(
                f"SELECT COUNT(*) FROM memories WHERE {clause}", params
            ).fetchone()[0]
            rows = store.conn.execute(
                f"SELECT * FROM memories WHERE {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, 50, page * 50],
            ).fetchall()
            return {
                "total": total,
                "pageSize": 50,
                "rows": [
                    {
                        "id": m["id"],
                        "clock": world.clock(m["tick"]),
                        "kind": m["kind"],
                        "text": m["text"],
                        "importance": m["importance"],
                        "forgotten": world.clock(m["forgotten_tick"]),
                        "eventId": m["source_event_id"],
                    }
                    for m in rows
                ],
            }
        finally:
            store.close()

    @app.get("/api/agents/{agent_id}/timeline")
    def timeline(agent_id: int, page: int = Query(0, ge=0), major: bool = False):
        store = world.open()
        try:
            n = names(store.conn)
            sql = (
                "SELECT e.* FROM events e JOIN event_agents ea ON ea.event_id = e.id "
                "WHERE ea.agent_id = ?"
            )
            params: list = [agent_id]
            if major:
                sql += f" AND e.type IN ({','.join('?' * len(MAJOR_EVENTS))})"
                params += list(MAJOR_EVENTS)
            rows = store.conn.execute(
                sql + " GROUP BY e.id ORDER BY e.id DESC LIMIT ? OFFSET ?",
                [*params, 50, page * 50],
            ).fetchall()
            return [event_item(store._row_to_event(r), n) for r in rows]
        finally:
            store.close()

    # =================================================================
    # LLM monitor
    # =================================================================

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

    # =================================================================
    # Events and "why"
    # =================================================================

    @app.get("/api/events")
    def events(type: str | None = None, agent: int | None = None, page: int = Query(0, ge=0)):
        store = world.open()
        try:
            n = names(store.conn)
            sql = "SELECT e.* FROM events e"
            where, params = [], []
            if agent is not None:
                sql += " JOIN event_agents ea ON ea.event_id = e.id"
                where.append("ea.agent_id = ?")
                params.append(agent)
            if type:
                where.append("e.type = ?")
                params.append(type)
            if where:
                sql += " WHERE " + " AND ".join(where)
            rows = store.conn.execute(
                sql + " GROUP BY e.id ORDER BY e.id DESC LIMIT ? OFFSET ?",
                [*params, 50, page * 50],
            ).fetchall()
            types = [r[0] for r in store.conn.execute("SELECT DISTINCT type FROM events")]
            return {
                "rows": [event_item(store._row_to_event(r), n) for r in rows],
                "types": sorted(types),
            }
        finally:
            store.close()

    @app.get("/api/why/{event_id}")
    def why(event_id: int):
        store = world.open()
        try:
            n = names(store.conn)
            chain = store.cause_chain(event_id)
            if not chain:
                raise HTTPException(404, "No such event")
            return {
                "chain": [event_item(e, n) for e in reversed(chain)],
                "consequences": [event_item(e, n) for e in store.consequences(event_id)],
            }
        finally:
            store.close()

    # =================================================================
    # Groups and the social network (M7)
    # =================================================================

    @app.get("/api/groups")
    def groups_list():
        store = world.open()
        conn = store.conn
        try:
            if not store._has_table("groups"):
                return {"groups": [], "series": []}
            n = names(conn)
            group_types = (
                ev.FOUNDED_GROUP,
                ev.JOINED_GROUP,
                ev.LEFT_GROUP,
                ev.GROUP_ENDED,
                ev.LEADER_CHANGED,
                ev.LEADER_CHALLENGED,
                ev.ORDER_GIVEN,
                ev.ORDER_DONE,
                ev.GROUP_STORE,
                ev.TRESPASS,
            )
            marks = ",".join("?" * len(group_types))
            history: dict[int, list] = {}
            orders: dict[int, dict] = {}
            trespass: dict[int, int] = {}
            for r in conn.execute(
                f"SELECT * FROM events WHERE type IN ({marks}) ORDER BY id", group_types
            ):
                e = store._row_to_event(r)
                gid = e.data.get("group")
                if gid is None:
                    continue
                if e.type == ev.ORDER_GIVEN:
                    o = orders.setdefault(gid, {"obeyed": 0, "refused": 0, "done": 0, "lapsed": 0})
                    o["obeyed" if e.data.get("obeyed") else "refused"] += 1
                elif e.type == ev.ORDER_DONE and e.data.get("kind") != "contribute":
                    o = orders.setdefault(gid, {"obeyed": 0, "refused": 0, "done": 0, "lapsed": 0})
                    o["done" if e.data.get("done") else "lapsed"] += 1
                elif e.type == ev.TRESPASS:
                    trespass[gid] = trespass.get(gid, 0) + 1
                elif e.type != ev.GROUP_STORE:
                    history.setdefault(gid, []).append(event_item(e, n))

            fights: dict[int, dict] = {}
            member_of = {}
            for r in conn.execute("SELECT id, state_json FROM agents"):
                member_of[r["id"]] = json.loads(r["state_json"]).get("group_id")
            for r in conn.execute("SELECT * FROM events WHERE type = ?", (ev.FIGHT,)):
                e = store._row_to_event(r)
                a, b = member_of.get(e.actors[0]), member_of.get(e.targets[0])
                for gid in {a, b} - {None}:
                    f = fights.setdefault(gid, {"inside": 0, "with_outsiders": 0})
                    f["inside" if a == b else "with_outsiders"] += 1

            groups = []
            for g in conn.execute("SELECT * FROM groups ORDER BY id"):
                members = json.loads(g["members_json"])
                groups.append(
                    {
                        "id": g["id"],
                        "name": g["name"],
                        "founder": {"id": g["founder_id"], "name": n.get(g["founder_id"])},
                        "leader": {"id": g["leader_id"], "name": n.get(g["leader_id"])},
                        "members": [{"id": m, "name": n.get(m)} for m in members],
                        "home": [g["home_x"], g["home_y"]],
                        "store": g["store_food"],
                        "founded": world.clock(g["founded_tick"]),
                        "ended": world.clock(g["ended_tick"]),
                        "history": history.get(g["id"], [])[-60:],
                        "orders": orders.get(
                            g["id"], {"obeyed": 0, "refused": 0, "done": 0, "lapsed": 0}
                        ),
                        "fights": fights.get(g["id"], {"inside": 0, "with_outsiders": 0}),
                        "trespass": trespass.get(g["id"], 0),
                    }
                )
            wanted = [
                "groups.count",
                "groups.share_in_groups",
                "groups.largest",
                "social.communities",
                "social.density",
                "today.fight",
                "today.trespass",
            ]
            series = [
                {"day": world.day(t), **{k: v.get(k, 0) for k in wanted}}
                for t, v in sorted(store.metrics(wanted, max_points=400).items())
            ]
            return {"groups": groups, "series": series}
        finally:
            store.close()

    @app.get("/api/ideas")
    def ideas():
        """M8b: every concept the valley invented, who knows it now, and the story by year."""
        store = world.open()
        conn = store.conn
        try:
            if not store._has_table("concepts"):
                return {"concepts": [], "years": [], "recent": [], "structures": 0}
            n = names(conn)
            holders: dict[int, list] = {}
            for r in conn.execute("SELECT id, state_json FROM agents WHERE alive = 1"):
                for cid in json.loads(r["state_json"]).get("knowledge", []):
                    holders.setdefault(cid, []).append({"id": r["id"], "name": n.get(r["id"])})
            kinds = (ev.EXPERIMENTED, ev.INVENTED, ev.MADE, ev.BUILT, ev.LEARNED, ev.CONCEPT_LOST)
            marks = ",".join("?" * len(kinds))
            made: dict[int, int] = {}
            taught: dict[int, int] = {}
            years: dict[int, dict] = {}
            recent = []
            cal = world.calendar
            for r in conn.execute(
                f"SELECT * FROM events WHERE type IN ({marks}) ORDER BY id", kinds
            ):
                e = store._row_to_event(r)
                y = years.setdefault(
                    cal.year(e.tick),
                    {"tried": 0, "invented": 0, "made": 0, "learned": 0, "lost": 0},
                )
                cid = e.data.get("concept")
                if e.type == ev.EXPERIMENTED:
                    y["tried"] += 1
                    continue
                if e.type == ev.INVENTED:
                    y["invented"] += 1
                elif e.type in (ev.MADE, ev.BUILT):
                    if e.type == ev.MADE:
                        y["made"] += 1
                        made[cid] = made.get(cid, 0) + 1
                elif e.type == ev.LEARNED:
                    if not e.data.get("learned"):
                        continue
                    y["learned"] += 1
                    taught[cid] = taught.get(cid, 0) + 1
                elif e.type == ev.CONCEPT_LOST:
                    y["lost"] += 1
                if e.type != ev.MADE:
                    recent.append(event_item(e, n))
            structures: dict[int, int] = {}
            for r in conn.execute("SELECT concept_id FROM structures"):
                structures[r[0]] = structures.get(r[0], 0) + 1
            rows = list(conn.execute("SELECT * FROM concepts ORDER BY id"))
            by_id = {r["id"]: r["name"] for r in rows}
            concepts = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "function": r["function"],
                    "functions": json.loads(r["functions_json"]),
                    "recipe": json.loads(r["recipe_json"]),
                    "inventor": {"id": r["inventor_id"], "name": n.get(r["inventor_id"])},
                    "invented": world.clock(r["tick"]),
                    "idea": r["description"],
                    "sameAs": by_id.get(r["same_as"]),
                    "lost": world.clock(r["lost_tick"]),
                    "holders": holders.get(r["id"], []),
                    "made": made.get(r["id"], 0),
                    "taught": taught.get(r["id"], 0),
                    "structures": structures.get(r["id"], 0),
                }
                for r in rows
            ]
            return {
                "concepts": concepts,
                "years": [{"year": k, **v} for k, v in sorted(years.items())],
                "recent": recent[-80:],
                "structures": sum(structures.values()),
            }
        finally:
            store.close()

    @app.get("/api/goals")
    def goals_view():
        """M8c: what people strive for: current goals, and how goals end, by need."""
        store = world.open()
        conn = store.conn
        try:
            n = names(conn)
            current = []
            for r in conn.execute("SELECT id, state_json FROM agents WHERE alive = 1 ORDER BY id"):
                g = json.loads(r["state_json"]).get("goal")
                if g:
                    current.append(
                        {
                            "id": r["id"],
                            "name": n.get(r["id"]),
                            "need": g["need"],
                            "text": g["text"],
                            "since": world.clock(g["tick"]),
                            "ownWords": g.get("how") == "own words",
                        }
                    )
            kinds = (ev.GOAL_SET, ev.GOAL_REACHED, ev.GOAL_ABANDONED)
            by_need: dict[str, dict] = {}
            days: dict[str, list] = {}
            recent = []
            for r in conn.execute(
                "SELECT * FROM events WHERE type IN (?, ?, ?) ORDER BY id", kinds
            ):
                e = store._row_to_event(r)
                need = e.data.get("need")
                row = by_need.setdefault(need, {"set": 0, "reached": 0, "gaveUp": 0, "own": 0})
                if e.type == ev.GOAL_SET:
                    row["set"] += 1
                    row["own"] += e.data.get("how") == "own words"
                elif e.type == ev.GOAL_REACHED:
                    row["reached"] += 1
                    days.setdefault(need, []).append(e.data.get("days") or 0)
                else:
                    row["gaveUp"] += 1
                recent.append(event_item(e, n))
            for need, row in by_need.items():
                d = sorted(days.get(need, []))
                row["medianDays"] = d[len(d) // 2] if d else None
            return {
                "current": current,
                "needs": [{"need": k, **v} for k, v in sorted(by_need.items())],
                "recent": recent[-80:],
            }
        finally:
            store.close()

    @app.get("/api/economy")
    def economy_view():
        """M8d: trades, promises, money of their own, prices, talents and professions."""
        from genesis.agents import talents

        store = world.open()
        conn = store.conn
        try:
            n = names(conn)
            cal = world.calendar
            economy = json.loads(store.meta().get("economy", '{"currencies": []}'))
            currencies = economy.get("currencies", [])
            for c in currencies:
                c["since"] = world.clock(c["tick"])
            years: dict[int, dict] = {}
            goods: dict[str, dict] = {}
            prices = []
            recent = []

            def year(tick):
                return years.setdefault(
                    cal.year(tick),
                    {
                        "offers": 0,
                        "accepted": 0,
                        "indirect": 0,
                        "promises": 0,
                        "repaid": 0,
                        "broken": 0,
                        "healed": 0,
                    },
                )

            rows = conn.execute(
                "SELECT e.*, m.act FROM events e JOIN messages m ON m.event_id = e.id "
                "WHERE m.act IN ('trade', 'repay', 'heal', 'reply') ORDER BY e.id"
            )
            for r in rows:
                e = store._row_to_event(r)
                p = e.data.get("payload", {})
                act = e.data.get("act")
                y = year(e.tick)
                if act == "trade":
                    y["offers"] += 1
                elif act == "repay":
                    y["repaid"] += 1
                    recent.append(event_item(e, n))
                elif act == "heal":
                    y["healed"] += 1
                elif p.get("answer") == "accept_trade":
                    y["accepted"] += 1
                    if p.get("promise"):
                        y["promises"] += 1
                    else:
                        g = goods.setdefault(p["give"], {"given": 0, "indirect": 0})
                        g["given"] += 1
                        g["indirect"] += bool(p.get("indirect"))
                        y["indirect"] += bool(p.get("indirect"))
                    if p.get("price"):
                        prices.append(
                            {
                                "year": cal.year(e.tick),
                                "day": world.day(e.tick),
                                **p["price"],
                                "label": p["gets"]
                                if p["price"]["good"] == p["get"]
                                else p["gives"],
                            }
                        )
                    recent.append(event_item(e, n))
            for r in conn.execute(
                "SELECT * FROM events WHERE type IN (?, ?) ORDER BY id",
                (ev.PROMISE_BROKEN, ev.CURRENCY_EMERGED),
            ):
                e = store._row_to_event(r)
                if e.type == ev.PROMISE_BROKEN:
                    year(e.tick)["broken"] += 1
                recent.append(event_item(e, n))
            recent.sort(key=lambda x: x["id"])

            people = []
            for r in conn.execute(
                "SELECT id, name, state_json FROM agents WHERE alive = 1 ORDER BY id"
            ):
                s = json.loads(r["state_json"])
                skills = {
                    k["skill"]: k["value"]
                    for k in conn.execute(
                        "SELECT skill, value FROM abilities WHERE agent_id = ?", (r["id"],)
                    )
                }
                work = s.get("work", {})
                people.append(
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "talent": s.get("talent"),
                        "profession": talents.profession_of(work, skills),
                        "work": {k: round(v, 1) for k, v in work.items()},
                        "owes": len(s.get("owes", [])),
                    }
                )
            return {
                "currencies": currencies,
                "years": [{"year": k, **v} for k, v in sorted(years.items())],
                "goods": [
                    {"good": k, **v} for k, v in sorted(goods.items(), key=lambda x: -x[1]["given"])
                ],
                "prices": prices[-500:],
                "people": people,
                "recent": recent[-80:],
            }
        finally:
            store.close()

    @app.get("/api/history")
    def history_view():
        """M8e: the long view, year by year: valleys, ideas, money, disasters, migration and
        how much of our world leaks into their words."""
        import re

        from genesis.culture.words import EARTH_TERMS

        store = world.open()
        conn = store.conn
        try:
            n = names(conn)
            cal = world.calendar
            years: dict[int, dict] = {}
            if store._has_table("yearly"):
                for r in conn.execute(
                    "SELECT year, data_json FROM yearly WHERE agent_id = 0 ORDER BY year"
                ):
                    d = json.loads(r["data_json"])
                    valleys = d.get("valleys", {})
                    ideas = {k: set(v.get("ideas", [])) for k, v in valleys.items()}
                    every = set().union(*ideas.values()) if ideas else set()
                    shared = set.intersection(*ideas.values()) if len(ideas) > 1 else every
                    years[r["year"]] = {
                        "year": r["year"],
                        "valleys": {
                            k: {
                                "people": v.get("people", 0),
                                "ideas": len(v.get("ideas", [])),
                                "currency": v.get("currency"),
                            }
                            for k, v in valleys.items()
                        },
                        # Share of known ideas that not every valley has: 0 = all alike.
                        "apart": round(1 - len(shared) / len(every), 3) if every else 0.0,
                        "concepts": d.get("concepts", 0),
                        "lost": d.get("lost", 0),
                    }
            disasters = []
            migrations: dict[int, int] = {}
            for r in conn.execute(
                "SELECT * FROM events WHERE type IN (?, ?) ORDER BY id", (ev.DISASTER, ev.MIGRATED)
            ):
                e = store._row_to_event(r)
                if e.type == ev.MIGRATED:
                    y = cal.year(e.tick)
                    migrations[y] = migrations.get(y, 0) + 1
                elif e.data.get("started"):
                    disasters.append(
                        {
                            "year": cal.year(e.tick),
                            "kind": e.data.get("kind"),
                            "region": e.data.get("region"),
                            "days": e.data.get("days"),
                            "people": e.data.get("people"),
                            "text": describe(e, n),
                        }
                    )
            leak: dict[int, list] = {}
            words_re = re.compile(r"[a-z]+")
            texts = conn.execute(
                "SELECT tick, line FROM messages WHERE line IS NOT NULL "
                "UNION ALL SELECT tick, text FROM memories WHERE kind = 'belief'"
            )
            for tick, text in texts:
                words = words_re.findall((text or "").lower())
                row = leak.setdefault(cal.year(tick), [0, 0])
                row[0] += len(words)
                row[1] += sum(1 for w in words if w in EARTH_TERMS)
            return {
                "years": [years[k] for k in sorted(years)],
                "disasters": disasters,
                "migrations": [{"year": k, "count": v} for k, v in sorted(migrations.items())],
                "leaks": [
                    {
                        "year": k,
                        "words": v[0],
                        "earth": v[1],
                        "perThousand": round(1000 * v[1] / v[0], 2) if v[0] else 0,
                    }
                    for k, v in sorted(leak.items())
                ],
                "valleys": [
                    st.name or f"valley {i + 1}" for i, st in enumerate(world.config.starts)
                ]
                or ["valley"],
            }
        finally:
            store.close()

    # =================================================================
    # M8.5: the long run, the family tree, exports
    # =================================================================

    @app.get("/api/run")
    def run_status():
        from genesis.simulation import service

        st = service.read_status(world.path)
        st["control"] = service._read_control(world.path) if st else None
        st["snapshots"] = [p.name for p in service.snapshots(world.path)]
        return st

    @app.post("/api/run/{command}")
    def run_control(command: str):
        from genesis.simulation import service

        if command not in ("pause", "continue", "stop"):
            raise HTTPException(400, "pause, continue or stop")
        if not service.read_status(world.path).get("target_tick"):
            raise HTTPException(409, "this world has no long run (genesis start WORLD --years N)")
        service.control(world.path, {"continue": "run"}.get(command, command))
        return {"asked": command}

    @app.get("/api/family")
    def family_tree():
        store = world.open()
        try:
            people = []
            for r in store.conn.execute("SELECT * FROM agents ORDER BY id"):
                state = json.loads(r["state_json"])
                people.append(
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "generation": r["generation"],
                        "parents": [p for p in (r["parent_a"], r["parent_b"]) if p],
                        "alive": bool(r["alive"]),
                        "born": world.clock(max(r["born_tick"], 0))
                        if r["born_tick"] >= 0
                        else None,
                        "died": world.clock(r["died_tick"]),
                        "cause": r["cause_of_death"],
                        "talent": state.get("talent"),
                        "partner": state.get("partner"),
                    }
                )
            return {"people": people}
        finally:
            store.close()

    EXPORTS = {
        "agents": "SELECT id, name, generation, parent_a, parent_b, born_tick, died_tick, "
        "cause_of_death, alive, stage FROM agents ORDER BY id",
        "events": "SELECT id, tick, type, actors_json, targets_json, data_json, cause_event_id "
        "FROM events ORDER BY id",
        "messages": "SELECT event_id, tick, from_id, to_id, act, line, truthful FROM messages "
        "ORDER BY event_id",
        "llm_calls": "SELECT id, tick, agent_id, kind, outcome, error, choice, say, thought, "
        "latency_ms FROM llm_calls ORDER BY id",
        "ideas": "SELECT * FROM concepts ORDER BY id",
        "yearly": "SELECT year, agent_id, data_json FROM yearly ORDER BY year, agent_id",
        "metrics": "SELECT tick, name, value FROM metrics ORDER BY tick, name",
    }

    @app.get("/api/export/{name}.csv")
    def export(name: str):
        import csv
        import io

        from fastapi.responses import StreamingResponse

        sql = EXPORTS.get(name)
        if sql is None:
            raise HTTPException(404, f"exports: {', '.join(EXPORTS)}")
        store = world.open()
        try:
            cur = store.conn.execute(sql)
            columns = [c[0] for c in cur.description]
            rows = cur.fetchall()
        except Exception as e:  # e.g. a table an older world doesn't have
            store.close()
            raise HTTPException(404, str(e)) from e
        store.close()

        def stream():
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(columns)
            for i, row in enumerate(rows):
                writer.writerow(list(row))
                if i % 2000 == 1999:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
            yield buf.getvalue()

        filename = f"{world.path.stem}-{name}.csv"
        return StreamingResponse(
            stream(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # =================================================================
    # M10: health, the world map, the mind lab, interviews, God Mode, SQL, compare
    # =================================================================

    @app.get("/api/health")
    def health():
        import shutil
        import time as _time

        import httpx

        llm = world.config.llm
        ollama = None
        if llm.enabled:
            try:
                r = httpx.get(llm.url.rstrip("/") + "/api/tags", timeout=1.5)
                ollama = r.status_code == 200
            except httpx.HTTPError:
                ollama = False
        store = world.open()
        try:
            recent = []
            if store._has_table("llm_calls"):
                since = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.localtime(_time.time() - 600))
                recent = list(
                    store.conn.execute(
                        "SELECT latency_ms, outcome FROM llm_calls WHERE created_at >= ?", (since,)
                    )
                )
        finally:
            store.close()
        disk = shutil.disk_usage(world.path.parent)
        gpu = None
        if shutil.which("nvidia-smi"):  # review: GPU temperature, memory and load
            import subprocess

            try:
                out = (
                    subprocess.run(
                        [
                            "nvidia-smi",
                            "--query-gpu=name,temperature.gpu,memory.used,memory.total,"
                            "utilization.gpu",
                            "--format=csv,noheader,nounits",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    .stdout.strip()
                    .splitlines()
                )
                if out:
                    name, temp, used, total, load = [x.strip() for x in out[0].split(",")]
                    gpu = {
                        "name": name,
                        "tempC": int(temp),
                        "memMb": int(used),
                        "memTotalMb": int(total),
                        "loadPct": int(load),
                    }
            except (OSError, ValueError, subprocess.SubprocessError):
                gpu = None
        lat = [r[0] for r in recent if r[0]]
        return {
            "llm": llm.enabled,
            "ollama": ollama,
            "callsPerMinute": round(len(recent) / 10, 1),
            "latencyMs": round(sum(lat) / len(lat)) if lat else None,
            "failed": sum(1 for r in recent if r[1] != "ok"),
            "dbMb": round(world.path.stat().st_size / 1e6, 1),
            "diskFreeGb": round(disk.free / 1e9, 1),
            "limit": llm.max_calls_per_minute,
            "gpu": gpu,
        }

    def _land():
        """The map as it is now: the config's land, God's changes, burned woods."""
        from genesis.api.views import TERRAIN_CODES
        from genesis.world import disasters as dis
        from genesis.world.world import Position as P
        from genesis.world.world import TerrainType
        from genesis.world.world import World as Land

        store = world.open()
        try:
            meta = store.meta()
            tick = int(meta["tick"])
            god_state = json.loads(meta.get("god", "{}"))
            food = list(store.conn.execute("SELECT id, x, y, quantity FROM resources"))
            structures = (
                list(store.conn.execute("SELECT * FROM structures"))
                if store._has_table("structures")
                else []
            )
        finally:
            store.close()
        land = Land(world.config)
        for edit_ in god_state.get("terrain", []):
            half = edit_["size"] // 2
            for x in range(edit_["x"] - half, edit_["x"] - half + edit_["size"]):
                for y in range(edit_["y"] - half, edit_["y"] - half + edit_["size"]):
                    if land.is_valid_position(P(x, y)):
                        land.terrain[P(x, y)] = TerrainType(edit_["terrain"])
        if land.disasters:
            land.rebuild_terrain()
            land.set_burned(dis.conditions(land, land.calendar.day_index(tick))["burned"])
        tiles = [
            "".join(TERRAIN_CODES[land.terrain[P(x, y)].value] for x in range(land.width))
            for y in range(land.height)
        ]
        removed = set(god_state.get("removed_food", []))
        return {
            "width": land.width,
            "height": land.height,
            "tiles": tiles,
            "food": [
                {"id": r[0], "x": r[1], "y": r[2], "quantity": r[3]}
                for r in food
                if r[0] not in removed
            ],
            "structures": [
                {"x": r["x"], "y": r["y"], "concept": r["concept_id"]} for r in structures
            ],
            "valleys": [{"name": n, "x": x, "y": y} for n, x, y in land.regions],
        }

    @app.get("/api/map")
    def world_map():
        out = _land()
        store = world.open()
        try:
            people = []
            for r in store.conn.execute(
                "SELECT id, name, x, y, alive, state_json FROM agents WHERE alive = 1"
            ):
                st = json.loads(r["state_json"])
                people.append(
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "x": r["x"],
                        "y": r["y"],
                        "group": st.get("group_id"),
                        "ideas": len(st.get("knowledge", [])),
                        "talent": st.get("talent"),
                    }
                )
            years = (
                [r[0] for r in store.conn.execute("SELECT DISTINCT year FROM yearly ORDER BY year")]
                if store._has_table("yearly")
                else []
            )
            out.update(people=people, years=years, clock=world.clock(int(store.meta()["tick"])))
            return out
        finally:
            store.close()

    @app.get("/api/map/year/{year}")
    def world_map_year(year: int):
        store = world.open()
        try:
            n = names(store.conn)
            people = []
            for r in store.conn.execute(
                "SELECT agent_id, data_json FROM yearly WHERE year = ? AND agent_id > 0", (year,)
            ):
                d = json.loads(r[1])
                if "x" in d:  # snapshots before M10 have no positions
                    people.append(
                        {
                            "id": r[0],
                            "name": n.get(r[0]),
                            "x": d["x"],
                            "y": d["y"],
                            "ideas": len(d.get("knows", [])),
                            "talent": d.get("talent"),
                        }
                    )
            cal = world.calendar
            lo, hi = (
                cal.ticks(days=(year - 1) * cal.days_per_year),
                cal.ticks(days=year * cal.days_per_year),
            )
            fights = [
                {"x": r[0], "y": r[1]}
                for r in store.conn.execute(
                    "SELECT x, y FROM events WHERE type = 'fight' AND tick >= ? AND tick < ? "
                    "AND x IS NOT NULL",
                    (lo - cal.ticks(days=cal.days_per_year), lo),
                )
            ]
            return {"year": year, "people": people, "fights": fights, "_": hi}
        finally:
            store.close()

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

    @app.get("/api/network")
    def network():
        """The living society as a graph: people, feelings, family, groups, communities."""
        import networkx as nx
        from networkx.algorithms import community

        store = world.open()
        conn = store.conn
        try:
            people = {}
            for r in conn.execute(
                "SELECT id, name, generation, stage, state_json FROM agents WHERE alive = 1"
            ):
                state = json.loads(r["state_json"])
                people[r["id"]] = {
                    "id": r["id"],
                    "name": r["name"],
                    "generation": r["generation"],
                    "stage": r["stage"],
                    "group": state.get("group_id"),
                    "partner": state.get("partner"),
                }
            group_names = (
                {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM groups")}
                if store._has_table("groups")
                else {}
            )
            lineage = lineage_objects(conn)
            edges, friend = [], nx.Graph()
            friend.add_nodes_from(people)
            seen = set()
            for r in conn.execute(
                "SELECT source_id, target_id, label, affinity FROM relationships"
            ):
                a, b = r["source_id"], r["target_id"]
                if a not in people or b not in people:
                    continue
                kin = family.relation(lineage[a], lineage[b], lineage)
                if kin in ("partner", "parent", "child", "sibling"):
                    kind = "partner" if kin == "partner" else "family"
                elif r["label"] in ("friend", "close friend"):
                    kind = "friend"
                    friend.add_edge(a, b)
                elif r["label"] in ("rival", "enemy"):
                    kind = "rival"
                else:
                    continue
                key = (min(a, b), max(a, b), kind)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"source": a, "target": b, "kind": kind, "label": r["label"]})
            centrality = nx.degree_centrality(friend) if len(friend) > 1 else {}
            communities = {}
            linked = [n for n in friend.nodes if friend.degree(n) > 0]
            if linked:
                found = community.greedy_modularity_communities(friend.subgraph(linked))
                for i, members in enumerate(found):
                    for m in members:
                        communities[m] = i + 1
            nodes = [
                {
                    **p,
                    "groupName": group_names.get(p["group"]),
                    "centrality": round(centrality.get(p["id"], 0.0), 3),
                    "community": communities.get(p["id"]),
                }
                for p in people.values()
            ]
            tick = int(store.meta()["tick"])
        finally:
            store.close()
        return {"nodes": nodes, "edges": edges, "clock": world.clock(tick)}

    # =================================================================
    # Live updates (server-sent events)
    # =================================================================

    @app.get("/api/live")
    async def live():
        async def stream():
            last = None
            while True:
                try:
                    store = WorldStore(world.path, readonly=True)
                    meta = store.meta()
                    store.close()
                    now = (meta.get("tick"), meta.get("saved_at"))
                except Exception:
                    now = None
                if now != last and now is not None:
                    last = now
                    tick = int(now[0])
                    data = {"tick": tick, "clock": world.clock(tick), "savedAt": now[1]}
                    yield f"data: {json.dumps(data)}\n\n"
                else:
                    yield ": still here\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(stream(), media_type="text/event-stream")

    dist = dashboard_dist()
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    else:

        @app.get("/")
        def missing():
            return HTMLResponse(
                "<h2>Genesis dashboard</h2><p>The browser app isn't built yet. Run:</p>"
                "<pre>cd dashboard && npm install && npm run build</pre>"
            )

    return app


def serve(path: Path, host="127.0.0.1", port=8810, open_browser=True):
    import uvicorn

    url = f"http://{host}:{port}"
    print(
        f"Genesis dashboard on {url} (read-only; safe beside `genesis run` or `view`)", flush=True
    )
    if open_browser:
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(path), host=host, port=port, log_level="warning")
