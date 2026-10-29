"""M9 God Mode and branching, M10 the complete dashboard."""

import pytest

from genesis.config import WorldConfig
from genesis.god import interventions as god
from genesis.god.compare import branch, summary
from genesis.history import events as ev
from genesis.persistence.db import WorldStore
from genesis.simulation import service
from genesis.simulation.engine import Simulation
from genesis.world.world import Position, TerrainType

TWO = "worlds/two_valleys.toml"


def world_file(tmp_path, name="w", days=0):
    folder = tmp_path / "worlds"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}.sqlite"
    sim = Simulation(WorldConfig.from_file(TWO))
    store = WorldStore.create(path, sim)
    if days:
        sim.run(4 * days)
        store.save(sim)
    store.close()
    return path


def load(path):
    store = WorldStore(path)
    sim = store.load()
    return store, sim


def free_spot(sim, size=3):
    busy = {a.position for a in sim.population.agents} | {
        f.position for f in sim.world.food_sources
    }
    for x in range(2, sim.world.width - 2):
        for y in range(2, sim.world.height - 2):
            tiles = [Position(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            if all(sim.world.is_walkable(t) and t not in busy for t in tiles):
                return x, y
    raise AssertionError("no free spot")


# =================================================================
# M9: interventions
# =================================================================


def test_god_changes_the_land_and_it_stays_changed(tmp_path):
    path = world_file(tmp_path)
    store, sim = load(path)
    x, y = free_spot(sim)
    lakes_before = sim.world.lakes
    event = god.apply(sim, {"act": "lake", "x": x, "y": y, "size": 3})
    assert sim.world.terrain[Position(x, y)] == TerrainType.WATER
    assert sim.world.lakes == lakes_before + 1 and Position(x, y) not in sim.world.walkable
    assert any(
        sim.world.can_drink_at(p) for p in Position(x, y + 2).neighbors() + [Position(x, y + 2)]
    )
    patch = god.apply(sim, {"act": "add_food", "x": x + 4, "y": y, "regen": 4})
    e = next(e for e in sim.log.pending if e.id == event)
    store.save(sim)
    store.close()

    store, again = load(path)  # loaded again: the same land
    store.close()
    assert again.world.terrain[Position(x, y)] == TerrainType.WATER
    assert any(f.position == Position(x + 4, y) for f in again.world.food_sources)
    assert e.type == ev.INTERVENTION and e.origin == ev.GOD and patch > event


def test_god_refuses_what_cannot_be(tmp_path):
    path = world_file(tmp_path)
    store, sim = load(path)
    store.close()
    someone = sim.population.agents[0].position
    with pytest.raises(god.GodError):
        god.apply(sim, {"act": "lake", "x": someone.x, "y": someone.y, "size": 1})
    with pytest.raises(god.GodError):
        god.apply(sim, {"act": "disaster", "kind": "earthquake"})
    with pytest.raises(god.GodError):
        god.apply(sim, {"act": "strike", "agent_id": 999})
    with pytest.raises(god.GodError):
        god.apply(sim, {"act": "fly"})


def test_a_sent_disaster_strikes_and_its_cause_is_god(tmp_path):
    path = world_file(tmp_path)
    store, sim = load(path)
    store.close()
    event = god.apply(sim, {"act": "disaster", "kind": "plague", "region": "all", "days": 5})
    sim.run(4 * 3)
    started = [e for e in sim.log.pending if e.type == ev.DISASTER and e.data["started"]]
    assert started and started[0].data["kind"] == "sickness"
    assert started[0].cause_id == event  # the cause chain leads back to God
    assert any(e.type == ev.FELL_SICK for e in sim.log.pending)


def test_migrants_arrive_strike_kills_and_a_whisper_is_remembered(tmp_path):
    path = world_file(tmp_path)
    store, sim = load(path)
    store.close()
    before = len(sim.population.get_alive())
    god.apply(sim, {"act": "migrants", "count": 3, "region": "east valley"})
    assert len(sim.population.get_alive()) == before + 3
    victim = sim.population.get_alive()[0]
    god.apply(sim, {"act": "strike", "agent_id": victim.id})
    assert not victim.alive and victim.cause_of_death == "god"
    listener = sim.population.get_alive()[0]
    god.apply(sim, {"act": "whisper", "agent_id": listener.id, "text": "the hills hold water"})
    assert any("voice from nowhere" in m.text for m in listener.stream.items)


def test_commands_are_queued_and_a_running_world_applies_them(tmp_path):
    path = world_file(tmp_path)
    store = WorldStore(path)
    god.enqueue(store.conn, {"act": "migrants", "count": 2})
    god.enqueue(store.conn, {"act": "strike", "agent_id": 999})  # cannot be done
    store.close()
    service.run(path, 4 * 2, llm=False, echo=lambda *_: None)
    store = WorldStore(path)
    rows = list(
        store.conn.execute("SELECT applied_tick, event_id, error FROM god_queue ORDER BY id")
    )
    store.close()
    assert rows[0][0] is not None and rows[0][1] is not None and rows[0][2] is None
    assert rows[1][2] and "no one alive" in rows[1][2]
    store, sim = load(path)
    store.close()
    assert len(sim.population.agents) == 14  # 12 founders + 2 newcomers


def test_a_branch_is_a_copy_that_can_go_its_own_way(tmp_path):
    a = world_file(tmp_path, "a", days=10)
    b = tmp_path / "worlds" / "b.sqlite"
    branch(a, b, "b")
    with pytest.raises(FileExistsError):
        branch(a, b, "b")
    store, sim = load(b)
    assert sim.config.name == "b" and sim.tick_count == 40
    god.apply(sim, {"act": "migrants", "count": 4})
    store.save(sim)
    store.close()
    sa, sb = summary(a), summary(b)
    assert sb["branched_from"] == {"world": "a", "tick": 40}
    assert sb["alive"] == sa["alive"] + 4 and sb["god_interventions"] == 1


# =================================================================
# M10: the complete dashboard
# =================================================================


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from genesis.dashboard.server import create_app

    monkeypatch.setenv("GENESIS_FAKE_LLM", "1")
    path = world_file(tmp_path, "w")
    world_file(tmp_path, "other", days=5)
    service.run(path, 4 * 370, llm=False, echo=lambda *_: None)  # into year 2: a snapshot
    store, sim = load(path)
    x, y = free_spot(sim)
    god.apply(sim, {"act": "lake", "x": x, "y": y, "size": 1})
    store.save(sim)
    store.close()
    return TestClient(create_app(path)), (x, y)


def test_the_map_shows_the_land_now_and_people_in_past_years(client):
    c, (x, y) = client
    m = c.get("/api/map").json()
    assert m["tiles"][y][x] == "w"  # God's pool is on the map
    assert m["years"] == [2] and m["people"]
    past = c.get("/api/map/year/2").json()
    assert past["people"] and all("x" in p for p in past["people"])


def test_mind_lab_compare_and_worlds(client):
    c, _ = client
    assert {"w", "other"} <= set(c.get("/api/worlds").json()["worlds"])
    lab = c.get("/api/mindlab").json()["worlds"][0]
    assert [y["year"] for y in lab["years"]][-1] == 2
    both = c.get("/api/mindlab/compare?other=other").json()["worlds"]
    assert [w["world"] for w in both] == ["w", "other"]
    cmp = c.get("/api/compare?other=other").json()
    assert cmp["worlds"][0]["god_interventions"] == 1 and set(cmp["series"]) == {"w", "other"}
    assert c.get("/api/compare?other=../../etc").status_code == 404


def test_god_panel_queues_and_shows_interventions(client):
    c, _ = client
    g = c.get("/api/god").json()
    assert len(g["done"]) == 1 and g["done"][0]["god"] is True
    assert c.post("/api/god", json={"act": "migrants", "count": 1}).status_code == 200
    assert c.get("/api/god").json()["queue"][0]["command"]["act"] == "migrants"
    assert c.post("/api/god", json={"act": "fly"}).status_code == 400


def test_interview_answers_from_their_memories_and_changes_nothing(client, tmp_path):
    c, _ = client
    path = tmp_path / "worlds" / "w.sqlite"
    before = path.stat().st_mtime
    a = c.post("/api/agents/1/interview", json={"question": "What do you remember?", "year": 1})
    assert a.status_code == 200
    body = a.json()
    assert body["answer"] and body["year"] == 1 and body["memoriesUsed"] > 0
    assert "Year 1" in body["asOf"] and path.stat().st_mtime == before
    assert c.post("/api/agents/1/interview", json={"question": ""}).status_code == 400


def test_sql_console_is_read_only_and_health_answers(client):
    c, _ = client
    ok = c.post("/api/sql", json={"query": "SELECT COUNT(*) AS n FROM agents"}).json()
    assert ok["columns"] == ["n"] and ok["rows"][0][0] >= 12
    for bad in ("DELETE FROM agents", "SELECT 1; DROP TABLE agents", "UPDATE meta SET value = 1"):
        assert c.post("/api/sql", json={"query": bad}).status_code == 400
    h = c.get("/api/health").json()
    assert h["dbMb"] > 0 and h["diskFreeGb"] > 0
