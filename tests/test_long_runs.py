"""M8.5: long runs that look after themselves - journal, workers, service, snapshots,
integrity, the dashboard's run page, family tree and exports, speed."""

import random
import subprocess
import sys
from collections import deque

from genesis.config import WorldConfig
from genesis.llm.fake import FakeGateway
from genesis.llm.mind import attach
from genesis.llm.settings import LLMConfig
from genesis.mind.planner import NavigationPlanner
from genesis.persistence.db import WorldStore, attach_journal
from genesis.simulation import service
from genesis.simulation.engine import Simulation

TWO = "worlds/two_valleys.toml"


def llm_config(**llm):
    config = WorldConfig.from_file(TWO)
    settings = dict(enabled=True, plan_every_days=2, voice_greetings=False, chatter_share=0.3)
    settings.update(llm)
    return config.with_overrides(llm=LLMConfig(**settings))


class Forbidden(FakeGateway):
    """A model that must not be asked: every answer has to come from the journal."""

    def chat(self, *a, **k):
        raise AssertionError("asked the model for an answer the journal already has")


# =================================================================
# The journal: no LLM answer is lost in a crash
# =================================================================


def test_after_a_crash_the_journal_gives_back_every_answer(tmp_path):
    config = llm_config()
    path = tmp_path / "w.sqlite"
    sim = Simulation(config)
    attach(sim, gateway=FakeGateway())
    store = WorldStore.create(path, sim)
    attach_journal(sim, path, store)
    for _ in range(4 * 6):  # six days, saved each day
        sim.tick()
        if sim.tick_count % 4 == 0:
            store.save(sim)
    for _ in range(3):  # most of a seventh day... then the machine dies (no save)
        sim.tick()
    crashed_at = sim.tick_count
    crash_state = sim.state()
    store.close()

    store = WorldStore(path)
    resumed = store.load()
    attach(resumed, gateway=Forbidden())
    assert attach_journal(resumed, path, store) > 0
    resumed.run(crashed_at - resumed.tick_count)  # the lost part: only from the journal
    assert resumed.state() == crash_state
    assert not resumed.mind.recorded  # every journaled answer was used
    store.close()


def test_saves_take_in_the_journal(tmp_path):
    config = llm_config()
    path = tmp_path / "w.sqlite"
    sim = Simulation(config)
    attach(sim, gateway=FakeGateway())
    store = WorldStore.create(path, sim)
    attach_journal(sim, path, store)
    sim.run(4 * 3)
    assert store.conn.execute("SELECT COUNT(*) FROM llm_journal").fetchone()[0] > 0
    store.save(sim)
    assert store.conn.execute("SELECT COUNT(*) FROM llm_journal").fetchone()[0] == 0
    store.close()


def test_several_workers_give_the_same_history_as_one():
    one = Simulation(llm_config(workers=1))
    attach(one, gateway=FakeGateway())
    many = Simulation(llm_config(workers=4))
    attach(many, gateway=FakeGateway())
    one.run(4 * 8)
    many.run(4 * 8)
    assert any(c["kind"] == "plan" for c in many.pending_llm_calls)
    assert one.state() == many.state()


# =================================================================
# The service: status, control, snapshots, integrity
# =================================================================


def _world(tmp_path, name="w"):
    folder = tmp_path / "worlds"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}.sqlite"
    WorldStore.create(path, Simulation(WorldConfig.from_file(TWO))).close()
    return path


def test_a_long_run_reaches_its_target_and_says_so(tmp_path):
    path = _world(tmp_path)
    reason = service.run(path, 4 * 10, llm=False, snapshot_minutes=0, echo=lambda *_: None)
    status = service.read_status(path)
    assert reason == service.FINISHED and status["state"] == "finished"
    assert status["tick"] == 40 and status["alive"] > 0
    assert service.snapshots(path)  # one at every save with snapshot_minutes = 0
    assert len(service.snapshots(path)) <= 5


