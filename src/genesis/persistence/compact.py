"""Keep a 1,000-year world file small (S2.0.7, doc 10.6).

Once rows are more than `KEEP_YEARS` old, routine rows are folded:
- routine rule-made messages (greetings, chat and praise, with no words of their own) become
  daily counts per pair in `message_counts`;
- routine needs (a need became pressing, a need was met) become daily counts per person in
  `event_counts`;
- daily metrics become 10-day averages (what the charts show anyway).

An event is never folded while anything points at it: a cause, a reply, a memory, a
relationship's last or grudge event, an LLM call or a God command. So every "why" chain stays
whole. Relationship-history rows stay.

Nothing the simulation reads is touched: a resume loads only the newest events, which are never
folded, so resumes and replays stay identical. Folding runs inside the daily save's transaction,
at most once a simulated month, so a crash can't leave a half-folded file.
"""

import json
from collections import Counter

ROUTINE = ("greet", "chat", "praise")
# In this order: a met need points at the pressing one, which is free to fold once that's gone.
ROUTINE_EVENTS = ("need_satisfied", "need_became_pressing")
KEEP_YEARS = 2
METRIC_DAYS = 10
EVERY_DAYS = 30  # fold at most once a simulated month

SCHEMAS = (
    """CREATE TABLE IF NOT EXISTS message_counts (
    day INTEGER NOT NULL, from_id INTEGER NOT NULL, to_id INTEGER NOT NULL, act TEXT NOT NULL,
    n INTEGER NOT NULL,
    PRIMARY KEY (day, from_id, to_id, act)
) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS event_counts (
    day INTEGER NOT NULL, agent_id INTEGER NOT NULL, type TEXT NOT NULL, n INTEGER NOT NULL,
    PRIMARY KEY (day, agent_id, type)
) WITHOUT ROWID""",
)


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def due(folded_to: int, tick: int, calendar) -> int | None:
    """The tick to fold up to now, or None if there's nothing (or too little) to fold."""
    span = METRIC_DAYS * calendar.ticks_per_day
    cutoff = tick - KEEP_YEARS * calendar.days_per_year * calendar.ticks_per_day
    cutoff -= cutoff % span  # whole 10-day buckets only
    if cutoff <= 0 or cutoff - folded_to < EVERY_DAYS * calendar.ticks_per_day:
        return None
    return cutoff


def fold(conn, lo: int, hi: int, calendar, keep_after_id: int) -> dict:
    """Fold rows with lo <= tick < hi. Events with id > keep_after_id are never touched.
    Runs inside the caller's transaction. Returns how many rows were folded, by kind."""
    for sql in SCHEMAS:
        conn.execute(sql)  # one statement each: stays inside the caller's transaction
    done = {"messages": _fold_messages(conn, lo, hi, calendar, keep_after_id)}
    for kind in ROUTINE_EVENTS:
        done[kind] = _fold_events(conn, kind, lo, hi, calendar, keep_after_id)
    done["metrics"] = _fold_metrics(conn, lo, hi, calendar)
    return done


def _unreferenced(conn, rows: list) -> list:
    if not rows:
        return rows
    kept = _referenced(conn, min(r[0] for r in rows), max(r[0] for r in rows))
    return [r for r in rows if r[0] not in kept]


def _delete_events(conn, ids: list[tuple[int]]):
    conn.executemany("DELETE FROM event_agents WHERE event_id = ?", ids)
    conn.executemany("DELETE FROM messages WHERE event_id = ?", ids)
    conn.executemany("DELETE FROM events WHERE id = ?", ids)


