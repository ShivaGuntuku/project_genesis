"""M8e: generated valleys, disasters, staged growth, migration, yearly snapshots, experiments."""

import json
from dataclasses import replace

import pytest

from genesis.config import ConfigError, WorldConfig
from genesis.history import events as ev
from genesis.mind.decision import ActionType
from genesis.mind.planner import ExplorationPlanner, leashes
from genesis.persistence.db import WorldStore
from genesis.simulation import experiment
from genesis.simulation.engine import Simulation
from genesis.world import disasters, generate
from genesis.world.disasters import Disaster
from genesis.world.world import Position, TerrainType
from helpers import make_agent, make_ctx

MILLENNIUM = "worlds/millennium.toml"


def small(**rules) -> WorldConfig:
    """The millennium preset on a smaller map: 2 valleys, 8 founders (fast to run)."""
    text = (
        open(MILLENNIUM)
        .read()
        .replace("width = 100", "width = 60")
        .replace("height = 100", "height = 40")
        .replace("valleys = 3", "valleys = 2")
        .replace("founders = 24", "founders = 8")
    )
    config = WorldConfig.from_toml(text)
    return config.with_overrides(rules=replace(config.rules, **rules)) if rules else config


def world_of(config):
    return Simulation(config).world


# =================================================================
# Generated maps
# =================================================================


def test_the_same_seed_makes_the_same_valleys_and_every_place_can_be_reached():
    a = generate.valleys(5, 100, 100, 3, 24)
    assert a == generate.valleys(5, 100, 100, 3, 24)
    assert a != generate.valleys(6, 100, 100, 3, 24)
    assert [s["name"] for s in a["starts"]] == ["west valley", "middle valley", "east valley"]
    assert sum(s["founders"] for s in a["starts"]) == 24
    assert generate._connected(a, 100, 100)
    stages = sorted({f["stage"] for f in a["food"]})
    assert stages[0] == 0 and stages[-1] == 40  # some patches wake only as people grow
    with pytest.raises(ConfigError):
        WorldConfig.from_toml(open(MILLENNIUM).read().replace("valleys = 3", "valleys = 5"))


def test_ridges_divide_the_valleys_and_only_a_pass_crosses_them():
    world = world_of(small())
    assert world.region_names == ["west valley", "east valley"]
    peaks = [p for p, t in world.terrain.items() if t == TerrainType.PEAK]
    assert peaks and not any(world.is_walkable(p) for p in peaks)
    ridge_x = {p.x for p in peaks}
    gap = [p for p in world.walkable if p.x in ridge_x]
    assert gap  # the pass
    west, east = (Position(x, y) for _, x, y in world.regions)
    assert world.region_at[west] == "west valley" and world.region_at[east] == "east valley"
    for region, lakes in world.lakes_in.items():  # biggest lake first
        sizes = [sum(1 for v in world.lake_of.values() if v == lake) for lake in lakes]
        assert sizes == sorted(sizes, reverse=True), region


def test_dormant_patches_wake_as_the_population_grows():
    world = world_of(small())
    dormant = next(f for f in world.food_sources if f.stage > 0)
    world.peak_population = dormant.stage - 1
    world.update(0)
    assert dormant.quantity == 0
    world.peak_population = dormant.stage
    world.update(0)
    assert dormant.quantity > 0


def test_founders_know_their_lake_and_nearest_berries():
    sim = Simulation(small())
    for a in sim.population.agents:
        assert a.memory.get_known_locations("water") and a.memory.get_known_locations("food")


# =================================================================
# The disaster schedule and what it does to the land
# =================================================================


def test_disasters_come_every_15_to_25_years_from_the_seed():
    r = small().rules
    plan = disasters.schedule(11, r, 360, ["west valley", "east valley"])
    assert plan == disasters.schedule(11, r, 360, ["west valley", "east valley"])
    assert len(plan) > 30  # over a thousand years
    days = [d.day for d in plan]
    gaps = [(b - a) / 360 for a, b in zip(days, days[1:], strict=False)]
    assert 15 * 360 <= days[0] <= 25 * 360 and all(15 <= g <= 25 for g in gaps)
    assert {d.kind for d in plan} == set(disasters.KINDS)
    assert {d.region for d in plan} >= {"all", "west valley", "east valley"}
    assert disasters.schedule(11, replace(r, disasters=0), 360, ["x"]) == ()


def _with(world, *plan):
    world.disasters = tuple(plan)
    return world


def test_a_drought_dries_all_but_the_biggest_lake_and_slows_the_berries():
    world = world_of(small())
    _with(world, Disaster(1, "drought", "west valley", 10, 100))
    lakes = world.lakes_in["west valley"]
    assert disasters.conditions(world, 5)["dry"] == frozenset()
    c = disasters.conditions(world, 20)
    assert c["dry"] == frozenset(lakes[1:]) and c["factor"] == {"west valley": 0.5}
    assert disasters.conditions(world, 200)["dry"] == frozenset()


