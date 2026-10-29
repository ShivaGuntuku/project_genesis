from genesis.history import events as ev
from genesis.history.describe import clock, describe
from genesis.simulation.engine import Simulation
from helpers import World, make_agent, step


def test_founders_arrive_because_the_world_was_created():
    sim = Simulation.create(founders=3)
    created, *arrivals = sim.log.recent[:4]
    assert created.type == ev.WORLD_CREATED and created.origin == ev.SYSTEM
    assert [e.type for e in arrivals] == [ev.FOUNDER_ARRIVED] * 3
    assert all(e.cause_id == created.id for e in arrivals)


def test_event_ids_are_sequential_and_identical_across_runs():
    a, b = Simulation.create(seed=5), Simulation.create(seed=5)
    a.run(300)
    b.run(300)
    ids = [e.id for e in a.log.recent]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert a.log.recent == b.log.recent


def test_death_is_caused_by_suffering_which_is_caused_by_need():
    world = World()
    agent = make_agent(x=1, y=1, thirst=99, health=10)  # far from water, about to suffer
    log = None
    for tick in range(20):
        log = step(agent, world, tick=tick, log=log)
        if not agent.alive:
            break

    by_id = {e.id: e for e in log.recent}
    died = next(e for e in log.recent if e.type == ev.DIED)
    suffering = by_id[died.cause_id]
    pressing = by_id[suffering.cause_id]
    assert suffering.type == ev.SUFFERING and suffering.data["need"] == "thirst"
    assert pressing.type == ev.NEED_BECAME_PRESSING
    assert died.data["cause"] == "dehydration"


def test_discovery_while_searching_is_caused_by_the_need():
    world = World()
    agent = make_agent(x=2, y=2, thirst=60)  # thirsty, has never seen water
    log = None
    for tick in range(40):
        log = step(agent, world, tick=tick, log=log)

    found = next(e for e in log.recent if e.type == ev.DISCOVERED and e.data["resource"] == "water")
    cause = next(e for e in log.recent if e.id == found.cause_id)
    assert cause.type == ev.NEED_BECAME_PRESSING and cause.data["need"] == "thirst"


def test_each_place_is_discovered_only_once():
    sim = Simulation.create(seed=42)
    sim.run(720)
    seen = [
        (e.actors, e.data["resource"], e.x, e.y)
        for e in sim.log.recent
        if e.type == ev.DISCOVERED and e.data["resource"] == "food"
    ]
    assert len(seen) == len(set(seen))


def test_agents_remember_the_events_they_took_part_in():
    sim = Simulation.create(seed=1)
    sim.run(100)
    for agent in sim.population.agents:
        mine = [e.id for e in sim.log.recent if agent.id in e.actors]
        assert set(mine) <= set(agent.memory.events)


def test_describe_and_clock():
    sim = Simulation.create(founders=1)
    names = {a.id: a.name for a in sim.population.agents}
    arrival = sim.log.recent[1]
    assert describe(arrival, names).startswith("John arrived")
    assert clock(0, 8) == "Day 1 08:00"
    assert clock(16, 8) == "Day 2 00:00"
