"""The Stage 2 targets (S2.0.8; roadmap "Done means"), measured from a world file.

`genesis check WORLD` shows these rows after the Stage 1 requirements. A target whose mechanism
isn't built yet says which step brings it (for example "comes with S2.1") and is marked n/a.
The thresholds are for 100-year runs; younger worlds are marked "wait" where that's fairer.
"""

import json
import re
import sqlite3
from collections import Counter

from genesis.culture.words import leak_words
from genesis.history.check import Row

SEASONS = ("spring", "wet season", "dry season", "cold season")


def _decades(ticks, tpy: int, years: float) -> list[int]:
    """Inventions in each complete decade."""
    complete = int(years // 10)
    out = [0] * complete
    for t in ticks:
        d = t // (10 * tpy)
        if d < complete:
            out[d] += 1
    return out


def _depth(concepts: dict[str, list]) -> int:
    """How deep made-from-made goes: 1 = made from raw materials only."""
    memo: dict[str, int] = {}

    def depth(name, seen=()):
        if name in memo:
            return memo[name]
        materials = concepts[name][0] if concepts[name] else []
        made = [m for m in materials if m in concepts and m not in seen]
        memo[name] = 1 + max((depth(m, (*seen, name)) for m in made), default=0)
        return memo[name]

    return max((depth(n) for n in concepts), default=0)


def stage2(path) -> list[Row]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return _rows(conn)
    finally:
        conn.close()


def _rows(conn) -> list[Row]:
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    config = json.loads(meta["config_json"])
    rules, llm = config["rules"], config.get("llm", {})
    tick = int(meta["tick"])
    tpd = config.get("ticks_per_day", 24)
    dpy = config.get("days_per_year", 360)
    tpy = tpd * dpy
    years = tick / tpy
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = []
    century = "per 100 years"

    # --- invention keeps going, breadth, ideas last ----------------------------------------
    concepts = list(conn.execute("SELECT * FROM concepts")) if "concepts" in tables else []
    if rules.get("inventions"):
        per_decade = _decades([c["tick"] for c in concepts], tpy, years)
        mark = "wait" if not per_decade else "ok" if all(per_decade) else "no"
        rows.append(
            Row(
                "Invention keeps going: 1+ in every decade",
                f"{len(concepts)} inventions; per decade: {per_decade or 'no full decade yet'}",
                mark,
            )
        )
        uses = {f for c in concepts for f in json.loads(c["functions_json"])} - {"material"}
        named = {f"#{c['id']}": c["name"] for c in concepts}  # S2.2: made materials by name
        recipes = {
            c["name"]: [
                [named.get(m, m) for m in json.loads(c["recipe_json"])[0]],
                json.loads(c["recipe_json"])[1],
            ]
            for c in concepts
        }
        depth = _depth(recipes)
        rows.append(
            Row(
                f"Breadth: 15+ uses {century}; depth 3+ (made from made from made)",
                f"{len(uses)} uses ({', '.join(sorted(uses)) or 'none'}); depth {depth}",
                "ok"
                if len(uses) >= 15 and depth >= 3
                else "wait"
                if years < 100 and concepts
                else "no",
            )
        )
        distinct = [c for c in concepts if c["same_as"] is None]
        lost = sum(1 for c in distinct if c["lost_tick"] is not None)
        share = lost / len(distinct) if distinct else 0
        rows.append(
            Row(
                f"Ideas last: under 20% lost {century}",
                f"{lost} of {len(distinct)} different ideas lost ({share:.0%}); "
                f"{len(concepts) - len(distinct)} were the same thing invented again",
                "wait" if not distinct else "ok" if share < 0.2 else "no",
            )
        )
    rows.append(_research_row(conn, rules))

    # --- culture: valleys apart (tool ideas until S2.4 adds the others) ----------------------
    yearly = {}
    if "yearly" in tables:
        yearly = {
            y: json.loads(d)
            for y, d in conn.execute("SELECT year, data_json FROM yearly WHERE agent_id = 0")
        }
    last = yearly[max(yearly)] if yearly else {}
    valleys = last.get("valleys", {})
    ideas = [set(v.get("ideas", [])) for v in valleys.values()]
    if len(ideas) > 1:
        every = set().union(*ideas)
        shared = set.intersection(*ideas)
        share = len(shared) / max(1, len(every))
        apart = f"valleys share {len(shared)} of {len(every)} ideas ({share:.0%})"
    else:
        apart = "one valley"
    rows.append(_culture_row(rules, valleys, apart, years))

    # --- land ----------------------------------------------------------------------------------
    land = {}
    if "yearly" in tables:
        land = {
            y: json.loads(d)
            for y, d in conn.execute("SELECT year, data_json FROM yearly WHERE agent_id = -1")
        }
    rows.append(_land_row(rules, land, years))

    # --- people --------------------------------------------------------------------------------
    peak: dict[str, int] = {}
    total_peak = 0
    for data in yearly.values():
        total_peak = max(
            total_peak, sum(v.get("people", 0) for v in data.get("valleys", {}).values())
        )
        for name, v in data.get("valleys", {}).items():
            peak[name] = max(peak.get(name, 0), v.get("people", 0))
    now = {name: v.get("people", 0) for name, v in valleys.items()}
    room = rules.get("max_population") or 50  # the land holds this many in all (the target)
    aim = min(40, room)
    worst = _worst_season(conn, yearly, tpd, dpy)
    people = ", ".join(f"{n} {now.get(n, 0)} (peak {p})" for n, p in sorted(peak.items()))
    rows.append(
        Row(
            f"People: the valleys together reach {aim}-{room} by year 100; "
            "no season kills over 25%",
            f"{sum(now.values())} now, peak {total_peak} ({people or 'no yearly record yet'}); "
            f"worst season: {worst[1]}",
            "ok"
            if total_peak >= aim and worst[0] <= 0.25
            else "wait"
            if years < 100 and worst[0] <= 0.25
            else "no",
        )
    )

    # --- no leakage, falling ---------------------------------------------------------------------
    lines = list(conn.execute("SELECT tick, line FROM messages WHERE line IS NOT NULL"))
    if lines:
        half = len(lines) // 2
        rates = []
        for part in (lines[:half], lines[half:]):
            words = sum(len(re.findall(r"[a-z]+", r["line"].lower())) for r in part)
            rates.append(1000 * sum(len(leak_words(r["line"])) for r in part) / max(1, words))
        names = {c["name"].lower() for c in concepts}
        names |= {
            m["name"].lower() for m in json.loads(meta.get("economy", "{}")).get("currencies", [])
        }
        own = sum(1 for r in lines if any(n in r["line"].lower() for n in names))
        rows.append(
            Row(
                "No leakage: under 0.5 per 1,000 words and falling; valley words used in speech",
                f"{rates[0]:.2f} then {rates[1]:.2f} per 1,000 (first and second half); "
                f"{own} of {len(lines)} lines use a valley word",
                "ok" if rates[1] < 0.5 and rates[1] <= rates[0] and own else "weak",
            )
        )
    else:
        rows.append(Row("No leakage, falling; valley words used", "no spoken lines", "n/a"))

    # --- throughput and speed ------------------------------------------------------------------
    speed = _speed(conn, tables, tpy)
    if llm.get("enabled"):
        rows.append(
            Row(
                "Throughput: P1 (10 years) in 2 h or less; 100 years in 2 days or less",
                speed[1],
                "wait" if speed[0] is None else "ok" if speed[0] >= 50 else "no",  # 100 y in 2 days
            )
        )
    else:
        tps = speed[2]
        rows.append(
            Row(
                "Rules speed: 150+ watches a second at 50 people",
                f"{speed[1]}; {tps:.0f} watches a second" if tps else speed[1],
                "wait" if not tps else "ok" if tps >= 150 else "weak",
            )
        )

    rows.append(
        Row(
            "Research tools: a report for every 100-year run",
            "`genesis report` writes a skeleton; markers, idea tree, divergence and Chronicle "
            "come with S2.7",
            "weak",
        )
    )
    return rows


def _research_row(conn, rules: dict) -> Row:
    need = "Research: 50%+ of inventions follow lore found by testing"
    if not rules.get("research"):
        return Row(need, "no research in this world (research = 0)", "n/a")
    found = with_lore = 0
    for (data,) in conn.execute("SELECT data_json FROM events WHERE type = 'invented'"):
        d = json.loads(data)
        if d.get("same_as") is None and d.get("function") != "material":
            found += 1
            with_lore += bool(d.get("lore"))
    lore = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'lore_found'").fetchone()[0]
    share = with_lore / found if found else 0
    return Row(
        need,
        f"{with_lore} of {found} new inventions followed lore ({share:.0%}); {lore} facts tested",
        "wait" if not found else "ok" if share >= 0.5 else "no",
    )


def _culture_row(rules: dict, valleys: dict, apart: str, years: float) -> Row:
    need = "Culture: 30+ shared non-tool ideas per valley; isolated valleys share under 50%"
    if not rules.get("culture"):
        return Row(need, f"tool ideas only (culture = 0): {apart}", "n/a")
    held = {k: {c[0] for c in v.get("culture", [])} for k, v in valleys.items()}
    counts = ", ".join(f"{k} {len(v)}" for k, v in sorted(held.items()))
    sets = [v for v in held.values() if v]
    every = set().union(*sets) if sets else set()
    shared = set.intersection(*sets) if len(sets) > 1 else set()
    share = len(shared) / max(1, len(every))
    met = sets and all(len(v) >= 30 for v in held.values()) and share < 0.5
    return Row(
        need,
        f"shared ideas held by 2+ per valley: {counts or 'none yet'}; "
        f"valleys share {len(shared)} of {len(every)} ({share:.0%})",
        "ok" if met else "wait" if years < 100 else "no",
    )


def _land_row(rules: dict, land: dict, years: float) -> Row:
    need = "Land changes: woods 10%+, 3+ patches born and lost per valley, lakes with seasons"
    if not rules.get("living_land"):
        return Row(need, "the land doesn't change in this world (living_land = 0)", "n/a")
    if not land:
        return Row(need, "no yearly record of the land yet", "wait")
    ordered = [land[y] for y in sorted(land)]
    first = max(1, ordered[0]["woods"])
    woods = max(abs(d["woods"] - ordered[0]["woods"]) for d in ordered) / first
    swing = max((d["water_max"] - d["water_min"]) / max(1, d["water"]) for d in ordered)
    valleys = sorted({v for d in ordered for v in d["valleys"]})
    born = {v: sum(d["valleys"].get(v, {}).get("born", 0) for d in ordered) for v in valleys}
    died = {v: sum(d["valleys"].get(v, {}).get("died", 0) for d in ordered) for v in valleys}
    patches = ", ".join(f"{v} +{born[v]}/-{died[v]}" for v in valleys)
    shows = (
        f"woods changed up to {woods:.0%}; lakes swing {swing:.0%} within a year; "
        f"patches born/lost: {patches}; climate now {ordered[-1]['climate']:+.2f}"
    )
    met = woods >= 0.1 and swing >= 0.1 and all(born[v] >= 3 and died[v] >= 3 for v in valleys)
    return Row(need, shows, "ok" if met else "wait" if years < 100 else "no")


def _worst_season(conn, yearly: dict, tpd: int, dpy: int) -> tuple[float, str]:
    deaths: Counter[tuple[int, int]] = Counter()
    for (died,) in conn.execute("SELECT died_tick FROM agents WHERE alive = 0"):
        if died is None:
            continue
        day = died // tpd
        deaths[(day // dpy + 1, (day % dpy) * 4 // dpy)] += 1
    worst, text = 0.0, "none"
    for (year, season), n in deaths.items():
        alive = sum(v.get("people", 0) for v in yearly.get(year, {}).get("valleys", {}).values())
        if alive and n / alive > worst:
            worst = n / alive
            text = f"{n} of {alive} died in the {SEASONS[season]} of year {year} ({worst:.0%})"
    return worst, text


def _speed(conn, tables: set[str], tpy: int) -> tuple[float | None, str, float | None]:
    """(simulated years per real day, text, watches per second) over finished run sessions."""
    if "runs" not in tables:
        return None, "no run sessions recorded", None
    from datetime import datetime

    ticks = seconds = 0.0
    for r in conn.execute(
        "SELECT started_at, ended_at, from_tick, to_tick FROM runs WHERE ended_at IS NOT NULL"
    ):
        span = datetime.fromisoformat(r["ended_at"]) - datetime.fromisoformat(r["started_at"])
        if r["to_tick"] is not None and span.total_seconds() > 0:
            ticks += r["to_tick"] - r["from_tick"]
            seconds += span.total_seconds()
    if not seconds:
        return None, "no finished run sessions yet", None
    per_day = ticks / tpy / (seconds / 86400)
    hours_100 = 100 / per_day * 24
    return (
        per_day,
        f"{per_day:.0f} simulated years a real day (100 years in about {hours_100:.0f} h)",
        ticks / seconds,
    )
