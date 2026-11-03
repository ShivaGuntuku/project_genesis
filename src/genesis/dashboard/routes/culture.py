"""Ideas, goals, the economy and history."""

import json

from fastapi import FastAPI

from genesis.dashboard.common import (
    World,
    event_item_of,
    names,
)
from genesis.history import events as ev
from genesis.history.describe import describe


def register(app: FastAPI, world: World) -> None:
    event_item = event_item_of(world)

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
                    "recipe": _named(json.loads(r["recipe_json"]), by_id),
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
            land = []  # S2.1: woods, water and berry patches, year by year
            if store._has_table("yearly"):
                for r in conn.execute(
                    "SELECT year, data_json FROM yearly WHERE agent_id = -1 ORDER BY year"
                ):
                    d = json.loads(r["data_json"])
                    land.append(
                        {
                            "year": r["year"],
                            "woods": d["woods"],
                            "water": d["water"],
                            "waterMin": d["water_min"],
                            "waterMax": d["water_max"],
                            "patches": d["patches"],
                            "ponds": d["ponds"],
                            "paths": d["paths"],
                            "climate": d["climate"],
                            "valleys": d["valleys"],
                        }
                    )
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
                "land": land,
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


def _named(recipe: list, by_id: dict) -> list:
    """S2.2: made materials ("#<id>") by their names."""
    mats = [by_id.get(int(m[1:]), m) if m.startswith("#") else m for m in recipe[0]]
    return [mats, recipe[1]]