def test_floods_take_patches_near_water_and_fires_burn_woods_for_years():
    world = world_of(small())
    _with(
        world,
        Disaster(1, "flood", "all", 0, 30),
        Disaster(2, "wildfire", "east valley", 100, 20),
    )
    flood = disasters.conditions(world, 1)["famine"]
    assert all(world.near_water(f.position, 3) for f in world.food_sources if f.id in flood)
    burned = disasters.conditions(world, 150)["burned"]
    assert burned and all(world.region_at[p] == "east valley" for p in burned)
    assert disasters.conditions(world, 100 + 360 * 11)["burned"] == frozenset()  # grown back
    world.set_burned(burned)
    assert all(world.terrain[p] == TerrainType.PLAINS for p in burned)
    world.set_burned(frozenset())
    assert all(world.terrain[p] == TerrainType.FOREST for p in burned)


# =================================================================
# Cold, sickness and beasts
# =================================================================


def _valley_ctx(world, agents, tick=0, seed=0):
    ctx = make_ctx(world, agents, tick=tick, seed=seed)
    for a in agents:
        a.position = Position(world.regions[0][1], world.regions[0][2])
    return ctx


def test_a_harsh_winter_hurts_those_who_sleep_alone_and_unsheltered():
    world = world_of(small())
    winter = Disaster(1, "winter", "all", 0, 60)
    alone = make_agent(agent_id=1, name="Alone")
    ctx = _valley_ctx(world, [alone])
    disasters.each_tick(ctx, [winter])
    assert alone.health < 100
    a, b, c = (make_agent(agent_id=i, name=f"P{i}") for i in (2, 3, 4))
    ctx = _valley_ctx(world, [a, b, c])
    disasters.each_tick(ctx, [winter])
    assert a.health == b.health == c.health == 100  # huddled together


def test_sickness_spreads_by_closeness_stops_healing_and_passes():
    world = world_of(small(sickness_spread=1.0, sick_min_days=2, sick_max_days=2))
    sickness = Disaster(1, "sickness", "all", 0, 60)
    a, b = make_agent(agent_id=1, name="A"), make_agent(agent_id=2, name="B")
    ctx = _valley_ctx(world, [a, b])
    disasters.fall_sick(ctx, a, None)
    disasters.each_tick(ctx, [sickness])
    assert b.sick_until is not None
    assert any(e.type == ev.FELL_SICK and e.targets == (a.id,) for e in ctx.log.pending)
    before = b.health
    b.update_needs(world, 1, ctx.log, ctx.rng)  # no natural healing while sick
    assert b.health <= before
    ctx = _valley_ctx(world, [a, b], tick=a.sick_until)
    disasters.each_tick(ctx, [])
    assert a.sick_until is None and a.immune_until > ctx.tick
    assert any(e.type == ev.RECOVERED for e in ctx.log.pending)


def test_tending_the_sick_halves_the_sickness():
    world = world_of(small())
    healer, sick = make_agent(agent_id=1, name="H"), make_agent(agent_id=2, name="S")
    ctx = _valley_ctx(world, [healer, sick])
    sick.sick_until = 100
    from genesis.society import economy

    assert economy.may_heal(ctx, healer, sick)
    economy.heal(ctx, healer, sick)
    assert sick.sick_until == 50


def test_beasts_come_for_people_alone_at_night_and_weapons_drive_them_off():
    world = world_of(small(predator_chance=1.0, armed_drive_off=1.0))
    beasts = Disaster(1, "predators", "all", 0, 60)
    night = 0
    while world.calendar.hour(night) >= 6:  # the first night watch
        night += 1
    alone = make_agent(agent_id=1, name="Alone")
    ctx = _valley_ctx(world, [alone], tick=night)
    disasters.each_tick(ctx, [beasts])
    attack = next(e for e in ctx.log.pending if e.type == ev.BEAST_ATTACK)
    assert not attack.data["driven_off"] and alone.health < 100
    armed = make_agent(agent_id=2, name="Armed")
    armed.bonus = {"reaches": 1}
    ctx = _valley_ctx(world, [armed], tick=night)
    disasters.each_tick(ctx, [beasts])
    assert armed.health == 100
    assert next(e for e in ctx.log.pending if e.type == ev.BEAST_ATTACK).data["driven_off"]


def test_dying_of_a_disaster_has_its_own_cause():
    world = world_of(small())
    weak = make_agent(agent_id=1, name="Weak", health=1)
    ctx = _valley_ctx(world, [weak])
    disasters.harm(ctx, weak, 5, "winter", None)
    assert not weak.alive and weak.cause_of_death == "cold"


# =================================================================
# Leash and migration
# =================================================================


def test_explorers_keep_within_reach_of_water():
    world = world_of(small())
    a = make_agent(agent_id=1, name="A", thirst=0)
    a.memory.remember_location("water", Position(world.regions[0][1], world.regions[0][2]))
    a.position = Position(world.regions[0][1], world.regions[0][2])
    (spots, limit), *_ = leashes(a, world)
    a.thirst = 60
    (_, tighter), *_ = leashes(a, world)
    assert tighter < limit
    target = ExplorationPlanner().choose_target(a, world)
    assert target is None or min(target.distance(p) for p in spots) <= tighter
    assert leashes(a, World_without_leash()) == []


