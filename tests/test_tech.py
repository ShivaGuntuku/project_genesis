"""S2.2: open-ended technology. 14 materials, 19 uses, made things as materials, tools,
wear and quality; older worlds (inventions = 1) keep Stage 1's physics."""

import dataclasses

import pytest

from genesis.config import WorldConfig
from genesis.culture import inventions as inv
from genesis.culture import tech
from genesis.culture.materials import Recipe
from genesis.history import events as ev
from genesis.persistence.db import WorldStore
from genesis.rules import Rules
from genesis.simulation.engine import Simulation
from helpers import World, make_agent, make_ctx

TWO = "worlds/two_valleys.toml"

# One way to each use, from raw materials (found by a search over 1-3 materials and 1-3
# actions; heating needs fire, splitting a cutting edge).
WAYS = {
    "holds water": (["clay"], ["hollow"]),
    "holds food": (["bark"], ["hollow"]),
    "cuts": (["flint"], ["sharpen"]),
    "reaches": (["fibre", "stone"], ["bind"]),
    "binds": (["fibre"], ["twist"]),
    "shelter": (["fibre", "stone"], ["bind"]),
    "adorns": (["bright stone"], ["smooth"]),
    "pierces": (["fibre", "flint"], ["bind", "sharpen"]),
    "digs": (["fibre", "stone"], ["bind"]),
    "grinds": (["stone"], ["smooth"]),
    "carries loads": (["fibre"], ["weave", "weave"]),
    "floats": (["bark", "reed"], ["bind"]),
    "keeps warm": (["fibre"], ["weave"]),
    "makes fire": (["flint", "resin"], ["bind"]),
    "cooks": (["clay"], ["hollow", "heat"]),
    "lights": (["fibre", "resin"], ["bind"]),
    "stores food": (["bright stone"], ["hollow", "hollow"]),
    "heals": (["clay"], ["grind"]),
    "marks": (["flint"], ["polish"]),
}


def v2_rules(**more):
    return dataclasses.replace(Rules(), inventions=2, **more)


def v2_world(**more):
    w = World()
    w.rules = v2_rules(**more)
    return w


def concept(cid, function, recipe, functions=None, props=None):
    return inv.Concept(
        cid,
        f"Name{cid}",
        function,
        functions or tech.functions(Recipe.of(*recipe)),
        [sorted(recipe[0]), list(recipe[1])],
        1,
        0,
        props=props,
    )


def test_every_use_can_be_made_and_none_comes_free():
    assert len(tech.FUNCTIONS) == 19 and len(tech.MATERIALS) == 14 and len(tech.PROPERTIES) == 16
    for material in tech.MATERIALS:
        assert tech.functions(Recipe.of([material], [])) == [], material
    for use, (mats, acts) in WAYS.items():
        assert use in tech.functions(Recipe.of(mats, acts)), use
    assert set(WAYS) == set(tech.FUNCTIONS) == set(tech.SOLVES.values())
    for problem in tech.SOLVES:  # every trouble can be put in words
        assert problem in inv.NEED_WORDS


def test_stage_1_lookups_are_unchanged_for_older_worlds():
    from genesis.culture import materials

    for problem, use in materials.SOLVES.items():
        assert tech.SOLVES[problem] == use
    assert inv.physics(Rules(inventions=1)) is materials
    assert inv.physics(v2_rules()) is tech


def test_parts_are_long_only_when_bound_into_one_thing():
    loose, _ = tech.properties(Recipe.of(["fibre", "stone"], ["smooth"]))
    tied, bound = tech.properties(Recipe.of(["fibre", "stone"], ["bind"]))
    assert bound and tied["long"] == 0.8 and loose["long"] == pytest.approx(0.5)
    assert "reaches" not in tech.functions(Recipe.of(["fibre", "stone"], ["smooth"]))


