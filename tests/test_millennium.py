"""M8a: the millennium engine (watches, years, seasons, sleep shifts, LLM governor)."""

from dataclasses import replace

import pytest

from fakes import FakeModel
from genesis.agents import rhythm
from genesis.agents.genome import Genome
from genesis.config import WorldConfig
from genesis.llm.mind import Governor, attach
from genesis.llm.settings import LLMConfig
from genesis.mind.decision import ActionType
from genesis.persistence.db import WorldStore
from genesis.rules import Rules
from genesis.simulation.calendar import Calendar
from genesis.simulation.engine import Simulation
from helpers import World, make_agent, make_ctx

MILLENNIUM = "worlds/two_valleys.toml"
YEAR = 1440  # watches


def millennium(**rules) -> WorldConfig:
    config = WorldConfig.from_file(MILLENNIUM)
    return config.with_overrides(rules=replace(config.rules, **rules)) if rules else config


# =================================================================
# Calendar
# =================================================================


def test_calendar_in_hours_and_in_watches():
    story = Calendar(ticks_per_day=24, start_hour=8)
    assert story.clock(0) == "Day 1 08:00" and story.clock(16) == "Day 2 00:00"
    assert story.is_day_end(16) and not story.is_day_end(17)
    assert story.ticks(days=3) == 72 and story.ticks(hours=24) == 24

    watches = Calendar(ticks_per_day=4, start_hour=6, seasons=True)
    assert watches.hours_per_tick == 6 and watches.watch(0) == "dawn" and watches.watch(1) == "day"
    assert watches.clock(0) == "Year 1, spring day 1, dawn"
    assert watches.is_day_end(3) and not watches.is_day_end(4)  # tick 3 starts at midnight
    assert watches.year(YEAR) == 2 and watches.season(90 * 4) == "wet season"
    assert watches.ticks(days=270) == 1080 and watches.ticks(hours=1) == 1  # at least a tick
    assert watches.age_days(YEAR) == 360


def test_old_configs_stay_hourly_and_bad_time_settings_are_refused():
    assert WorldConfig.default().ticks_per_day == 24
    text = WorldConfig.default().source_toml
    from genesis.config import ConfigError

    with pytest.raises(ConfigError):
        WorldConfig.from_toml(text.replace("start_hour = 8", "start_hour = 8\nticks_per_day = 5"))
    config = WorldConfig.from_file(MILLENNIUM)
    assert config.ticks_per_day == 4 and config.seasons and config.save_every_ticks == 4
    assert sum(s.founders for s in config.starts) == config.founders


# =================================================================
# Watches: needs, walking, hours
# =================================================================


def test_a_watch_of_needs_equals_six_hours_of_needs():
    hourly = World()
    watched = World(ticks_per_day=4, start_hour=6)
    a = make_agent(agent_id=1, name="A", thirst=10, hunger=10)
    b = make_agent(agent_id=1, name="A", thirst=10, hunger=10)
    from genesis.history.events import EventLog

    log = EventLog()
    import random

    for t in range(6):
        a.update_needs(hourly, t, log, random.Random(0))
    b.update_needs(watched, 0, log, random.Random(0))
    assert round(a.thirst, 6) == round(b.thirst, 6) and round(a.hunger, 6) == round(b.hunger, 6)


def test_in_a_watch_agents_walk_several_tiles_and_talk_once():
    world = World(ticks_per_day=4, start_hour=6)
    walker = make_agent(x=2, y=2, agent_id=1, name="Walker", thirst=60)
    walker.memory.remember_location(
        "water", next(iter(sorted(world.shore, key=lambda p: (p.x, p.y))))
    )
    ctx = make_ctx(world, [walker])
    walker.perceive(ctx)
    walker.decide(world)
    assert walker.current_action == ActionType.MOVE_TO_WATER
    walker.hours_left = 6
    start = walker.position
    walker.act(ctx)
    assert start.distance(walker.position) > 1  # more than one tile in a watch
    assert walker.hours_left < 6

    a = make_agent(agent_id=2, name="A")
    b = make_agent(x=11, agent_id=3, name="B")
    ctx = make_ctx(world, [a, b])
    a.talked = True
    a.perceive(ctx)
    a.decide(world)
    assert a.current_action != ActionType.SOCIALIZE  # already talked this watch


