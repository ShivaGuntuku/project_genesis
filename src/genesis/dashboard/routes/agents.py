"""People: the list, one person, their memories and timeline."""

import json

from fastapi import FastAPI, HTTPException, Query

from genesis.dashboard.common import (
    MAJOR_EVENTS,
    World,
    event_item_of,
    lineage_objects,
    names,
)
from genesis.history import events as ev
from genesis.history.describe import describe
from genesis.society import family


def register(app: FastAPI, world: World) -> None:
    event_item = event_item_of(world)

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
