"""M8b: materials, the hidden physics, inventing, making, teaching and losing knowledge."""

import json
from dataclasses import replace

from fakes import FakeModel
from genesis.config import WorldConfig
from genesis.culture import inventions as inv
from genesis.culture import materials as phys
from genesis.culture.words import is_valley_word, leaks
from genesis.history import events as ev
from genesis.llm.mind import ReplayMind, attach
from genesis.llm.settings import LLMConfig
from genesis.mind.decision import ActionType
from genesis.persistence.db import WorldStore
from genesis.rules import Rules
from genesis.simulation.engine import Simulation
from helpers import World, make_agent, make_ctx

MILLENNIUM = "worlds/two_valleys.toml"


def millennium(**rules) -> WorldConfig:
    config = WorldConfig.from_file(MILLENNIUM)
    return config.with_overrides(rules=replace(config.rules, **rules)) if rules else config


def invention_world(**rules):
    return World(rules=replace(Rules(), inventions=1, **rules))


class IdeaMind:
    """A stand-in mind that always has the same idea and name (to test without chance)."""

    active = True

    def __init__(self, materials=("clay",), actions=("hollow",), name="Vrosk"):
        self.materials, self.actions, self.name = list(materials), list(actions), name

    def ask(self, ctx, point, question):
        if question.kind == "invent":
            text = {
                "thought": "",
                "idea": "a hollow lump",
                "materials": self.materials,
                "actions": self.actions,
            }
        else:
            text = {"thought": "", "name": self.name}
        return question.parse(json.dumps(text), None)


# =================================================================
# The hidden physics
# =================================================================


def test_what_a_thing_can_do_comes_from_its_materials_and_working():
    assert phys.functions(phys.Recipe.of(["clay"], ["hollow"])) == ["holds water", "holds food"]
    assert phys.functions(phys.Recipe.of(["clay"], [])) == []  # an unworked lump holds nothing
    spear = phys.functions(phys.Recipe.of(["wood", "flint"], ["sharpen"]))
    assert "cuts" in spear and "reaches" in spear
    assert phys.functions(phys.Recipe.of(["fibre"], ["sharpen"])) == ["binds"]  # too soft
    # A shelter has to be tied together, with something that binds.
    assert "shelter" not in phys.functions(phys.Recipe.of(["wood", "stone"], []))
    assert "shelter" in phys.functions(phys.Recipe.of(["wood", "stone", "fibre"], ["tie"]))
    assert "adorns" in phys.functions(phys.Recipe.of(["shell"], []))


def test_names_must_be_new_words_of_the_valley():
    assert is_valley_word("Vrosk", set())
    assert not is_valley_word("water", set())  # an English word
    assert not is_valley_word("london", set())  # a name from our world
    assert not is_valley_word("Vrosk", {"vrosk"})  # already taken
    assert not is_valley_word("Qa", set()) and not is_valley_word("Vr0sk", set())
    assert leaks("a bronze bowl") and not leaks("a hollow lump to keep water")


# =================================================================
# Problems, tinkering, inventing
# =================================================================


def test_trouble_that_keeps_happening_becomes_a_problem_and_fades():
    world = invention_world()
    agent = make_agent(agent_id=1, name="A", thirst=70)  # thirsty, no lake known nearby
    ctx = make_ctx(world, [agent])
    for _ in range(6):
        inv.notice_problems(ctx, agent)
    assert inv.main_problem(agent, world.rules) == "carry water"
    for _ in range(100):
        inv.fade_problems(agent)
    assert inv.main_problem(agent, world.rules) is None


def test_an_idea_that_works_becomes_a_named_concept_and_a_thing_in_hand():
    world = invention_world(invent_base_chance=1.0)
    maker = make_agent(agent_id=1, name="Maker")
    maker.materials = {"clay": 2, "reed": 1}
    ctx = make_ctx(world, [maker])
    ctx.mind = IdeaMind()
    event_id = inv.experiment(ctx, maker, "carry water")
    event = next(e for e in ctx.log.pending if e.id == event_id)
    assert event.type == ev.INVENTED
    concept = world.concepts[event.data["concept"]]
    assert concept.name == "Vrosk" and concept.function == "holds water"
    assert concept.inventor == maker.id and concept.id in maker.knowledge
    assert maker.items and maker.bonus.get("water", 0) > 0  # carries more water now
    assert maker.materials == {"clay": 1, "reed": 1}  # the try used up the clay
    assert maker.problems.get("carry water", 0) == 0


