import os
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from genesis.cli import main as cli
from genesis.history import events as ev
from genesis.persistence.db import WorldStore, WorldStoreError
from genesis.simulation.engine import Simulation
from helpers import harsh_sim, harsh_toml


def run_saving(sim, store, ticks):
    for _ in range(ticks):
        sim.tick()
        store.save(sim)


def test_reload_continues_exactly_like_an_uninterrupted_run(tmp_path):
    reference = Simulation.create(seed=11, founders=4)
    reference.run(600)

    sim = Simulation.create(seed=11, founders=4)
    store = WorldStore.create(tmp_path / "w.sqlite", sim)
    run_saving(sim, store, 250)
    store.close()

    store = WorldStore(tmp_path / "w.sqlite")
    resumed = store.load()
    run_saving(resumed, store, 350)
    assert resumed.state() == reference.state()

    # The file itself also holds every event of the uninterrupted run, in order.
    saved = [r[0] for r in store.conn.execute("SELECT id FROM events ORDER BY id")]
    assert saved == list(range(1, reference.log.next_id))
    store.close()


def test_world_file_is_not_overwritten_by_accident(tmp_path):
    WorldStore.create(tmp_path / "w.sqlite", Simulation.create()).close()
    with pytest.raises(WorldStoreError):
        WorldStore.create(tmp_path / "w.sqlite", Simulation.create())


def test_cause_chain_from_database(tmp_path):
    sim = harsh_sim(seed=3, founders=10, regen=0)  # berries never regrow: some will starve
    store = WorldStore.create(tmp_path / "w.sqlite", sim)
    run_saving(sim, store, 240)

    died = store.events(type=ev.DIED, limit=1)[0]
    chain = store.cause_chain(died.id)
    assert [e.type for e in chain] == [ev.DIED, ev.SUFFERING, ev.NEED_BECAME_PRESSING]
    assert store.consequences(chain[1].id)[0].id == died.id
    store.close()


def test_events_by_agent_query(tmp_path):
    sim = Simulation.create(seed=2)
    store = WorldStore.create(tmp_path / "w.sqlite", sim)
    run_saving(sim, store, 100)
    john = sim.population.get_agent("John")
    events = store.events(agent_id=john.id, limit=1000)
    assert events and all(john.id in e.actors or john.id in e.targets for e in events)
    store.close()


def test_killed_process_resumes_to_the_same_history(tmp_path):
    """Doc 11 check: kill -9 mid-run, resume, and get exactly the uninterrupted history."""
    total = 1500
    reference = Simulation.create(seed=21, founders=3)
    reference.run(total)

    env = {**os.environ, "GENESIS_DATA": str(tmp_path)}
    WorldStore.create(tmp_path / "w.sqlite", Simulation.create(seed=21, founders=3)).close()

    proc = subprocess.Popen(
        [sys.executable, "-m", "genesis.cli", "run", "w", "--ticks", str(total), "--quiet"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    proc.send_signal(signal.SIGKILL)  # no chance to clean up
    proc.wait()

    store = WorldStore(tmp_path / "w.sqlite")
    resumed = store.load()
    assert 0 < resumed.tick_count < total, "kill should land mid-run"
    run_saving(resumed, store, total - resumed.tick_count)
    assert resumed.state() == reference.state()
    store.close()


def test_sigterm_finishes_the_tick_and_records_the_stop(tmp_path):
    env = {**os.environ, "GENESIS_DATA": str(tmp_path)}
    WorldStore.create(tmp_path / "w.sqlite", Simulation.create()).close()
    proc = subprocess.Popen(
        [sys.executable, "-m", "genesis.cli", "run", "w", "--ticks", "100000", "--quiet"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == 0

    conn = sqlite3.connect(tmp_path / "w.sqlite")
    reason, to_tick = conn.execute("SELECT end_reason, to_tick FROM runs").fetchone()
    tick = int(conn.execute("SELECT value FROM meta WHERE key = 'tick'").fetchone()[0])
    assert reason == "SIGTERM" and to_tick == tick > 0


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GENESIS_DATA", str(tmp_path))
    (tmp_path / "harsh.toml").write_text(harsh_toml())
    cli(["new", "--config", str(tmp_path / "harsh.toml"), "--name", "t", "--founders", "10"])
    cli(["run", "t", "--days", "10", "--quiet"])
    cli(["status", "t"])
    cli(["inspect", "t", "John"])
    cli(["events", "t", "--type", "died"])
    store = WorldStore(tmp_path / "t.sqlite")
    died = store.events(type=ev.DIED, limit=1)[0]
    store.close()
    cli(["why", "t", str(died.id)])
    cli(["metrics", "t", "--genes", "--every", "5"])
    cli(["conversations", "t", "John"])
    cli(["relations", "t", "--all"])
    out = capsys.readouterr().out
    assert "Why did event" in out and "became" in out and "died of" in out
    assert "Strengths" in out and "metabolism" in out
    assert "Conversations: John" in out and "Who sees whom" in out


def test_messages_and_relationship_history_are_saved(tmp_path):
    sim = Simulation.create(seed=3, founders=6)
    store = WorldStore.create(tmp_path / "w.sqlite", sim)
    run_saving(sim, store, 10 * 24)

    n_messages = store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert n_messages == sim.log.counts.get("message", 0) > 0
    history = store.conn.execute("SELECT COUNT(*) FROM relationships_history").fetchone()[0]
    assert history > 0

    loaded = store.load()
    for a, b in zip(sim.population.agents, loaded.population.agents, strict=True):
        assert {k: r.to_dict() for k, r in a.relationships.items()} == {
            k: r.to_dict() for k, r in b.relationships.items()
        }
        assert a.inventory == b.inventory
    store.close()


def test_genes_skills_and_metrics_are_saved(tmp_path):
    sim = Simulation.create(seed=4)
    store = WorldStore.create(tmp_path / "w.sqlite", sim)
    run_saving(sim, store, 5 * 24)

    loaded = WorldStore(tmp_path / "w.sqlite").load()
    for a, b in zip(sim.population.agents, loaded.population.agents, strict=True):
        assert a.genome == b.genome
        assert a.abilities.to_dict() == b.abilities.to_dict()
        assert (a.born_tick, a.stage, a.generation) == (b.born_tick, b.stage, b.generation)

    rows = store.metrics(["population.alive", "gene.metabolism.mean"])
    assert len(rows) == 5  # one per finished day
    assert all(r["population.alive"] == 3 for r in rows.values())
    store.close()


def test_old_world_files_get_a_clear_message(tmp_path):
    store = WorldStore.create(tmp_path / "w.sqlite", Simulation.create())
    with store.conn:
        store.conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    with pytest.raises(WorldStoreError, match="Create a new world"):
        store.load()
    store.close()