def test_heating_needs_fire_and_splitting_a_cutting_edge():
    assert tech.tools_needed(Recipe.of(["clay"], ["hollow", "heat"])) == ["makes fire"]
    assert tech.tools_needed(Recipe.of(["wood"], ["split"])) == ["cuts"]
    world = v2_world()
    a = make_agent()
    pot = concept(1, "cooks", WAYS["cooks"])
    fire = concept(2, "makes fire", WAYS["makes fire"])
    world.concepts.update({1: pot, 2: fire})
    a.knowledge |= {1, 2}
    a.materials = {"clay": 1}
    assert not inv.can_make(a, pot, world.concepts)
    assert inv.next_step(a, pot, world.concepts) is None  # no fire, no pot
    a.items.append({"concept": 2, "uses": 5, "quality": 0.5})
    assert inv.can_make(a, pot, world.concepts)
    assert inv.next_step(a, pot, world.concepts) == ("make", pot)


def test_made_stuff_goes_into_other_recipes_and_the_chain_is_followed():
    world = v2_world()
    cord_recipe = Recipe.of(["fibre"], ["twist"])
    assert tech.novel(cord_recipe)  # much stronger than fibre: a new kind of stuff
    cord = concept(1, inv.MATERIAL, WAYS["binds"], props=tech.properties(cord_recipe)[0])
    world.concepts[1] = cord
    made = inv.made_props(world.concepts)
    bag_recipe = Recipe.of(["#1"], ["weave", "weave"])
    assert "carries loads" in tech.functions(bag_recipe, made)
    bag = concept(
        2, "carries loads", (["#1"], ["weave", "weave"]), tech.functions(bag_recipe, made)
    )
    world.concepts[2] = bag
    a = make_agent()
    a.knowledge |= {1, 2}
    assert inv.next_step(a, bag, world.concepts) == ("gather", "fibre")  # for the cord first
    a.materials = {"fibre": 1}
    assert inv.next_step(a, bag, world.concepts) == ("make", cord)
    ctx = make_ctx(world, [a])
    inv.make(ctx, a, cord)
    assert a.materials == {"#1": 1} and inv.material_name("#1", world.concepts) == "Name1"
    inv.make(ctx, a, bag)
    assert a.materials == {} and a.items[0]["concept"] == 2
    # the bag is woven cord: it carries loads (+4) and ties bundles too (+2)
    assert inv.materials_room(a, world.rules) == world.rules.materials_capacity + 6
    from genesis.history.targets import _depth

    assert _depth({"Name1": [["fibre"], ["twist"]], "Name2": [["Name1"], ["weave"]]}) == 2


def test_a_thing_wears_breaks_and_the_trouble_comes_back():
    world = v2_world()
    knife = concept(1, "cuts", WAYS["cuts"])
    world.concepts[1] = knife
    a = make_agent()
    a.knowledge.add(1)
    ctx = make_ctx(world, [a])
    a.items.append({"concept": 1, "uses": 2, "quality": 0.5})
    inv.refresh_bonus(a, world.concepts)
    inv.wear(ctx, a, "cuts")
    assert a.items and a.bonus["cuts"]
    inv.wear(ctx, a, "cuts")
    assert not a.items and not a.bonus["cuts"]
    assert a.problems["gather food"] >= world.rules.problem_threshold  # make another
    assert [e.type for e in ctx.log.recent][-1] == ev.ITEM_BROKE


def test_skill_and_good_materials_make_things_last_longer():
    world = v2_world()
    knife = concept(1, "cuts", WAYS["cuts"])
    world.concepts[1] = knife
    novice, master = make_agent(agent_id=1), make_agent(agent_id=2)
    master.abilities.values["crafting"] = 1.0
    ctx = make_ctx(world, [novice, master])
    assert inv.new_item(ctx, master, knife)["uses"] > inv.new_item(ctx, novice, knife)["uses"]
    old = World()
    old.rules = dataclasses.replace(Rules(), inventions=1)
    assert inv.new_item(make_ctx(old, [novice]), novice, knife) == {"concept": 1, "uses": 120}


def test_food_spoils_unless_stored_well():
    world = v2_world(food_spoils_per_day=0.5)
    pot = concept(1, "stores food", WAYS["stores food"])
    world.concepts[1] = pot
    a, b = make_agent(agent_id=1), make_agent(agent_id=2)
    for x in (a, b):
        x.inventory["food"] = 40
    b.items.append({"concept": 1, "uses": 50, "quality": 0.5})
    inv.refresh_bonus(b, world.concepts)
    ctx = make_ctx(world, [a, b])
    inv.daily_wear(ctx, a)
    inv.daily_wear(ctx, b)
    assert a.inventory["food"] == 20 and a.problems["food spoils"] == 0.12
    assert b.inventory["food"] == 40 and b.items[0]["uses"] < 50  # the pot wears instead


