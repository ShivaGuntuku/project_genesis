"""Branching and comparing worlds (M9): `genesis branch` copies a world under a new name;
`genesis compare A B` puts the two side by side."""

import json
import sqlite3
from collections import Counter
from pathlib import Path


def branch(source: Path, target: Path, name: str):
    """A copy of the world file as it was last saved, under a new name."""
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=60)
    dest = sqlite3.connect(str(target))
    try:
        src.backup(dest)
        row = dest.execute("SELECT value FROM meta WHERE key = 'config_json'").fetchone()
        config = json.loads(row[0])
        config["name"] = name
        dest.execute("UPDATE meta SET value = ? WHERE key = 'config_json'", (json.dumps(config),))
        for key in ("source_toml",):
            dest.execute("DELETE FROM meta WHERE key = ?", (f"branch_{key}",))
        dest.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('branched_from', ?)",
            (
                json.dumps(
                    {
                        "world": source.stem,
                        "tick": int(
                            dest.execute("SELECT value FROM meta WHERE key = 'tick'").fetchone()[0]
                        ),
                    }
                ),
            ),
        )
        dest.execute("DROP TABLE IF EXISTS god_queue")
        dest.commit()
    finally:
        src.close()
        dest.close()


def summary(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        config = json.loads(meta["config_json"])
        tpd = config.get("ticks_per_day", 24)
        ev = Counter(r[0] for r in conn.execute("SELECT type FROM events"))
        acts = Counter(r[0] for r in conn.execute("SELECT act FROM messages"))
        alive = conn.execute("SELECT COUNT(*) FROM agents WHERE alive = 1").fetchone()[0]
        causes = Counter(
            r[0] for r in conn.execute("SELECT cause_of_death FROM agents WHERE alive = 0")
        )
        genes = {
            r[0]: round(r[1], 3)
            for r in conn.execute(
                "SELECT g.gene, AVG(g.value) FROM genes g JOIN agents a ON a.id = g.agent_id "
                "WHERE a.alive = 1 GROUP BY g.gene"
            )
        }
        feelings = conn.execute(
            "SELECT AVG(affinity), AVG(trust) FROM relationships r JOIN agents a "
            "ON a.id = r.source_id WHERE a.alive = 1"
        ).fetchone()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ideas = (
            conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
            if "concepts" in tables
            else 0
        )
        groups = (
            conn.execute("SELECT COUNT(*) FROM groups WHERE ended_tick IS NULL").fetchone()[0]
            if "groups" in tables
            else 0
        )
        economy = json.loads(meta.get("economy", '{"currencies": []}'))
        return {
            "world": path.stem,
            "day": int(meta["tick"]) // tpd,
            "branched_from": json.loads(meta["branched_from"]) if "branched_from" in meta else None,
            "alive": alive,
            "births": ev.get("birth", 0),
            "deaths": ev.get("died", 0),
            "deaths_by_cause": dict(causes),
            "fights": ev.get("fight", 0),
            "feuds_begun": ev.get("relation_changed", 0),
            "groups": groups,
            "ideas": ideas,
            "currencies": [c["name"] for c in economy.get("currencies", [])],
            "trades": acts.get("trade", 0),
            "gifts": acts.get("give", 0),
            "insults": acts.get("insult", 0),
            "god_interventions": ev.get("intervention", 0),
            "mean_affinity": round(feelings[0] or 0, 3),
            "mean_trust": round(feelings[1] or 0, 3),
            "genes": genes,
        }
    finally:
        conn.close()


ROWS = (
    ("day", "Day"),
    ("alive", "Alive"),
    ("births", "Births"),
    ("deaths", "Deaths"),
    ("fights", "Fights"),
    ("insults", "Insults"),
    ("gifts", "Gifts"),
    ("trades", "Trade offers"),
    ("groups", "Groups now"),
    ("ideas", "Ideas invented"),
    ("mean_affinity", "Mean affinity"),
    ("mean_trust", "Mean trust"),
    ("god_interventions", "God interventions"),
)
