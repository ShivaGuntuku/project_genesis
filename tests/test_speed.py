"""S2.6: throughput and speed. Pacing holds a real-time target; history stays replayable."""

from types import SimpleNamespace

from fakes import FakeModel
from genesis.config import WorldConfig
from genesis.llm.mind import Pace, attach
from genesis.llm.settings import LLMConfig
from genesis.simulation.calendar import Calendar
from genesis.simulation.engine import Simulation

TWO = "worlds/two_valleys.toml"


def _ctx(tick):
    cal = Calendar(ticks_per_day=4, days_per_year=360)
    return SimpleNamespace(tick=tick, world=SimpleNamespace(calendar=cal))


def _plan(agent_id):
    return SimpleNamespace(kind="plan", agent=SimpleNamespace(id=agent_id))


def test_pacing_asks_fewer_plans_when_slow_and_all_again_when_caught_up():
    now = [0.0]
    pace = Pace(10, clock=lambda: now[0])  # 10 real minutes per simulated year
    span = 4 * Pace.DAYS
    tick = 0
    pace.update(_ctx(tick))
    for _ in range(6):  # each 10 days takes twice the target's time
        tick += span
        now[0] += 2 * 10 * 60 * Pace.DAYS / 360
        pace.update(_ctx(tick))
    assert Pace.LOWEST <= pace.share < 0.3 and round(pace.last_minutes) == 20
    kept = sum(pace.keep(_ctx(tick), _plan(i)) for i in range(1000))
    assert 0.1 * 1000 < kept < 0.4 * 1000  # about the share, chosen without chance
    assert all(pace.keep(_ctx(tick), SimpleNamespace(kind="trade", agent=None)) for _ in range(9))
    for _ in range(12):  # now much faster than the target: back to every plan
        tick += span
        now[0] += 0.2 * 10 * 60 * Pace.DAYS / 360
        pace.update(_ctx(tick))
    assert pace.share == 1.0
    assert Pace(0).keep(_ctx(0), _plan(1))  # 0 = off


def test_a_paced_run_leaves_some_plans_to_the_rules_and_keeps_going():
    llm = LLMConfig(enabled=True, plan_every_days=1, target_minutes_per_year=0.001)
    sim = Simulation(WorldConfig.from_file(TWO).with_overrides(llm=llm))
    attach(sim, gateway=FakeModel())
    sim.run(4 * 40)
    stats = sim.mind.stats
    assert stats["skipped_pace"] > 0 and stats["ok"] > 0
    assert sim.mind.pace.share == Pace.LOWEST  # a fake model is never that fast


def test_tiles_hash_as_before_so_sets_keep_their_order():
    from genesis.mind.memory import KnownLocation
    from genesis.world.world import Position

    assert hash(Position(3, 4)) == hash((3, 4))  # what the frozen dataclass hashed
    assert hash(KnownLocation("water", Position(3, 4))) == hash(("water", (3, 4)))


def test_the_leash_grid_is_the_distance_to_the_nearest_known_place():
    import random

    from genesis.mind.memory import AgentMemory
    from genesis.world.world import Position

    rng = random.Random(5)
    memory = AgentMemory()
    width, height = 30, 20

    def brute(resource):
        places = [k.position for k in memory.known_locations if k.resource_type == resource]
        return [
            [min(abs(x - p.x) + abs(y - p.y) for p in places) for y in range(height)]
            for x in range(width)
        ]

    for step in range(60):
        p = Position(rng.randrange(width), rng.randrange(height))
        memory.remember_location(rng.choice(["water", "food"]), p)
        if step % 7 == 6:  # forgetting one rebuilds the grid
            gone = rng.choice(memory.known_locations)
            memory.forget_location(gone.resource_type, gone.position)
        if step % 5 == 4:
            for resource in ("water", "food"):
                if any(k.resource_type == resource for k in memory.known_locations):
                    grid = memory.near_grid(resource, width, height)
                    assert grid.tolist() == brute(resource), (step, resource)


def test_a_look_at_the_same_view_adds_nothing_and_a_new_view_is_counted():
    from genesis.mind.memory import AgentMemory
    from genesis.mind.perception import _square

    memory = AgentMemory()
    view, _ = _square(0, 2, 0, 2)
    assert memory.remember_seen(view) == 9
    assert memory.remember_seen(view) == 0
    memory.seen_positions.clear()  # whatever happens to the set, a look counts again
    assert memory.remember_seen(view) == 9
    other, _ = _square(1, 3, 0, 2)
    assert memory.remember_seen(other) == 3


def test_small_talk_waits_for_spare_time_but_hard_choices_do_not():
    from genesis.llm.mind import Governor

    now = [0.0]
    g = Governor(1000, clock=lambda: now[0])  # a count limit far away: only time matters
    assert g.allow(important=False)
    for _ in range(55):  # the model answered for 55 of the last 60 seconds
        g.record(1.0)
    assert g.busy() > Governor.BUSY
    assert not g.allow(important=False)  # small talk: the rules speak
    assert g.allow(important=True)  # a hard choice still goes to the model
    now[0] += 61  # a quiet minute later
    assert g.busy() == 0 and g.allow(important=False)
