"""S2.4: culture beyond tools. Shared beliefs, values, stories, customs and norms."""

import dataclasses

from genesis.config import WorldConfig
from genesis.culture import ideas
from genesis.history import events as ev
from genesis.persistence.db import WorldStore
from genesis.rules import Rules
from genesis.simulation.engine import Simulation
from genesis.society.groups import Group
from helpers import World, make_agent, make_ctx

TWO = "worlds/two_valleys.toml"


def world(**more):
    w = World()
    w.rules = dataclasses.replace(Rules(), culture=1, **more)
    return w


def test_a_belief_joins_the_valley_held_again_or_as_a_variant():
    w = world()
    a, b, c = make_agent(agent_id=1), make_agent(agent_id=2), make_agent(agent_id=3)
    ctx = make_ctx(w, [a, b, c])
    ideas.from_belief(ctx, a, "Sharing food with strangers brings trust and warmth", 1)
    (first,) = ideas.ideas(w).values()
    assert first["kind"] == "belief" and a.ideas == {first["id"]: 0.7}
    ideas.from_belief(ctx, b, "Sharing food with strangers brings trust and warmth to all", 2)
    assert len(ideas.ideas(w)) == 1 and first["id"] in b.ideas  # the same idea, held again
    ideas.from_belief(ctx, c, "Sharing food brings trust, but we must guard the lake", 3)
    variant = ideas.ideas(w)[2]
    assert variant["kind"] == "value" and variant["parents"] == [first["id"]]  # grew from it


def test_ideas_spread_with_trust_fade_unheard_and_die_with_their_last_holder():
    w = world()
    a, b = make_agent(agent_id=1), make_agent(agent_id=2, x=11)
    ctx = make_ctx(w, [a, b], seed=5)
    iid = ideas.new_idea(ctx, "belief", "The lake remembers those who thank it", 1)
    ideas.hold(a, iid, 0.9)
    b.relationship(1, "Test").trust = 1.0
    for _ in range(10):
        ideas.share(ctx, a, b)
    assert iid in b.ideas
    a.alive = b.alive = False
    ideas.daily(ctx, None)
    assert ideas.ideas(w)[iid]["lost"] == ctx.tick
    assert ev.IDEA_LOST in [e.type for e in ctx.log.recent]


def test_children_take_their_parents_ideas_and_widely_held_ones_get_a_word():
    w = world()
    parent = make_agent(agent_id=1)
    kids = [make_agent(agent_id=i, age=100) for i in (2, 3)]
    for k in kids:
        k.parents, k.stage = [1], "child"
    ctx = make_ctx(w, [parent, *kids], seed=1)
    iid = ideas.new_idea(ctx, "story", "The great drought of year 3", 1)
    ideas.hold(parent, iid, 1.0)
    for day in range(40):
        ctx.tick = day
        ideas.daily(ctx, None)
    assert all(iid in k.ideas for k in kids)
    assert ideas.ideas(w)[iid]["name"]  # three hold it: a word of the valley
    lines = ideas.prompt_lines(kids[0], w)
    assert ideas.ideas(w)[iid]["name"] in lines[1] and "your people's words" in lines[-1]


def test_a_group_that_shares_gets_a_norm_its_members_follow_or_break():
    w = world()
    people = [make_agent(agent_id=i, x=10 + i) for i in (1, 2, 3)]
    ctx = make_ctx(w, people, tick=100)
    ctx.groups[1] = Group(1, "Kor", 1, 1, [1, 2, 3], (10, 10), 0)
    ideas.did(ctx, people[0], "share")
    ideas.did(ctx, people[1], "share")
    ideas.daily(ctx, None)
    norm = next(i for i in ideas.ideas(w).values() if i["kind"] == "norm")
    assert all(norm["id"] in p.ideas for p in people)
    assert ideas.sharing_norm(people[2], w) > 0
    before = people[2].ideas[norm["id"]]
    ideas.broke_norm(ctx, people[2], people[0], "food")
    assert people[2].ideas[norm["id"]] < before
    assert ev.NORM_BROKEN in [e.type for e in ctx.log.recent]


def test_deaths_become_stories_and_mourning_together_a_custom_that_softens_grief():
    from genesis.society import family

    w = world()
    dead = make_agent(agent_id=1)
    mourners = [make_agent(agent_id=i) for i in (2, 3, 4)]
    ctx = make_ctx(w, [dead, *mourners])
    for _ in range(ideas.MOURNINGS):
        died = ctx.log.emit(0, ev.DIED, actors=(1,))
        event = next(e for e in ctx.log.recent if e.id == died)
        ideas.on_death(ctx, dead, event, mourners)
    kinds = sorted(i["kind"] for i in ideas.ideas(w).values())
    assert kinds.count("story") == ideas.MOURNINGS and "custom" in kinds  # a founder's tale
    assert ideas.mourns_together(mourners[0], w)
    for m in mourners:
        m.parents = [1]
        m.energy = 100
    family.grieve(ctx, event)
    assert mourners[0].energy == 100 - w.rules.grief_energy_loss * 0.5


def test_a_culture_world_grows_ideas_and_resumes_exactly(tmp_path):
    from genesis.simulation import service

    c = WorldConfig.from_file(TWO)
    rules = dataclasses.replace(c.rules, inventions=2, research=1, culture=1, items_capacity=4)
    config = dataclasses.replace(c, rules=rules)
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
    assert ideas.ideas(reference.world)  # stories, customs or norms grew without the LLM
