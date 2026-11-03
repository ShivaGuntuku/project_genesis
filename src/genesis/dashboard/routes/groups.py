"""Groups."""

import json

from fastapi import FastAPI

from genesis.dashboard.common import (
    World,
    event_item_of,
    names,
)
from genesis.history import events as ev


def register(app: FastAPI, world: World) -> None:
    event_item = event_item_of(world)

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
