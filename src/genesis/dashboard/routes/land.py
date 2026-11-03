"""M10: machine health and the world map."""

import json

from fastapi import FastAPI

from genesis.dashboard.common import (
    World,
    names,
)


def register(app: FastAPI, world: World) -> None:
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
        if land.disasters or god_state.get("terrain"):
            land.rebuild_terrain()
        saved_land = json.loads(meta.get("land") or "{}")
        if saved_land:  # S2.1: woods, water and patches as they have become
            from genesis.world.land import apply_saved

            apply_saved(land, saved_land)
        if land.disasters:
            land.set_burned(dis.conditions(land, land.calendar.day_index(tick))["burned"])
        tiles = [
            "".join(TERRAIN_CODES[land.terrain[P(x, y)].value] for x in range(land.width))
            for y in range(land.height)
        ]
        removed = set(god_state.get("removed_food", []))
        removed |= {int(k) for k in saved_land.get("dead", {})}
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
            "paths": sorted(
                [int(k.split(",")[0]), int(k.split(",")[1])] for k in saved_land.get("paths", {})
            ),
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
