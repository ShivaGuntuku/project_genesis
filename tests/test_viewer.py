"""M3.5: the live viewer server (the browser app itself is checked with `npm run build`)."""

from fastapi.testclient import TestClient

from genesis.api import views
from genesis.api.server import Runner, create_app
from genesis.history import events as ev
from genesis.persistence.db import WorldStore
from genesis.simulation.engine import Simulation


def make_world(tmp_path, seed=3, founders=6):
    path = tmp_path / "w.sqlite"
    WorldStore.create(path, Simulation.create(seed=seed, founders=founders)).close()
    return path


def test_watching_never_changes_history(tmp_path):
    """M3.5 check: a world run in the viewer ends exactly like `genesis run`."""
    reference = Simulation.create(seed=3, founders=6)
    reference.run(300)

    path = make_world(tmp_path)
    runner = Runner(path, speed=0, max_ticks=300)  # max speed
    with TestClient(create_app(runner)) as client:
        client.get("/api/state")
        assert runner.finished.wait(60)

    store = WorldStore(path)
    assert store.load().state() == reference.state()
    reason = store.conn.execute("SELECT end_reason FROM runs").fetchone()[0]
    assert reason == "finished"
    store.close()


def test_api_snapshot_agent_and_why(tmp_path):
    path = make_world(tmp_path)
    runner = Runner(path, speed=0, max_ticks=120)
    with TestClient(create_app(runner)) as client:
        assert runner.finished.wait(60)

        snap = client.get("/api/state").json()
        assert snap["type"] == "snapshot"
        assert len(snap["world"]["tiles"]) == 20 and set("".join(snap["world"]["tiles"])) <= set(
            "pfmw"
        )
        assert len(snap["agents"]) == 6
        assert {"skin", "hair", "eyes", "strength"} <= set(snap["agents"][0]["looks"])
        assert snap["frame"]["tick"] == 120

        detail = client.get("/api/agent/1").json()
        assert detail["name"] == "John"
        assert {"health", "hunger", "thirst", "energy"} == set(detail["needs"])
        assert detail["events"] and "relationships" in detail

        with_cause = next(e for e in detail["events"] if e["hasCause"])
        why = client.get(f"/api/why/{with_cause['id']}").json()
        assert why["chain"][-1]["id"] == with_cause["id"] and len(why["chain"]) >= 2

        assert client.get("/api/agent/999").status_code == 404
        assert client.get("/api/why/999999").status_code == 404


def test_websocket_streams_frames_and_obeys_controls(tmp_path):
    path = make_world(tmp_path)
    runner = Runner(path, speed=20)
    with TestClient(create_app(runner)) as client, client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot"

        frame = ws.receive_json()
        while frame["type"] != "tick":
            frame = ws.receive_json()
        assert {"tick", "clock", "agents", "food", "links", "events"} <= set(frame)
        assert {"x", "y", "facing", "icon", "stage"} <= set(frame["agents"][0])

        ws.send_json({"cmd": "pause"})
        status = ws.receive_json()
        while status["type"] != "status":
            status = ws.receive_json()
        assert status["paused"] is True

        ws.send_json({"cmd": "speed", "value": 5})
        status = ws.receive_json()
        while status["type"] != "status":
            status = ws.receive_json()
        assert status["speed"] == 5


def test_frames_describe_speech_and_moments():
    sim = Simulation.create(seed=3, founders=6)
    sim.run(10 * 24)
    names = {a.id: a.name for a in sim.population.agents}
    items = [views.event_item(e, names, sim) for e in sim.log.recent]

    spoken = [i for i in items if i["act"]]
    assert spoken and all(i["bubble"] for i in spoken)
    assert any(i["routine"] for i in items) and any(not i["routine"] for i in items)
    for item in items:
        if item["type"] in (ev.SKILL_IMPROVED, ev.DIED, ev.LIE_DISCOVERED):
            assert item["float"]

    frame = views.frame(sim, [], names)
    assert all(link[2] in views.LINK_LABELS for link in frame["links"])


def test_agents_face_the_way_they_walk():
    sim = Simulation.create(seed=1, founders=3)
    before = {a.id: a.position for a in sim.population.agents}
    for _ in range(30):
        sim.tick()
        for a in sim.population.agents:
            old = before[a.id]
            if a.position != old:
                dx, dy = a.position.x - old.x, a.position.y - old.y
                expected = {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}
                assert a.facing == expected[(dx, dy)]
            before[a.id] = a.position