# =================================================================
# Sleep shifts
# =================================================================


def test_sleep_windows_follow_the_genes():
    r = Rules()
    early_short = Genome.neutral().with_genes(sleep_need=0.0, chronotype=0.0)
    late_long = Genome.neutral().with_genes(sleep_need=1.0, chronotype=1.0)
    assert rhythm.sleep_hours(early_short, r) == 6 and rhythm.bedtime(early_short, r) == 20
    assert rhythm.sleep_hours(late_long, r) == 10 and rhythm.bedtime(late_long, r) == 2
    assert rhythm.asleep_at(early_short, r, 22, 1) and not rhythm.asleep_at(early_short, r, 3, 1)
    assert rhythm.asleep_at(late_long, r, 6, 6) and not rhythm.asleep_at(late_long, r, 18, 6)


def test_people_sleep_in_shifts_and_sleepers_never_call_the_llm():
    config = millennium().with_overrides(llm=LLMConfig(enabled=True))
    sim = Simulation(config)
    attach(sim, gateway=FakeModel())
    asking = []
    original = sim.mind.ask

    def watched_ask(ctx, index, q):  # who is asking, at the moment of asking
        asking.append((q.kind, q.agent.current_action))
        return original(ctx, index, q)

    sim.mind.ask = watched_ask
    asleep_by_watch = {w: set() for w in ("night", "dawn", "day", "dusk")}
    for _ in range(12):  # three days
        sim.tick()
        watch = sim.world.calendar.watch(sim.tick_count - 1)
        asleep_by_watch[watch] |= {a.id for a in sim.population.get_alive() if a.sleeping}
    assert sum(1 for s in asleep_by_watch.values() if s) >= 2  # sleep is spread over watches
    awake = [(kind, action) for kind, action in asking if kind != "reflect"]  # reflect: at night
    assert awake, "awake agents should still talk"
    assert all(action != ActionType.SLEEP for _, action in awake)


def test_nobody_sleeps_into_a_crisis():
    world = World(ticks_per_day=4, start_hour=18, rules=replace(Rules(), scheduled_sleep=1))
    sleepy = make_agent(
        agent_id=1, name="Sleepy", thirst=60, genes={"chronotype": 0.0, "sleep_need": 1.0}
    )  # bed at 20:00
    ctx = make_ctx(world, [sleepy])
    sleepy.perceive(ctx)
    sleepy.decide(world)
    assert sleepy.current_action != ActionType.SLEEP  # 60 + a watch of thirst would be urgent
    sleepy.thirst = 10
    sleepy.decide(world)
    assert sleepy.current_action == ActionType.SLEEP


# =================================================================
# LLM governor
# =================================================================


def test_the_governor_keeps_calls_within_its_band():
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    g = Governor(10, clock=lambda: now[0], sleep=sleep)
    allowed = [g.allow(important=False) for _ in range(20)]
    assert sum(allowed) == 9  # chatter: up to 90% of the maximum, then the rules decide
    assert g.allow(important=True) and g.load() == 1.0  # hard choices: up to 100%
    assert g.allow(important=True)  # window full: it waits for a free slot instead
    assert now[0] >= 60 and g.waited >= 60
    in_window = [t for t in g.calls if now[0] - t < 60]
    assert len(in_window) <= 10  # never above the maximum
    assert Governor(0).allow(False)  # 0 = no limit


