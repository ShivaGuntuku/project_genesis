"""S2.0.7: old routine messages and daily metrics are folded, and nothing else changes."""

import json
import sqlite3
from collections import Counter

import pytest

from genesis.config import WorldConfig
from genesis.persistence import compact
from genesis.persistence.db import SCHEMA_VERSION, WorldStore
from genesis.simulation import service
from genesis.simulation.engine import Simulation

TWO = "worlds/two_valleys.toml"
DAYS = 200


@pytest.fixture(scope="module")
def folded_world(tmp_path_factory, monkeypatch_module):
    # Fold everything but the newest events (instead of keeping 2 years), to fold a lot quickly.
    monkeypatch_module.setattr(compact, "KEEP_YEARS", 0)
    path = tmp_path_factory.mktemp("compact") / "w.sqlite"
    config = WorldConfig.from_file(TWO)
    WorldStore.create(path, Simulation(config)).close()
    service.run(path, 4 * DAYS, llm=False, snapshot_minutes=0, echo=lambda *_: None)
    return path, config


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _ids(conn, sql):
    return {r[0] for r in conn.execute(sql) if r[0] is not None}


def test_folding_keeps_every_reference_and_every_count(folded_world):
    path, _ = folded_world
    conn = sqlite3.connect(path)
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    assert int(meta["folded_to"]) > 4 * 100
    folded = compact.folded_messages(conn)
    assert folded["greet"] > 50 and set(folded) <= set(compact.ROUTINE)

    events = _ids(conn, "SELECT id FROM events")
    for sql in (
        "SELECT cause_event_id FROM events",
        "SELECT reply_to FROM messages",
        "SELECT source_event_id FROM memories",
        "SELECT last_event_id FROM relationships",
        "SELECT grudge_event_id FROM relationships",
    ):
        assert _ids(conn, sql) <= events, sql  # no "why" chain points at a folded message

    # Every message ever said is still counted: kept rows plus folded counts.
    kept = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'message'").fetchone()[0]
    assert kept + sum(folded.values()) == json.loads(meta["event_counts"])["message"]
    assert kept == conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    every = Counter(r[0] for r in conn.execute("SELECT type FROM events"))
    every += compact.folded_events(conn)
    totals = {k: v for k, v in json.loads(meta["event_counts"]).items() if "." not in k}
    assert every == Counter(totals)  # nothing lost from the totals
    assert compact.folded_events(conn)["need_satisfied"] > 100
    orphans = conn.execute(
        "SELECT COUNT(*) FROM event_agents WHERE event_id NOT IN (SELECT id FROM events)"
    ).fetchone()[0]
    assert orphans == 0

    # Old metrics: one row per name per 10 days.
    span = compact.METRIC_DAYS * 4
    per_bucket = conn.execute(
        "SELECT MAX(c) FROM (SELECT COUNT(*) c FROM metrics WHERE tick < ? "
        "GROUP BY name, tick / ?)",
        (int(meta["folded_to"]), span),
    ).fetchone()[0]
    assert per_bucket == 1
    conn.close()


def test_a_folded_world_resumes_exactly_like_an_unbroken_run(folded_world):
    path, config = folded_world
    reference = Simulation(config)
    reference.run(4 * DAYS)
    store = WorldStore(path)
    resumed = store.load()
    store.close()
    assert resumed.state() == reference.state()
    resumed.run(4 * 40)
    reference.run(4 * 40)
    assert resumed.state() == reference.state()


def test_the_dashboard_and_check_read_a_folded_world(folded_world):
    from fastapi.testclient import TestClient

    from genesis.dashboard.server import create_app
    from genesis.history.check import check

    path, _ = folded_world
    client = TestClient(create_app(path))
    for url in ("/api/overview", "/api/conversations", "/api/agents/1", "/api/history"):
        assert client.get(url).status_code == 200, url
    info, rows = check(path)
    assert info["days"] == DAYS and rows


def test_folding_waits_two_years_and_runs_at_most_monthly(monkeypatch):
    monkeypatch.setattr(compact, "KEEP_YEARS", 2)
    from genesis.simulation.calendar import calendar_of

    cal = calendar_of(WorldConfig.from_file(TWO))
    year = cal.days_per_year * cal.ticks_per_day
    assert compact.due(0, 2 * year, cal) is None  # nothing is old enough yet
    upto = compact.due(0, 2 * year + 4 * 45, cal)
    assert upto == 4 * 40  # whole 10-day buckets only
    assert compact.due(upto, 2 * year + 4 * 60, cal) is None  # not a month later yet


def test_an_older_world_file_is_upgraded(tmp_path):
    path = tmp_path / "old.sqlite"
    WorldStore.create(path, Simulation(WorldConfig.from_file(TWO))).close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE message_counts")
    conn.execute("DROP TABLE event_counts")
    conn.execute("UPDATE meta SET value = '10' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    store = WorldStore(path)
    store.load()
    assert int(store.meta()["schema_version"]) == SCHEMA_VERSION
    store.close()


def test_genesis_compact_shrinks_a_stopped_world_and_waits_for_a_running_one(tmp_path):
    import os
    import subprocess
    import sys

    path = tmp_path / "w.sqlite"
    WorldStore.create(path, Simulation(WorldConfig.from_file(TWO))).close()
    service.run(path, 4 * 30, llm=False, snapshot_minutes=0, echo=lambda *_: None)
    env = {**os.environ, "GENESIS_DATA": str(tmp_path)}
    cmd = [sys.executable, "-m", "genesis.cli", "compact", "w"]
    with service.run_lock(path, "test"):
        busy = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert busy.returncode != 0 and "already being run" in busy.stderr
    assert "Traceback" not in busy.stderr
    done = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert done.returncode == 0 and "MB ->" in done.stdout, done.stderr
    assert WorldStore(path).load().tick_count == 4 * 30  # still a good world
