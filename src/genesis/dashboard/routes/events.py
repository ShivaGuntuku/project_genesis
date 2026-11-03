"""Events and "why" chains."""

from fastapi import FastAPI, HTTPException, Query

from genesis.dashboard.common import (
    World,
    event_item_of,
    names,
)


def register(app: FastAPI, world: World) -> None:
    event_item = event_item_of(world)

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
