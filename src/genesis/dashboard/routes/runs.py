"""M8.5: the long run, the family tree, exports."""

import json

from fastapi import FastAPI, HTTPException

from genesis.dashboard.common import (
    World,
)


def register(app: FastAPI, world: World) -> None:
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
