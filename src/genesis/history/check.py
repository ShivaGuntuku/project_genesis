"""Does a world behave as designed? (M8f). `genesis check WORLD` reads a world file and grades
it against the experiment's requirements (docs/15, the 2026-10-28/29 decisions):

  their own minds - no leakage - invention - goals of their own - talents become expertise -
  money of their own - the LLM at its maximum - valleys and growth - disasters

Each row: what was agreed, what the file shows (numbers), and a mark: ok / weak / no / wait.
"""

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass

from genesis.culture.words import EARTH_OBJECTS, leak_words

CHATTER = ("greet", "reply", "turn", "ask")


@dataclass
class Row:
    requirement: str
    shows: str
    mark: str  # ok | weak | no | wait | n/a


def _templates() -> set[str]:
    from genesis.mind.goals import NEEDS

    return {t.lower().strip(" .") for t in NEEDS.values()}


def check(path) -> tuple[dict, list[Row]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    config = json.loads(meta["config_json"])
    rules, llm = config["rules"], config.get("llm", {})  # worlds from before M4 have no [llm]
    tick = int(meta["tick"])
    tpd = config.get("ticks_per_day", 24)
    days = max(1, tick // tpd)
    years = days / config.get("days_per_year", 360)
    alive = conn.execute("SELECT COUNT(*) FROM agents WHERE alive = 1").fetchone()[0]
    people = max(1, alive)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    info = {
        "world": config["name"],
        "seed": config["seed"],
        "days": days,
        "years": round(years, 2),
        "alive": alive,
        "llm": bool(llm.get("enabled")),
    }
    rows = []

    # --- their own minds -------------------------------------------------------------
    calls = Counter()
    if "llm_calls" in tables:
        calls = Counter(
            r[0] for r in conn.execute("SELECT kind FROM llm_calls WHERE outcome = 'ok'")
        )
    total = sum(calls.values())
    msgs = conn.execute("SELECT COUNT(*), SUM(line IS NOT NULL) FROM messages").fetchone()
    voiced = (msgs[1] or 0) / max(1, msgs[0])
    chatter = sum(calls[k] for k in CHATTER) / max(1, total)
    plans = calls.get("plan", 0)
    per_day = total / days / people
    if not info["llm"]:
        rows.append(Row("Agents think for themselves (LLM mind)", "LLM is off: rules only", "n/a"))
    else:
        rows.append(
            Row(
                "Agents think for themselves (LLM mind)",
                f"{per_day:.2f} LLM calls per person per day; {voiced:.0%} of messages in their "
                f"own words; {chatter:.0%} of calls are small talk; {plans} daily plans",
                "ok" if plans and chatter < 0.5 and per_day >= 0.2 else "weak" if total else "no",
            )
        )

    # --- no leakage --------------------------------------------------------------------
    texts = [r[0] for r in conn.execute("SELECT line FROM messages WHERE line IS NOT NULL")]
    if "memories" in tables:
        texts += [r[0] for r in conn.execute("SELECT text FROM memories WHERE kind = 'belief'")]
    words = sum(len(re.findall(r"[a-z]+", t.lower())) for t in texts)
    leaked = Counter(w for t in texts for w in leak_words(t))
    rate = 1000 * sum(leaked.values()) / max(1, words)
    if not texts:
        rows.append(Row("No leakage from our world", "nothing said in their own words yet", "n/a"))
    else:
        top = ", ".join(f"{w} {n}" for w, n in leaked.most_common(6)) or "none"
        rows.append(
            Row(
                "No leakage from our world",
                f"{rate:.1f} words of our world per 1,000 ({sum(leaked.values())} in {words}); "
                f"most: {top}",
                "ok" if rate < 0.5 else "weak" if rate < 3 else "no",
            )
        )

    # --- invention -----------------------------------------------------------------------
    ev = Counter(r[0] for r in conn.execute("SELECT type FROM events"))
    if rules.get("inventions"):
        concepts = []
        if "concepts" in tables:
            concepts = list(conn.execute("SELECT name, function, description FROM concepts"))
        functions = Counter(c["function"] for c in concepts)
        ideas = [c["description"] for c in concepts if c["description"]]
        if "llm_calls" in tables:
            ideas += [
                r[0] or "" for r in conn.execute("SELECT say FROM llm_calls WHERE kind = 'invent'")
            ]
        earthy = sum(
            1
            for i in ideas
            if any(
                w.rstrip("s") in EARTH_OBJECTS or w in EARTH_OBJECTS
                for w in re.findall(r"[a-z]+", i.lower())
            )
        )
        tries = ev.get("experimented", 0) + ev.get("invented", 0)
        rows.append(
            Row(
                "Curiosity: they invent what isn't there",
                f"{len(concepts)} ideas from {tries} tries; uses: "
                f"{', '.join(f'{k} {v}' for k, v in functions.most_common()) or 'none'}; "
                f"{earthy} of {len(ideas)} idea descriptions name our world's objects",
                "ok"
                if concepts and len(functions) >= 2 and earthy <= 0.1 * max(1, len(ideas))
                else "weak"
                if concepts
                else "no",
            )
        )

    # --- goals -----------------------------------------------------------------------------
    if rules.get("goals"):
        templates = _templates()
        goals = [
            json.loads(r[0])
            for r in conn.execute("SELECT data_json FROM events WHERE type = 'goal_set'")
        ]
        own = [g for g in goals if g.get("how") == "own words"]
        copies = sum(1 for g in own if g["text"].lower().strip(" .") in templates)
        needs = Counter(g["need"] for g in goals)
        top_share = needs.most_common(1)[0][1] / len(goals) if goals else 0
        shows = (
            f"{len(goals)} goals set, {len(own)} in their own words ({copies} of those copy the "
            f"rules' sentence); reached {ev.get('goal_reached', 0)}; most common: "
            f"{needs.most_common(1)[0][0] if goals else '-'} ({top_share:.0%})"
        )
        if not goals:
            mark = "wait"
        elif info["llm"]:
            mark = "ok" if own and copies <= 0.1 * len(own) and top_share < 0.5 else "weak"
            if own and copies > 0.5 * len(own):
                mark = "no"
        else:
            mark = "ok" if top_share < 0.5 else "weak"
        rows.append(Row("Goals of their own", shows, mark))

    # --- talents become expertise ------------------------------------------------------------
    if rules.get("talents"):
        from genesis.agents.talents import SKILL_OF, WORK_OF, profession_of

        skills: dict[int, dict] = {}
        for r in conn.execute("SELECT agent_id, skill, value FROM abilities"):
            skills.setdefault(r[0], {})[r[1]] = r[2]
        living = [
            (r["id"], json.loads(r["state_json"]))
            for r in conn.execute(
                "SELECT id, state_json FROM agents WHERE alive = 1 AND stage != 'child'"
            )
        ]
        jobs = {i: profession_of(s.get("work", {}), skills.get(i, {})) for i, s in living}

        def specialty(i):  # best skill apart from foraging, which everyone learns by eating
            sk = {k: v for k, v in skills.get(i, {}).items() if k != "foraging" and v >= 0.1}
            return max(sk, key=sk.get) if sk else None

        matched = sum(
            1
            for i, s in living
            if s.get("talent")
            and (
                jobs[i] == WORK_OF.get(s["talent"])
                or (SKILL_OF.get(s["talent"]) and specialty(i) == SKILL_OF[s["talent"]])
            )
        )
        tops = Counter(specialty(i) for i, _ in living)

        def listed(counter):
            return ", ".join(f"{k or 'none'} {v}" for k, v in counter.most_common(5))

        kinds = len({j for j in jobs.values() if j})
        rows.append(
            Row(
                "Each person an expert in their own field (talent)",
                f"work or specialty matches their talent for {matched} of {len(living)} adults; "
                f"specialties: {listed(tops)}; professions: {listed(Counter(jobs.values()))}",
                "ok"
                if living and matched >= 0.5 * len(living) and kinds >= 3
                else "weak"
                if matched >= 0.25 * max(1, len(living))
                else "no",
            )
        )

    # --- money -------------------------------------------------------------------------------
    if rules.get("trade"):
        economy = json.loads(meta.get("economy", '{"currencies": []}'))
        money = economy.get("currencies", [])
        trades = conn.execute("SELECT COUNT(*) FROM messages WHERE act = 'trade'").fetchone()[0]
        early = [m for m in money if m["tick"] < tpd * rules.get("currency_after_days", 0)]
        shows = f"{trades} trade offers; currencies: " + (
            ", ".join(
                f"{m['name']} ({m['good']}, {m['region']}, day {m['tick'] // tpd})" for m in money
            )
            or "none yet"
        )
        mark = "wait" if not money else "ok"
        if money and (trades < 200 or days < 360):
            mark = "weak"  # too soon to be real money
        if early:
            mark = "no"
        rows.append(Row("Money they create themselves", shows, mark))

    # --- the LLM at its maximum ------------------------------------------------------------
    if info["llm"]:
        limit = llm.get("max_calls_per_minute", 0)
        per_minute = Counter()
        if "llm_calls" in tables:
            per_minute = Counter(
                r[0][:16] for r in conn.execute("SELECT created_at FROM llm_calls")
            )
        full = sorted(per_minute.values())[1:-1] or list(per_minute.values())
        busy = sum(full) / len(full) if full else 0
        caps = (llm.get("max_calls_per_tick", 0), llm.get("max_calls_per_day", 0))
        shows = (
            f"limit {limit or 'none'} per minute; used about {busy:.0f} per minute; old caps "
            f"per tick/day: {caps[0] or 'none'}/{caps[1] or 'none'}"
        )
        if not limit:
            mark = "no"
        elif any(caps):
            mark = "weak"
        else:
            mark = "ok" if 0.85 * limit <= busy <= limit * 1.02 else "weak"
        rows.append(Row("LLM kept at its maximum (±10%), never above", shows, mark))

    # --- valleys, growth, disasters -----------------------------------------------------------
    births, deaths = ev.get("birth", 0), ev.get("died", 0)
    causes = Counter(
        r[0] for r in conn.execute("SELECT cause_of_death FROM agents WHERE alive = 0")
    )
    starts = config.get("starts", [])
    died_of = ", ".join(f"{k} {v}" for k, v in causes.most_common(3)) or "none"
    rows.append(
        Row(
            "2-3 valleys, growing in stages towards 50",
            f"{len(starts) or 1} valleys; {alive} alive (founders {config['founders']}); "
            f"{births} born, {deaths} died ({died_of}); "
            f"{ev.get('migrated', 0)} moved valleys",
            "ok" if len(starts) >= 2 and alive >= 0.8 * config["founders"] else "weak",
        )
    )
    if rules.get("disasters"):
        hit = ev.get("disaster", 0) // 2 + ev.get("disaster", 0) % 2
        rows.append(
            Row(
                "A disaster every 15-25 years",
                f"{hit} so far in {years:.1f} years",
                "wait" if years < 15 and not hit else "ok" if hit else "no",
            )
        )
    conn.close()
    return info, rows