def test_the_control_file_pauses_and_stops_a_running_world(tmp_path):
    import threading
    import time

    path = _world(tmp_path)
    service.run(path, 4 * 5, llm=False, echo=lambda *_: None)
    result = {}
    thread = threading.Thread(
        target=lambda: result.update(
            reason=service.run(path, 4 * 5000, llm=False, echo=lambda *_: None)
        )
    )
    thread.start()

    def wait_for(state):
        for _ in range(600):
            if service.read_status(path).get("state") == state:
                return True
            time.sleep(0.05)
        return False

    assert wait_for("running")
    service.control(path, "pause")
    assert wait_for("paused")
    paused_at = service.read_status(path)["tick"]
    time.sleep(0.5)
    assert service.read_status(path)["tick"] == paused_at  # really paused
    service.control(path, "run")
    assert wait_for("running")
    service.control(path, "stop")
    thread.join(60)
    assert result["reason"] == service.STOPPED
    status = service.read_status(path)
    assert status["state"] == "stopped" and status["target_tick"] == 4 * 5000
    assert status["tick"] % 4 == 0  # stopped at a daily save


def test_a_damaged_world_file_is_restored_from_its_newest_snapshot(tmp_path):
    path = _world(tmp_path)
    service.run(path, 4 * 6, llm=False, snapshot_minutes=0, echo=lambda *_: None)
    assert service.intact(path)
    with open(path, "r+b") as f:  # the disk fails
        f.seek(4096)
        f.write(b"\x00garbage" * 4000)
    assert not service.intact(path)
    service.run(path, 4 * 8, llm=False, snapshot_minutes=0, echo=lambda *_: None)
    assert service.intact(path) and path.with_suffix(".damaged").exists()
    assert service.read_status(path)["tick"] == 32


def test_the_service_unit_resumes_the_world_and_stops_cleanly(tmp_path):
    text = service.unit_text("main", tmp_path, "/usr/bin/uv")
    assert "genesis resume main" in text and "Restart=on-failure" in text
    assert "KillSignal=SIGTERM" in text


def test_kill_minus_nine_never_changes_the_history():
    out = subprocess.run(
        [sys.executable, "scripts/kill_test.py", "--kills", "3", "--days", "240", "--seed", "3"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert "IDENTICAL" in out.stdout


# =================================================================
# Speed, and the dashboard for long runs
# =================================================================


def test_paths_are_still_the_shortest():
    world = Simulation(WorldConfig.from_file("worlds/millennium.toml")).world
    tiles = sorted(world.walkable, key=lambda p: (p.x, p.y))
    rng = random.Random(4)
    nav = NavigationPlanner()
    for _ in range(60):
        a, b = rng.choice(tiles), rng.choice(tiles)
        path = nav.find_path(a, b, world)
        seen, queue = {a: 0}, deque([a])
        while queue:
            c = queue.popleft()
            for n in world.adjacent[c]:
                if n not in seen:
                    seen[n] = seen[c] + 1
                    queue.append(n)
        if b not in seen:
            assert path is None
            continue
        assert len(path) - 1 == seen[b]
        assert all(
            abs(x.x - y.x) + abs(x.y - y.y) == 1 for x, y in zip(path, path[1:], strict=False)
        )


def test_long_series_are_averaged_for_the_charts(tmp_path):
    path = _world(tmp_path)
    service.run(path, 4 * 40, llm=False, echo=lambda *_: None)
    store = WorldStore(path)
    full = store.metrics(["population.alive"])
    short = store.metrics(["population.alive"], max_points=10)
    store.close()
    assert len(full) >= 40 and 5 <= len(short) <= 10


def test_the_dashboard_shows_and_steers_the_run_and_exports(tmp_path):
    from fastapi.testclient import TestClient

    from genesis.dashboard.server import create_app

    path = _world(tmp_path)
    service.run(path, 4 * 10, llm=False, echo=lambda *_: None)
    client = TestClient(create_app(path))
    run = client.get("/api/run").json()
    assert run["state"] == "finished" and run["alive"] > 0
    assert client.post("/api/run/pause").status_code == 200
    assert service._read_control(path) == "pause"
    assert client.post("/api/run/fly").status_code == 400
    people = client.get("/api/family").json()["people"]
    assert people and all("parents" in p for p in people)
    csv = client.get("/api/export/agents.csv")
    assert csv.status_code == 200 and csv.text.startswith("id,name,generation")
    assert client.get("/api/export/nothing.csv").status_code == 404
