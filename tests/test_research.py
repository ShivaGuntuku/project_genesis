"""S2.3: research in the world. Lore from testing, shared, apprenticed, recorded."""

import dataclasses

from genesis.config import WorldConfig
from genesis.culture import inventions as inv
from genesis.culture import research, tech
from genesis.history import events as ev
from genesis.persistence.db import WorldStore
from genesis.rules import Rules
from genesis.simulation.engine import Simulation
from genesis.world.world import Position
from helpers import World, make_agent, make_ctx

TWO = "worlds/two_valleys.toml"


def world(**more):
    w = World()
    w.rules = dataclasses.replace(Rules(), inventions=2, research=1, **more)
    return w


def test_lore_is_what_the_physics_says_in_plain_words():
    assert research.finding("reed", "soak")["flexible"] > 0
    assert research.words("reed:soak").startswith("soaked reed is more")
    assert research.finding("stone", "twist") == {}  # stone can't be twisted
    assert research.words("stone:twist") == "stone can't be twisted"


def test_a_curious_person_studies_what_they_hold_and_keeps_the_fact():
    w = world()
    a = make_agent(genes={"curiosity": 0.9})
    a.materials = {"reed": 2}
    question = research.plan(a, w, 0)
    assert question[0] == "study" and question[1] == "reed"
    ctx = make_ctx(w, [a])
    event = research.study(ctx, a, question[1], question[2])
    k = research.key(question[1], question[2])
    assert k in a.lore and a.materials == {"reed": 1}
    found = [e for e in ctx.log.recent if e.id == event][0]
    assert found.type == ev.LORE_FOUND and found.data["first"]
    assert research.plan(a, w, 1) is None  # resting a while before the next question
    dull = make_agent(agent_id=2, genes={"curiosity": 0.1})
    dull.materials = {"reed": 1}
    assert research.plan(dull, w, 0) is None


def test_what_one_found_out_guides_the_next_try():
    a = make_agent()
    needs = tech.FUNCTIONS["binds"]["needs"]
    assert research.guided(a, "fibre", "twist", needs) == 0.0
    a.lore.add("fibre:twist")
    assert research.guided(a, "fibre", "twist", needs) > 0
    from genesis.culture.materials import Recipe

    assert research.used(a, Recipe.of(["fibre"], ["twist"])) == ["fibre:twist"]


def test_lore_is_told_in_talk_and_learned_beside_a_parent():
    w = world()
    teacher, friend = make_agent(agent_id=1), make_agent(agent_id=2, x=11)
    teacher.lore = {"reed:soak", "clay:heat"}
    friend.relationship(1, "Test").trust = 1.0
    ctx = make_ctx(w, [teacher, friend], seed=1)
    for _ in range(20):
        research.share(ctx, teacher, friend)
    assert friend.lore == teacher.lore
    parent = make_agent(agent_id=3)
    child = make_agent(agent_id=4, x=11, age=100)
    child.parents = [3]
    parent.lore = {"flint:sharpen"}
    ctx = make_ctx(w, [parent, child], seed=2)
    for _ in range(30):
        research._apprentice(ctx, child)
    assert "flint:sharpen" in child.lore


def test_records_keep_what_the_dead_knew(monkeypatch):
    w = world()
    w.concepts[1] = inv.Concept(1, "Tolo", "marks", ["marks"], [["flint"], ["polish"]], 1, 0)
    scribe, reader = make_agent(agent_id=1), make_agent(agent_id=2, x=11)
    scribe.knowledge.add(1)
    scribe.lore.add("reed:soak")
    scribe.items.append({"concept": 1, "uses": 50, "quality": 0.5})
    inv.refresh_bonus(scribe, w.concepts)
    monkeypatch.setattr(research, "_home", lambda ctx, agent: Position(10, 10))
    ctx = make_ctx(w, [scribe, reader], seed=4)
    for _ in range(40):
        research._record(ctx, scribe)
    rec = research.records_of(w)[0]
    assert rec["concepts"] == [1] and rec["lore"] == ["reed:soak"]
    scribe.alive = False
    inv.check_lost(ctx, None)
    assert w.concepts[1].lost_tick is None  # kept in the marks
    for _ in range(40):
        research._read(ctx, reader)
    assert 1 in reader.knowledge and "reed:soak" in reader.lore


def test_no_name_stem_more_than_twice():
    w = world()
    for cid, name in ((1, "Tolo"), (2, "Toluma")):
        w.concepts[cid] = inv.Concept(cid, name, "cuts", ["cuts"], [[], []], 1, 0)
    assert not inv._fresh_stem("Tolsa", w.concepts)
    assert inv._fresh_stem("Mira", w.concepts)


def test_a_research_world_finds_lore_and_resumes_exactly(tmp_path):
    from genesis.simulation import service

    c = WorldConfig.from_file(TWO)
    config = dataclasses.replace(
        c, rules=dataclasses.replace(c.rules, inventions=2, research=1, items_capacity=4)
    )
    path = tmp_path / "w.sqlite"
    WorldStore.create(path, Simulation(config)).close()
    days = 300
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
    assert research.lore_of(reference.world)  # facts were found out
    assert any(a.lore for a in reference.population.agents)