def test_an_idea_that_cannot_work_is_only_a_failed_try():
    world = invention_world(invent_base_chance=1.0)
    maker = make_agent(agent_id=1, name="Maker")
    maker.materials = {"fibre": 1}
    ctx = make_ctx(world, [maker])
    ctx.mind = IdeaMind(materials=["fibre"], actions=["sharpen"])  # whatever they imagine
    event_id = inv.experiment(ctx, maker, "carry water")
    event = next(e for e in ctx.log.pending if e.id == event_id)
    assert event.type == ev.EXPERIMENTED and not world.concepts and not maker.knowledge
    assert maker.materials == {}


def test_a_bad_name_from_the_mind_falls_back_to_a_made_up_word():
    world = invention_world(invent_base_chance=1.0)
    maker = make_agent(agent_id=1, name="Maker")
    maker.materials = {"clay": 1}
    ctx = make_ctx(world, [maker])
    ctx.mind = IdeaMind(name="Bowl")  # an English word: not allowed
    inv.experiment(ctx, maker, "carry water")
    (concept,) = world.concepts.values()
    assert concept.name != "Bowl" and is_valley_word(concept.name.lower(), set())


def test_curious_people_gather_and_tinker_with_their_problem():
    world = invention_world()
    agent = make_agent(agent_id=1, name="A", genes={"curiosity": 0.9})
    agent.problems = {"carry food": 10.0}
    ctx = make_ctx(world, [agent])
    agent.perceive(ctx)
    agent.decide(world)
    assert agent.current_action == ActionType.GATHER
    agent.materials = {"clay": 1, "reed": 1, "fibre": 1}
    agent.decide(world)
    assert agent.current_action == ActionType.EXPERIMENT

    dull = make_agent(agent_id=2, name="B", genes={"curiosity": 0.1})
    dull.problems = {"carry food": 10.0}
    dull.materials = {"clay": 1, "reed": 1, "fibre": 1}
    dull.perceive(make_ctx(world, [dull]))
    dull.decide(world)
    assert dull.current_action not in (ActionType.GATHER, ActionType.EXPERIMENT)


def test_seeing_a_neighbour_with_the_fix_means_waiting_to_learn_not_reinventing():
    world = invention_world(invent_base_chance=1.0)
    maker = make_agent(agent_id=1, name="Maker")
    maker.materials = {"clay": 1}
    neighbour = make_agent(x=11, agent_id=2, name="Near", genes={"curiosity": 0.9})
    neighbour.problems = {"carry food": 10.0}
    neighbour.materials = {"clay": 1, "reed": 1, "fibre": 1}
    ctx = make_ctx(world, [maker, neighbour])
    ctx.mind = IdeaMind()
    inv.experiment(ctx, maker, "carry food")
    inv.notice_problems(ctx, neighbour)
    assert "seen:holds food" in neighbour.asked
    neighbour.perceive(ctx)
    neighbour.decide(world)
    assert neighbour.current_action != ActionType.EXPERIMENT
    # And the teacher shows them what they need first.
    (concept,) = inv.teachable(maker, neighbour, world.concepts)
    assert inv.wants_to_learn(neighbour, concept, world.rules) == 1.0


# =================================================================
# Making, teaching, losing
# =================================================================


def _concept(world, maker, function="holds water", recipe=(("clay",), ("hollow",))):
    ctx = make_ctx(world, [maker])
    ctx.mind = IdeaMind(materials=list(recipe[0]), actions=list(recipe[1]))
    maker.materials = {m: 1 for m in recipe[0]}
    inv.experiment(ctx, maker, next(p for p, f in phys.SOLVES.items() if f == function))
    return next(iter(world.concepts.values()))


def test_whoever_knows_how_can_make_it_again_from_the_right_materials():
    world = invention_world(invent_base_chance=1.0)
    maker = make_agent(agent_id=1, name="Maker")
    concept = _concept(world, maker)
    maker.items.clear()
    assert inv.missing_material(maker, concept) == "clay" and not inv.can_make(maker, concept)
    maker.materials = {"clay": 1}
    ctx = make_ctx(world, [maker])
    event_id = inv.make(ctx, maker, concept)
    assert event_id is not None and len(maker.items) == 1 and not maker.materials
    capacity_with = maker.carry_capacity("water", world.rules)
    maker.items.clear()
    inv.refresh_bonus(maker, world.concepts)
    assert capacity_with > maker.carry_capacity("water", world.rules)


def test_a_shelter_is_built_where_it_is_made_and_restores_more_sleep():
    world = invention_world(invent_base_chance=1.0)
    builder = make_agent(agent_id=1, name="Builder")
    _concept(world, builder, "shelter", (("wood", "stone", "fibre"), ("tie",)))
    (structure,) = world.structures
    assert (structure["x"], structure["y"]) == (10, 10) and structure["owner"] == builder.id
    assert inv.sheltered(builder, world.structures)
    builder.position = builder.position.__class__(15, 15)
    assert not inv.sheltered(builder, world.structures)