def _fold_messages(conn, lo, hi, calendar, keep_after_id) -> int:
    rows = conn.execute(
        f"""SELECT event_id, tick, from_id, to_id, act FROM messages
            WHERE tick >= ? AND tick < ? AND event_id <= ? AND source = 'rules'
              AND line IS NULL AND act IN ({",".join("?" * len(ROUTINE))})""",
        (lo, hi, keep_after_id, *ROUTINE),
    ).fetchall()
    rows = _unreferenced(conn, rows)
    if rows:
        counts = Counter((calendar.day(t), a, b, act) for _, t, a, b, act in rows)
        conn.executemany(
            """INSERT INTO message_counts (day, from_id, to_id, act, n) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (day, from_id, to_id, act) DO UPDATE SET n = n + excluded.n""",
            [(*k, n) for k, n in sorted(counts.items())],
        )
        _delete_events(conn, [(r[0],) for r in rows])
    return len(rows)


def _fold_events(conn, kind, lo, hi, calendar, keep_after_id) -> int:
    rows = conn.execute(
        """SELECT id, tick, actors_json FROM events
           WHERE type = ? AND tick >= ? AND tick < ? AND id <= ? AND origin != 'god'""",
        (kind, lo, hi, keep_after_id),
    ).fetchall()
    rows = _unreferenced(conn, rows)
    if rows:
        counts = Counter(
            (calendar.day(t), (json.loads(actors) or [0])[0], kind) for _, t, actors in rows
        )
        conn.executemany(
            """INSERT INTO event_counts (day, agent_id, type, n) VALUES (?, ?, ?, ?)
               ON CONFLICT (day, agent_id, type) DO UPDATE SET n = n + excluded.n""",
            [(*k, n) for k, n in sorted(counts.items())],
        )
        _delete_events(conn, [(r[0],) for r in rows])
    return len(rows)


def _fold_metrics(conn, lo, hi, calendar) -> int:
    span = METRIC_DAYS * calendar.ticks_per_day
    before = conn.execute(
        "SELECT COUNT(*) FROM metrics WHERE tick >= ? AND tick < ?", (lo, hi)
    ).fetchone()[0]
    averaged = conn.execute(
        """SELECT MIN(tick), name, AVG(value) FROM metrics WHERE tick >= ? AND tick < ?
           GROUP BY name, tick / ? ORDER BY 1, 2""",
        (lo, hi, span),
    ).fetchall()
    conn.execute("DELETE FROM metrics WHERE tick >= ? AND tick < ?", (lo, hi))
    conn.executemany("INSERT INTO metrics (tick, name, value) VALUES (?, ?, ?)", averaged)
    return before - len(averaged)


def _referenced(conn, first: int, last: int) -> set[int]:
    """Event ids in [first, last] that something else points at."""
    tables = _tables(conn)
    out: set[int] = set()

    def add(sql, *params):
        out.update(r[0] for r in conn.execute(sql, params))

    # Causes and replies always come after what they point at, so only newer rows can point.
    add(
        "SELECT cause_event_id FROM events WHERE id > ? AND cause_event_id BETWEEN ? AND ?",
        first,
        first,
        last,
    )
    add(
        "SELECT reply_to FROM messages WHERE event_id > ? AND reply_to BETWEEN ? AND ?",
        first,
        first,
        last,
    )
    for table, column in (
        ("memories", "source_event_id"),
        ("llm_calls", "event_id"),
        ("god_queue", "event_id"),
        ("relationships", "last_event_id"),  # a later fight may name it as its cause
        ("relationships", "grudge_event_id"),
    ):
        if table in tables:
            add(f"SELECT {column} FROM {table} WHERE {column} BETWEEN ? AND ?", first, last)
    return out


def folded_messages(conn) -> Counter:
    """Folded message counts by act (for totals that must include them)."""
    if "message_counts" not in _tables(conn):
        return Counter()
    return Counter(dict(conn.execute("SELECT act, SUM(n) FROM message_counts GROUP BY act")))


def folded_events(conn) -> Counter:
    """Folded event counts by type, messages included."""
    out = Counter({"message": sum(folded_messages(conn).values())})
    if "event_counts" in _tables(conn):
        out.update(dict(conn.execute("SELECT type, SUM(n) FROM event_counts GROUP BY type")))
    return +out