def test_things_have_their_effects():
    world = v2_world()
    for cid, use in enumerate(("keeps warm", "lights", "grinds", "pierces"), start=1):
        world.concepts[cid] = concept(cid, use, WAYS[use])
    a = make_agent()
    plain = make_agent(agent_id=2)
    base_vision = a.vision_radius(world)
    base_food = a._nourishment(world.rules)
    for cid in world.concepts:
        a.items.append({"concept": cid, "uses": 50, "quality": 0.5})
    inv.refresh_bonus(a, world.concepts)
    assert a.vision_radius(world) == base_vision + 1
    assert a.bonus["cuts"]  # the spear cuts too
    assert a._nourishment(world.rules) == pytest.approx(base_food * 1.2 * 1.1)
    from genesis.world import disasters

    assert disasters._cold(a) == pytest.approx(0.3) and disasters._cold(plain) == 1.0
    assert a.bonus["pierces"] == 1


def test_a_new_world_invents_with_the_new_physics_and_resumes_exactly(tmp_path):
    from genesis.simulation import service

    c = WorldConfig.from_file(TWO)
    config = dataclasses.replace(
        c, rules=dataclasses.replace(c.rules, inventions=2, food_spoils_per_day=0.1)
    )
    path = tmp_path / "w.sqlite"
    WorldStore.create(path, Simulation(config)).close()
    days = 400
    service.run(path, 4 * days, llm=False, snapshot_minutes=0, echo=lambda *_: None)
    reference = Simulation(config)
    reference.run(4 * days)
    store = WorldStore(path)
    resumed = store.load()
    store.close()
    assert resumed.state() == reference.state()
    resumed.run(4 * 30)
    reference.run(4 * 30)
    assert resumed.state() == reference.state()
    assert reference.concepts  # they invent with the new physics
    for c in reference.concepts.values():
        assert c.function in tech.FUNCTIONS or c.function == inv.MATERIAL


def test_looking_for_one_thing_finds_it_or_nothing_and_tries_are_aimed():
    sim = Simulation(
        dataclasses.replace(
            WorldConfig.from_file(TWO),
            rules=dataclasses.replace(WorldConfig.from_file(TWO).rules, inventions=2),
        )
    )
    w = sim.world
    mountain = next((p for p in sorted(w.walkable) if inv.terrain_of(w, p) == "mountain"), None)
    if mountain is None:
        pytest.skip("no mountain here")
    a = sim.population.get_alive()[0]
    a.position, a.materials = mountain, {}
    a.project = {"material": "flint"}
    ctx = make_ctx(w, [a], seed=3)
    found = [inv.gather(ctx, a) for _ in range(40)]
    assert set(found) <= {"flint", None} and "flint" in found  # never stone instead
    mats, acts = inv._directed(
        ctx,
        a,
        "pierces",
        ["fibre", "flint", "wood", "clay"],
        ["bind", "sharpen", "smooth", "hollow"],
        {},
    )
    assert set(mats) <= {"fibre", "flint", "wood", "clay"}
    if "bind" in acts:
        assert acts[0] == "bind"  # tied first, then worked


def test_stuff_never_counts_as_the_finished_thing():
    world = v2_world()
    cord_recipe = Recipe.of(["fibre"], ["twist"])
    world.concepts[1] = concept(
        1, inv.MATERIAL, WAYS["binds"], props=tech.properties(cord_recipe)[0]
    )
    a = make_agent()
    a.knowledge.add(1)
    assert "binds" in world.concepts[1].functions
    assert inv.known_for(a, world.concepts, "binds") == []  # else made again and again


