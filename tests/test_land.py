"""S2.1: a living land. Woods, berry patches, lakes and paths change; resume stays exact."""

import dataclasses

import pytest

from genesis.config import WorldConfig
from genesis.history import events as ev
from genesis.persistence.db import WorldStore
from genesis.simulation import service
from genesis.simulation.engine import Simulation
from genesis.world.land import Land, climate
from genesis.world.world import Position, TerrainType, World

TWO = "worlds/two_valleys.toml"


def config(**rules):
    c = WorldConfig.from_file(TWO)
    settings = {"living_land": 1, "land_every_days": 10, **rules}
    return dataclasses.replace(c, rules=dataclasses.replace(c.rules, **settings))


def derived(world):
    """What `change_tiles` keeps up to date, as a full rebuild would work it out."""
    return (world.walkable, world.adjacent, world.shore, world.forest)


def test_the_land_is_off_unless_asked_for():
    assert Simulation(WorldConfig.from_file(TWO)).world.land is None
    assert Simulation(config()).world.land is not None


def test_climate_drifts_slowly_and_comes_from_the_seed():
    a = [climate(11, m) for m in range(0, 1200, 12)]
    assert a == [climate(11, m) for m in range(0, 1200, 12)]
    assert a != [climate(12, m) for m in range(0, 1200, 12)]
    assert all(-1 <= c <= 1 for c in a)
    assert max(abs(x - y) for x, y in zip(a, a[1:], strict=False)) < 0.2  # slow
    assert max(a) - min(a) > 0.3  # but it moves within a century


def test_a_saved_and_resumed_world_runs_exactly_like_an_unbroken_one(tmp_path):
    path = tmp_path / "w.sqlite"
    WorldStore.create(path, Simulation(config())).close()
    service.run(path, 4 * 300, llm=False, snapshot_minutes=0, echo=lambda *_: None)
    reference = Simulation(config())
    reference.run(4 * 300)
    store = WorldStore(path)
    resumed = store.load()
    store.close()
    assert resumed.world.land.month == reference.world.land.month == 30
    assert resumed.state() == reference.state()
    resumed.run(4 * 60)
    reference.run(4 * 60)
    assert resumed.state() == reference.state()
    assert derived(resumed.world) == derived(reference.world)


def test_lakes_swing_with_the_seasons_and_keep_their_numbers():
    sim = Simulation(config())
    basins = set(sim.world.lake_of.values())
    sim.run(4 * 360)
    year = next(d for _, a, d in sim.pending_yearly if a == -1)
    assert year["water_max"] > year["water_min"]  # wet season high, dry season low
    assert set(sim.world.lake_of.values()) <= basins | set(sim.world.land.ponds.values())
    rebuilt = World(sim.config)  # the generated map, then the same tiles: same derived data
    rebuilt.change_tiles({p: (t, sim.world.lake_of.get(p)) for p, t in sim.world.terrain.items()})
    assert derived(rebuilt) == derived(sim.world)


def test_nothing_floods_under_people_patches_or_a_group_home():
    sim = Simulation(config())
    sim.run(4 * 400)
    w = sim.world
    for a in sim.population.get_alive():
        assert w.is_walkable(a.position)
    for f in w.food_sources:
        assert w.is_walkable(f.position)
    for g in sim.groups.values():
        assert w.is_walkable(Position(*g.home))


def test_cut_woods_lie_bare_then_turn_to_grass():
    sim = Simulation(config(bare_months=2, woods_spread=0, woods_dieback=0))
    land = sim.world.land
    tree = min(sim.world.forest, key=lambda p: (p.x, p.y))
    for _ in range(sim.config.rules.wood_per_tree):
        land.cut_wood(tree)
    land.step(sim, sim.tick_count)
    assert sim.world.terrain[tree] == TerrainType.PLAINS and tree in land.bare
    assert tree not in sim.world.forest
    land.step(sim, sim.tick_count)
    land.step(sim, sim.tick_count)
    assert tree not in land.bare  # grass again


def test_woods_spread_into_grass_beside_them():
    sim = Simulation(config(woods_spread=1, woods_dieback=0))
    before = len(sim.world.forest)
    sim.world.land.step(sim, sim.tick_count)
    assert len(sim.world.forest) > before


def test_much_walked_grass_becomes_a_path_and_paths_are_faster():
    sim = Simulation(config())
    land, w = sim.world.land, sim.world
    a = sim.population.get_alive()[0]
    row = [Position(a.position.x + i, a.position.y) for i in range(4)]
    if not all(w.terrain.get(p) == TerrainType.PLAINS for p in row):
        pytest.skip("no straight grass here")
    for p in row:
        land.walks[p] = sim.config.rules.path_steps
    land.step(sim, sim.tick_count)
    assert all(p in land.paths for p in row)
    a.route = None
    assert a.step_towards(row[3], w)
    assert a.position == row[2]  # two tiles in one step along the path


def test_a_patch_stripped_bare_month_after_month_dies(tmp_path):
    sim = Simulation(config(patch_strip_months=2, patch_old_age=0, patch_seed=0, patch_spawn=0))
    w, land = sim.world, sim.world.land
    active = [f for f in w.food_sources if f.stage <= w.peak_population]
    victim = active[0]
    ticks = sim.config.rules.land_every_days * w.calendar.ticks_per_day
    events = []
    for _ in range(2):
        land.low[victim.id] = ticks
        events += land.step(sim, sim.tick_count)
    assert victim not in w.food_sources and victim.id in land.dead
    assert ("patch_died", victim.position) in [(k, p) for k, p, _ in events]


def test_healthy_patches_seed_new_ones_that_survive_a_resume(tmp_path):
    sim = Simulation(config(patch_seed=1, patch_spawn=1))
    w = sim.world
    before = {f.id for f in w.food_sources}
    events = sim.world.land.step(sim, sim.tick_count)
    born = {f.id for f in w.food_sources} - before
    assert born and any(k == "patch_born" for k, _, _ in events)
    for fid in born:
        assert w.food_region[fid] in w.region_names
    path = tmp_path / "w.sqlite"
    store = WorldStore.create(path, sim)
    store.save(sim)
    loaded = store.load()
    store.close()
    assert {f.id for f in loaded.world.food_sources} == {f.id for f in w.food_sources}
    assert loaded.state() == sim.state()


def test_land_events_are_logged_and_described():
    from genesis.history.describe import describe

    sim = Simulation(config(patch_seed=1, patch_spawn=1, land_every_days=1))
    sim.run(8)
    found = [e for e in sim.log.recent if e.type == ev.LAND_CHANGED]
    assert found and "berry patch" in describe(found[0], {})


def test_a_year_of_land_is_kept_for_the_history_page():
    sim = Simulation(config())
    sim.run(4 * 361)
    rows = [d for y, a, d in sim.pending_yearly if a == -1]
    assert rows and {"woods", "water", "water_min", "water_max", "patches", "valleys"} <= set(
        rows[0]
    )


def test_the_viewer_and_dashboard_show_the_land_as_it_is(tmp_path):
    from fastapi.testclient import TestClient

    from genesis.api.views import land_info
    from genesis.dashboard.server import create_app

    sim = Simulation(config(woods_spread=1))
    sim.world.land.step(sim, sim.tick_count)
    path = tmp_path / "w.sqlite"
    store = WorldStore.create(path, sim)
    store.save(sim)
    store.close()
    tiles = land_info(sim.world)["tiles"]
    shown = TestClient(create_app(path)).get("/api/map").json()
    assert shown["tiles"] == tiles
    assert Land  # the class is importable for tools