def test_over_the_limit_the_rules_decide_and_nothing_breaks():
    config = millennium().with_overrides(llm=LLMConfig(enabled=True, max_calls_per_minute=5))
    sim = Simulation(config)
    attach(sim, gateway=FakeModel())
    now = [0.0]  # a fake clock: waiting for a slot moves it on instead of really waiting

    def sleep(seconds):
        now[0] += seconds

    governor = sim.mind.governor
    governor.clock, governor.sleep = (lambda: now[0]), sleep
    times = []
    allow = governor.allow

    def recorded(important):
        ok = allow(important)
        if ok:
            times.append(now[0])
        return ok

    governor.allow = recorded
    sim.run(16)
    assert times and len(sim.pending_llm_calls) == len(times)
    for t in times:  # never more than the maximum in any one-minute window
        assert sum(1 for u in times if t <= u < t + 60) <= 5
    assert sim.mind.stats["skipped_rate"] > 0
    assert sim.population.get_alive()


# =================================================================
# Seasons, valleys, reflection, survival, saving
# =================================================================


def test_berries_regrow_by_season():
    world = World(ticks_per_day=4, start_hour=6, seasons=True)
    patch = world.food_sources[0]
    grown = []
    for season in range(4):
        patch.quantity = 0
        world.update(season * 90 * 4)
        grown.append(patch.quantity)
    r = world.rules
    expected = [
        round(patch.regen_per_tick * 6 * f)
        for f in (r.regen_spring, r.regen_wet, r.regen_dry, r.regen_cold)
    ]
    assert grown == expected and grown[0] > grown[3]


def test_founders_start_in_their_valleys():
    sim = Simulation(millennium())
    west = [a for a in sim.population.agents if a.position.x < 20]
    east = [a for a in sim.population.agents if a.position.x > 23]
    assert len(west) == 6 and len(east) == 6


def test_reflection_comes_every_n_days():
    config = millennium(reflect_every_days=2).with_overrides(llm=LLMConfig(enabled=True))
    sim = Simulation(config)
    attach(sim, gateway=FakeModel())
    sim.run(4 * 6)  # six days
    days = {
        sim.world.calendar.day(c["tick"]) for c in sim.pending_llm_calls if c["kind"] == "reflect"
    }
    assert days and all(d % 2 == 0 for d in days)


def test_a_millennium_valley_lives_on_and_resumes_exactly(tmp_path):
    reference = Simulation(millennium())
    reference.run(2 * YEAR)
    alive = reference.population.get_alive()
    assert len(alive) >= 6  # at least half the founders live through the first years (seed 11
    # is the hardest start of 12 seeds: 6-8 alive at year 2; others 9-13)
    assert reference.world.calendar.year(reference.tick_count) == 3

    path = tmp_path / "m.sqlite"
    sim = Simulation(millennium())
    store = WorldStore.create(path, sim)
    for _ in range(YEAR + 7):  # save once a day, as the preset does
        sim.tick()
        if sim.tick_count % sim.config.save_every_ticks == 0:
            store.save(sim)
    store.close()
    store = WorldStore(path)
    resumed = store.load()
    store.close()
    assert resumed.tick_count == YEAR + 4  # the last daily save
    resumed.run(2 * YEAR - resumed.tick_count)
    assert resumed.state() == reference.state()


def test_viewer_and_dashboard_speak_years_and_watches(tmp_path):
    from fastapi.testclient import TestClient

    from genesis.api.server import Runner, create_app
    from genesis.dashboard.server import create_app as dashboard_app

    path = tmp_path / "m.sqlite"
    WorldStore.create(path, Simulation(millennium())).close()
    runner = Runner(path, speed=0, max_ticks=10)
    with TestClient(create_app(runner)) as client:
        assert runner.finished.wait(60)
        snap = client.get("/api/state").json()
        assert snap["frame"]["clock"].startswith("Year 1, spring day 3")
        detail = client.get("/api/agent/1").json()
        assert detail["ageDays"] >= 18 * 360  # founders are adults, in years

    dash = TestClient(dashboard_app(path))
    over = dash.get("/api/overview").json()
    assert over["clock"].startswith("Year 1") and over["day"] == 3
    assert dash.get("/api/conversations?day_from=1&day_to=2").status_code == 200
    rows = dash.get("/api/agents").json()
    assert all(r["ageDays"] >= 18 * 360 for r in rows if r["generation"] == 0)