def test_a_better_or_new_mix_is_a_new_idea_and_the_same_again_is_not():
    world = v2_world()
    world.concepts[1] = concept(1, "cuts", (["flint"], ["sharpen"]))
    same = Recipe.of(["flint"], ["sharpen"])
    assert inv._kin(world.concepts, "cuts", tech.functions(same), same) == (world.concepts[1], None)
    better = Recipe.of(["obsidian"], ["sharpen", "sharpen"])  # a finer edge
    assert tech.fit(better, "cuts") >= tech.fit(same, "cuts") + inv.BETTER
    assert inv._kin(world.concepts, "cuts", tech.functions(better), better) == (None, 1)
    both = Recipe.of(["flint"], ["polish", "sharpen"])  # cuts and marks: a new mix
    assert set(tech.functions(both)) - set(world.concepts[1].functions)
    assert inv._kin(world.concepts, "cuts", tech.functions(both), both)[1] == 1


def test_a_full_kit_leaves_the_least_useful_thing_and_thinks_of_two_jobs_in_one():
    world = v2_world(items_capacity=2)
    for cid, use in enumerate(("cuts", "keeps warm", "lights"), start=1):
        world.concepts[cid] = concept(cid, use, WAYS[use])
    world.concepts[4] = concept(4, "cuts", WAYS["cuts"])  # a second cutter
    a = make_agent()
    inv.add_item(a, {"concept": 1, "uses": 9, "quality": 0.5}, world.concepts, world.rules)
    inv.add_item(a, {"concept": 4, "uses": 50, "quality": 0.5}, world.concepts, world.rules)
    inv.add_item(a, {"concept": 2, "uses": 9, "quality": 0.5}, world.concepts, world.rules)
    assert sorted(i["concept"] for i in a.items) == [2, 4]  # the worn spare cutter went
    assert a.asked["combine"] == "cuts"
    inv.add_item(a, {"concept": 3, "uses": 9, "quality": 0.5}, world.concepts, world.rules)
    assert len(a.items) == 2 and a.bonus.get("lights")


def test_what_one_has_makes_one_want_what_it_leads_to():
    for use, nexts in tech.LEADS_TO.items():
        assert use in tech.FUNCTIONS and nexts and set(nexts) <= set(tech.FUNCTIONS)
    assert set(tech.LEADS_TO) == set(tech.FUNCTIONS)
    world = v2_world()
    world.concepts[1] = concept(1, "makes fire", WAYS["makes fire"])
    a = make_agent()
    a.items.append({"concept": 1, "uses": 50, "quality": 0.5})
    inv.refresh_bonus(a, world.concepts)
    inv.daily_wear(make_ctx(world, [a]), a)
    assert a.problems["raw food"] > 0 and a.problems["dark"] > 0  # cooking, lights next


def test_an_older_world_file_gets_the_parent_column(tmp_path):
    import sqlite3

    from genesis.persistence.db import SCHEMA_VERSION

    path = tmp_path / "w.sqlite"
    WorldStore.create(path, Simulation(WorldConfig.from_file(TWO))).close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE concepts DROP COLUMN parent")
    conn.execute("UPDATE meta SET value = '11' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    store = WorldStore(path)
    store.load()
    assert int(store.meta()["schema_version"]) == SCHEMA_VERSION == 12
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(concepts)")}
    store.close()
    assert "parent" in cols


def test_a_swap_between_full_kits_completes_before_anything_is_left_behind():
    from genesis.society import economy

    world = v2_world(items_capacity=1)
    world.concepts[1] = concept(1, "cuts", WAYS["cuts"])
    world.concepts[2] = concept(2, "keeps warm", WAYS["keeps warm"])
    a, b = make_agent(agent_id=1), make_agent(agent_id=2, x=11)
    a.items.append({"concept": 1, "uses": 9, "quality": 0.5})
    b.items.append({"concept": 2, "uses": 9, "quality": 0.5})
    ctx = make_ctx(world, [a, b])
    economy.move(a, b, "item:1", 1, ctx)  # b holds two for a moment ...
    economy.move(b, a, "item:2", 1, ctx)  # ... and can still hand theirs over
    for x in (a, b):
        inv.trim_items(x, world.concepts, world.rules)
    assert [i["concept"] for i in a.items] == [2] and [i["concept"] for i in b.items] == [1]