def World_without_leash():
    from helpers import World

    return World()


def test_the_vectorised_search_finds_the_same_tile_as_the_old_one():
    from genesis.mind.memory import AgentMemory

    world = world_of(small())
    memory = AgentMemory()
    memory.remember_seen([p for p in world.walkable if p.x < 20])
    origin = Position(5, 5)
    fast = ExplorationPlanner._nearest_unseen(origin, world, memory)
    slow = min(world.walkable - memory.seen_positions, key=lambda p: (origin.distance(p), p.x, p.y))
    assert fast == slow


def test_the_bold_leave_a_troubled_valley_for_one_they_can_reach():
    world = world_of(small())
    west = Position(world.regions[0][1], world.regions[0][2])
    bold = make_agent(agent_id=1, name="Bold", genes={"risk_tolerance": 0.9}, thirst=0)
    bold.position = west
    bold.inventory["water"] = 2
    timid = make_agent(agent_id=2, name="Timid", genes={"risk_tolerance": 0.1}, thirst=0)
    timid.position = west
    timid.inventory["water"] = 2
    world.troubled = frozenset({"west valley"})
    ctx = make_ctx(world, [bold, timid])
    for a in (bold, timid):
        a.perceive(ctx)
        a.decide(world)
    if bold.current_action == ActionType.MIGRATE:
        assert bold.migrating == "east valley"
        east_lake = [k for k in bold.memory.get_known_locations("water")]
        assert any(world.region_at[k.position] == "east valley" for k in east_lake)
    else:  # the walk through the pass was too long for two drinks: they stayed
        assert bold.migrating is None
    assert timid.current_action != ActionType.MIGRATE


def test_living_long_enough_in_another_valley_is_moving_there():
    sim = Simulation(small(migrate_days=2))
    a = sim.population.agents[0]
    a.home_region = "west valley"
    a.position = Position(sim.world.regions[1][1], sim.world.regions[1][2])
    ctx = make_ctx(sim.world, sim.population.agents)
    for _ in range(2):
        sim._migration(ctx)
    assert a.home_region == "east valley"
    moved = [e for e in ctx.log.pending if e.type == ev.MIGRATED]
    assert moved and moved[0].data == {"from": "west valley", "to": "east valley"}


# =================================================================
# A whole run: disasters, snapshots, resume, experiments, dashboard
# =================================================================


def test_disasters_strike_snapshots_are_kept_and_resume_is_exact(tmp_path):
    config = small(disaster_min_years=0.3, disaster_max_years=0.5)
    year = 4 * 360
    reference = Simulation(config)
    reference.run(2 * year + 8)
    started = [e for e in reference.log.pending if e.type == ev.DISASTER and e.data["started"]]
    assert len(started) >= 3
    assert any(e.actors for e in started)  # the people there lived through it

    path = tmp_path / "m.sqlite"
    sim = Simulation(config)
    store = WorldStore.create(path, sim)
    for _ in range(year + 300):
        sim.tick()
        if sim.tick_count % 4 == 0:
            store.save(sim)
    store.close()
    store = WorldStore(path)
    resumed = store.load()
    store.close()
    resumed.run(2 * year + 8 - resumed.tick_count)
    assert resumed.state() == reference.state()
    assert resumed.world.burned == reference.world.burned
    assert resumed.world.disaster_events == reference.world.disaster_events

    store = WorldStore(path)
    store.save(resumed)
    rows = store.conn.execute("SELECT year, agent_id, data_json FROM yearly").fetchall()
    store.close()
    years = {r[0] for r in rows}
    assert years == {2, 3}
    valleys = json.loads(next(r[2] for r in rows if r[0] == 3 and r[1] == 0))["valleys"]
    assert set(valleys) == {"west valley", "east valley"}

    from fastapi.testclient import TestClient

    from genesis.dashboard.server import create_app

    data = TestClient(create_app(path)).get("/api/history").json()
    assert [y["year"] for y in data["years"]] == [2, 3]
    assert len(data["disasters"]) == len(started)
    assert data["valleys"] == ["west valley", "east valley"]


def test_the_experiment_runner_compares_arms_over_seeds():
    assert experiment.parse_seeds("1-3,7") == [1, 2, 3, 7]
    assert experiment.parse_arm("no trade: trade=0, disaster_min_years=0.5") == (
        "no trade",
        {"trade": 0, "disaster_min_years": 0.5},
    )
    job = {
        "toml": open("worlds/two_valleys.toml").read(),
        "seed": 3,
        "years": 1,
        "arm": "base",
        "rules": {"trade": 0},
        "llm": False,
    }
    result = experiment.run_one(job)
    assert result["years"][0]["alive"] == result["summary"]["alive"] > 0
    summary = experiment._by_arm([result, {**result, "seed": 4}])
    assert summary["base"]["runs"] == 2