def test_teaching_spreads_knowledge():
    world = invention_world(invent_base_chance=1.0)
    teacher = make_agent(agent_id=1, name="Teacher")
    concept = _concept(world, teacher)
    teacher.abilities.values["teaching"] = 1.0
    learner = make_agent(x=11, agent_id=2, name="Learner")
    learned = False
    for seed in range(10):
        ctx = make_ctx(world, [teacher, learner], seed=seed)
        inv.teach(ctx, teacher, learner, concept)
        if concept.id in learner.knowledge:
            learned = True
            break
    assert learned
    types = [e.type for e in ctx.log.pending]
    assert ev.MESSAGE in types and ev.LEARNED in types
    assert not inv.teachable(teacher, learner, world.concepts)  # nothing more to show


def test_knowledge_nobody_alive_holds_is_lost():
    world = invention_world(invent_base_chance=1.0)
    maker = make_agent(agent_id=1, name="Maker")
    concept = _concept(world, maker)
    other = make_agent(agent_id=2, name="Other")
    ctx = make_ctx(world, [maker, other])
    inv.check_lost(ctx, None)
    assert concept.lost_tick is None
    maker.alive = False
    inv.check_lost(ctx, None)
    assert concept.lost_tick == ctx.tick
    assert [e.type for e in ctx.log.pending].count(ev.CONCEPT_LOST) == 1
    inv.check_lost(ctx, None)  # lost once only
    assert [e.type for e in ctx.log.pending].count(ev.CONCEPT_LOST) == 1


# =================================================================
# A whole valley: with the LLM, replay, saving
# =================================================================


def eager(**llm):
    """The millennium valley with troubles that turn into problems quickly."""
    config = millennium(problem_threshold=1, tinker_cooldown_days=0.25)
    return config.with_overrides(llm=LLMConfig(enabled=True, **llm))


def test_the_valley_invents_with_its_own_words_and_replays_exactly():
    sim = Simulation(eager())
    attach(sim, gateway=FakeModel())
    sim.run(4 * 90)
    kinds = {c["kind"] for c in sim.pending_llm_calls}
    assert "invent" in kinds
    assert sim.concepts, "someone should have invented something in a season"
    for concept in sim.concepts.values():
        assert is_valley_word(concept.name.lower(), set())
        assert concept.function in phys.functions(phys.Recipe(*map(tuple, concept.recipe)))

    records = {(c["tick"], c["point"]): dict(c) for c in sim.pending_llm_calls}
    replay = Simulation(sim.config)
    replay.mind = ReplayMind(records, sim.config.llm)
    replay.run(4 * 90)
    assert replay.state() == sim.state()


def test_concepts_and_structures_are_saved_and_resume_exactly(tmp_path):
    config = millennium(problem_threshold=1, tinker_cooldown_days=0.25)
    reference = Simulation(config)
    reference.run(4 * 180)
    assert reference.concepts

    path = tmp_path / "m.sqlite"
    sim = Simulation(config)
    store = WorldStore.create(path, sim)
    for _ in range(4 * 120):
        sim.tick()
        if sim.tick_count % 4 == 0:
            store.save(sim)
    store.close()
    store = WorldStore(path)
    resumed = store.load()
    store.close()
    assert resumed.concepts == sim.concepts and resumed.structures == sim.structures
    assert resumed.world.concepts is resumed.concepts
    resumed.run(4 * 180 - resumed.tick_count)
    assert resumed.state() == reference.state()


def test_story_worlds_stay_without_inventions():
    sim = Simulation(WorldConfig.default().with_overrides(seed=3))
    sim.run(24 * 10)
    assert not sim.concepts
    assert not any(a.materials or a.items for a in sim.population.agents)


def test_the_dashboard_lists_the_valleys_ideas(tmp_path):
    from fastapi.testclient import TestClient

    from genesis.dashboard.server import create_app

    sim = Simulation(millennium(problem_threshold=1, tinker_cooldown_days=0.25))
    path = tmp_path / "m.sqlite"
    store = WorldStore.create(path, sim)
    sim.run(4 * 120)
    store.save(sim)
    store.close()
    data = TestClient(create_app(path)).get("/api/ideas").json()
    assert len(data["concepts"]) == len(sim.concepts) > 0
    first = data["concepts"][0]
    alive = [a for a in sim.population.get_alive() if first["id"] in a.knowledge]
    assert len(first["holders"]) == len(alive)
    assert first["inventor"]["name"] and first["recipe"][0]
    assert sum(y["invented"] for y in data["years"]) == len(sim.concepts)
