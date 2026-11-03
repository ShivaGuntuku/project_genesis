"""`genesis report WORLD`: one page per run (S2.0.8 skeleton; S2.7.6 fills it in).

A Markdown page with what a reader of a long run needs first: the world, the Stage 1 and
Stage 2 rows of `genesis check`, and the run decade by decade. Sections that later steps add
(mind markers, the idea tree, divergence statistics, the Chronicle) are listed as coming.
"""

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone

from genesis.history.check import check
from genesis.history.targets import stage2

MARKS = {"ok": "✅", "weak": "⚠️", "no": "❌", "wait": "⏳", "n/a": "–"}
COMING = (
    ("Mind markers", "S2.7.1"),
    ("Leak trend by decade", "S2.7.2"),
    ("Divergence between valleys", "S2.7.3"),
    ("Idea family tree", "S2.7.4"),
    ("Chronicle", "S2.7.5"),
)


def _table(rows) -> list[str]:
    out = ["| | Requirement | What the world shows |", "|---|---|---|"]
    for r in rows:
        shows = r.shows.replace("|", "/")
        out.append(f"| {MARKS.get(r.mark, r.mark)} | {r.requirement} | {shows} |")
    return out


def _decades(path) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        config = json.loads(meta["config_json"])
        tpy = config.get("ticks_per_day", 24) * config.get("days_per_year", 360)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        born = Counter(t // (10 * tpy) for (t,) in conn.execute("SELECT born_tick FROM agents"))
        died = Counter(
            t // (10 * tpy)
            for (t,) in conn.execute("SELECT died_tick FROM agents WHERE died_tick IS NOT NULL")
        )
        invented: Counter = Counter()
        lost: Counter = Counter()
        if "concepts" in tables:
            for t, gone in conn.execute("SELECT tick, lost_tick FROM concepts"):
                invented[t // (10 * tpy)] += 1
                if gone is not None:
                    lost[gone // (10 * tpy)] += 1
        people: dict[int, int] = {}
        if "yearly" in tables:
            for y, d in conn.execute("SELECT year, data_json FROM yearly WHERE agent_id = 0"):
                valleys = json.loads(d).get("valleys", {}).values()
                people.setdefault((y - 1) // 10, sum(v.get("people", 0) for v in valleys))
        last = int(meta["tick"]) // (10 * tpy)
    finally:
        conn.close()
    out = [
        "| Years | People at start | Born | Died | Inventions | Ideas lost |",
        "|---|---|---|---|---|---|",
    ]
    for d in range(last + 1):
        out.append(
            f"| {10 * d + 1}-{10 * d + 10} | {people.get(d, '–')} | {born[d]} | {died[d]} | "
            f"{invented[d]} | {lost[d]} |"
        )
    return out


def report(path) -> str:
    info, stage1 = check(path)
    lines = [
        f"# {info['world']}: run report",
        "",
        f"Seed {info['seed']}; day {info['days']} (year {info['years']}); {info['alive']} alive; "
        f"LLM {'on' if info['llm'] else 'off'}. Written "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
        "",
        "## Stage 1 requirements",
        "",
        *_table(stage1),
        "",
        "## Stage 2 targets",
        "",
        *_table(stage2(path)),
        "",
        "## Decade by decade",
        "",
        *_decades(path),
        "",
    ]
    for title, step in COMING:
        lines += [f"## {title}", "", f"_Comes with {step}._", ""]
    return "\n".join(lines)
