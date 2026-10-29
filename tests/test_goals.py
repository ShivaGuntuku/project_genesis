"""M8c: drives, goals and long-term projects."""

from dataclasses import replace

from fakes import FakeModel
from genesis.config import WorldConfig
from genesis.culture.inventions import Concept
from genesis.history import events as ev
from genesis.llm import persona
from genesis.llm.mind import attach
from genesis.llm.settings import LLMConfig
from genesis.mind import goals
from genesis.mind.decision import ActionType
from genesis.persistence.db import WorldStore
from genesis.rules import Rules
from genesis.simulation.engine import Simulation
from genesis.society.groups import Group, view
from helpers import World, make_agent, make_ctx

MILLENNIUM = "worlds/two_valleys.toml"
YEAR_DAYS = 360


def goal_world(**rules):
    return World(
        ticks_per_day=4, start_hour=6, rules=replace(Rules(), inventions=1, goals=1, **rules)
    )


def millennium(**rules) -> WorldConfig:
    config = WorldConfig.from_file(MILLENNIUM)
    return config.with_overrides(rules=replace(config.rules, **rules)) if rules else config


def water_concept(world, cid=1):
    world.concepts[cid] = Concept(
        id=cid,
        name="Vrosk",
        function="holds water",
        functions=["holds water", "holds food"],
        recipe=[["clay"], ["hollow"]],
        inventor=99,
        tick=0,
        description="",
    )
    return world.concepts[cid]


# =================================================================
# Drives and choosing a goal
# =================================================================


def test_drives_come_from_genes_family_and_age():
    world = goal_world()
    young = make_agent(agent_id=1, name="Young", genes={"curiosity": 0.9, "ambition": 0.2})
    old = make_agent(
        agent_id=2, name="Old", genes={"ambition": 0.9}, age=world.calendar.ticks(days=60 * 360)
    )
    ctx = make_ctx(world, [young, old])
    d = goals.drives(young, ctx)
    assert d["curiosity"] == 0.9 and d["ambition"] == 0.2
    assert goals.drives(old, ctx)["legacy"] > d["legacy"]  # legacy grows with age
    lonely_care = d["care"]
    young.partner, young.children = 2, [3]
    child = make_agent(agent_id=3, name="Child", age=10)
    ctx = make_ctx(world, [young, old, child])
    assert goals.drives(young, ctx)["care"] == lonely_care + 0.5  # a child alive, a partner
    assert goals.strongest_drive(young, ctx) == "curiosity"


def test_the_strongest_pull_becomes_the_goal():
    world = goal_world()
    thirsty = make_agent(agent_id=1, name="Thirsty", genes={"curiosity": 0.8, "ambition": 0.1})
    thirsty.problems = {"carry water": 10.0}
    proud = make_agent(agent_id=2, name="Proud", genes={"ambition": 0.95, "curiosity": 0.0})
    calm = make_agent(
        agent_id=3, name="Calm", genes={"ambition": 0.1, "curiosity": 0.0, "generosity": 0.1}
    )
    ctx = make_ctx(world, [thirsty, proud, calm])
    assert goals.choose(thirsty, ctx) == "carry water"
    assert goals.choose(proud, ctx) == "be respected"
    assert goals.choose(calm, ctx) is None  # nothing pulls hard enough: content for now

    water_concept(world)
    thirsty.knowledge.add(1)  # already solved: no longer a goal
    assert "carry water" not in goals.scores(thirsty, ctx)


def test_story_worlds_and_rules_only_without_inventions_offer_fewer_goals():
    assert not Rules().goals
    no_inventions = replace(Rules(), goals=1)
    assert "carry water" not in goals.needs_allowed(no_inventions)
    assert "be respected" in goals.needs_allowed(no_inventions)
    sim = Simulation(WorldConfig.default().with_overrides(seed=3))
    sim.run(24 * 5)
    assert all(a.goal is None for a in sim.population.agents)


# =================================================================
# Reaching, giving up, deeds
# =================================================================


class Sim:  # the bits of a Simulation that goals.nightly needs
    def __init__(self, agents):
        self.population = type("P", (), {"get_alive": lambda _: [a for a in agents if a.alive]})()


def test_goals_are_set_on_their_day_reached_by_the_world_and_given_up_in_time():
    world = goal_world(goal_every_days=1, goal_give_up_days=10)
    thirsty = make_agent(agent_id=1, name="Thirsty", genes={"curiosity": 0.8})
    thirsty.problems = {"carry water": 10.0}
    proud = make_agent(agent_id=2, name="Proud", genes={"ambition": 0.95})
    child = make_agent(agent_id=3, name="Child", age=10, genes={"ambition": 0.95})
    child.stage = "child"
    agents = [thirsty, proud, child]
    sim = Sim(agents)
    ctx = make_ctx(world, agents, tick=3)
    goals.nightly(ctx, sim)
    assert thirsty.goal["need"] == "carry water" and proud.goal["need"] == "be respected"
    assert child.goal is None  # children have no goals yet
    assert [e.type for e in ctx.log.pending].count(ev.GOAL_SET) == 2

    water_concept(world)
    thirsty.knowledge.add(1)  # learned or invented a way: the world judges it reached
    ctx = make_ctx(world, agents, tick=7)
    goals.nightly(ctx, sim)
    assert thirsty.goal is None
    reached = [e for e in ctx.log.pending if e.type == ev.GOAL_REACHED]
    assert reached and reached[0].data["need"] == "carry water"

    ctx = make_ctx(world, agents, tick=world.calendar.ticks(days=11))
    goals.nightly(ctx, sim)
    assert proud.goal is None or proud.goal["tick"] == ctx.tick  # given up (maybe set anew)
    assert any(e.type == ev.GOAL_ABANDONED for e in ctx.log.pending)
    assert "gave_up:be respected" in proud.asked
    assert "be respected" not in goals.scores(proud, ctx)  # not straight back to it


def test_leading_a_group_or_being_looked_up_to_is_respect():
    world = goal_world()
    proud = make_agent(agent_id=1, name="Proud")
    others = [make_agent(agent_id=i, name=f"O{i}") for i in range(2, 5)]
    ctx = make_ctx(world, [proud, *others])
    assert not goals.reached(proud, ctx, "be respected", {})
    for o in others:
        o.relationship(proud.id, proud.name).respect = 0.5
    assert goals.reached(proud, ctx, "be respected", {})
    for o in others:
        o.relationships[proud.id].respect = 0.0
    ctx.groups[1] = Group(1, "Tavu", 1, 1, [1, 2], (10, 10), founded_tick=0)
    assert goals.reached(proud, ctx, "be respected", {})


def test_providing_and_teaching_count_from_when_the_goal_was_set():
    world = goal_world()
    giver = make_agent(agent_id=1, name="Giver", genes={"generosity": 1.0})
    giver.group_id = 1
    taker = make_agent(agent_id=2, name="Taker")
    agents = {1: giver, 2: taker}
    ctx = make_ctx(world, [giver, taker])
    for _ in range(3):
        goals.count_deeds(
            ctx.log.emit(0, ev.MESSAGE, actors=(1,), targets=(2,), data={"act": "give"})
            and ctx.log.pending[-1],
            agents,
        )
    assert giver.deeds["fed"] == 3
    goals.set_goal(ctx, giver, "provide")
    assert not goals.reached(giver, ctx, "provide", giver.goal)  # 3 before, none since
    giver.deeds["fed"] += goals.FED_TO_PROVIDE
    assert goals.reached(giver, ctx, "provide", giver.goal)

    goals.count_deeds(
        ctx.log.emit(0, ev.LEARNED, actors=(2,), targets=(1,), data={"learned": True})
        and ctx.log.pending[-1],
        agents,
    )
    assert giver.deeds["taught"] == 1  # the teacher is the one remembered

    goals._end(ctx, giver, ev.GOAL_REACHED)
    assert "provide" not in goals.scores(giver, ctx)  # a reached aim rests for a year


# =================================================================
# Projects: free time goes into the goal
# =================================================================


def test_a_goal_makes_even_the_incurious_tinker_for_it():
    world = goal_world()
    plain = make_agent(agent_id=1, name="Plain", genes={"curiosity": 0.1})
    plain.materials = {"clay": 1, "reed": 1, "fibre": 1}
    ctx = make_ctx(world, [plain])
    plain.perceive(ctx)
    plain.decide(world)
    assert plain.current_action != ActionType.EXPERIMENT  # not curious, no goal
    goals.set_goal(ctx, plain, "carry water")
    plain.decide(world)
    assert plain.current_action == ActionType.EXPERIMENT


def test_wanting_to_know_the_land_sends_them_exploring():
    world = goal_world()
    homebody = make_agent(agent_id=1, name="Homebody", genes={"curiosity": 0.0}, energy=40)
    ctx = make_ctx(world, [homebody])
    homebody.perceive(ctx)
    homebody.decide(world)
    assert homebody.current_action == ActionType.REST
    goals.set_goal(ctx, homebody, "know the land")
    homebody.decide(world)
    assert homebody.current_action == ActionType.EXPLORE


def test_providing_brings_even_one_spare_meal_home():
    world = goal_world()
    giver = make_agent(x=12, y=10, agent_id=1, name="Giver")  # near home, not at it
    giver.inventory["food"] = world.rules.food_per_meal
    g = Group(1, "Tavu", 1, 1, [1], (10, 10), founded_tick=0)
    giver.group_id, giver.group = 1, view(g)
    ctx = make_ctx(world, [giver])
    giver.perceive(ctx)
    giver.decide(world)
    assert giver.current_action != ActionType.GO_HOME  # one meal is not "spare" normally
    goals.set_goal(ctx, giver, "provide")
    giver.decide(world)
    assert giver.current_action == ActionType.GO_HOME


# =================================================================
# Their own words, persona, a whole valley
# =================================================================


def test_reflection_writes_the_goal_in_their_own_words():
    config = millennium(reflect_every_days=2).with_overrides(llm=LLMConfig(enabled=True))
    sim = Simulation(config)
    attach(sim, gateway=FakeModel(goal_need="know the land"))
    sim.run(4 * 6)
    set_by_mind = [
        e for e in sim.log.pending if e.type == ev.GOAL_SET and e.data["how"] == "own words"
    ]
    assert set_by_mind
    agent = sim.population.get_by_id(set_by_mind[0].actors[0])
    assert agent.goal["text"].startswith("In my own words") or agent.goal is None
    reflect = next(c for c in sim.pending_llm_calls if c["kind"] == "reflect")
    assert '"goal_need"' in reflect["prompt"] and "know the land" in reflect["prompt"]


def test_the_persona_knows_their_drive_and_goal():
    world = goal_world()
    a = make_agent(agent_id=1, name="A", genes={"curiosity": 1.0})
    ctx = make_ctx(world, [a])
    goals.set_goal(ctx, a, "know the land", "I will walk to where the sun sets.", how="own words")
    text = persona.fact_block(a, ctx)
    assert "finding out how things work" in text
    assert "I will walk to where the sun sets." in text
    goals.set_goal(ctx, a, "know the land")  # the rules' goal: shown as a feeling, not words
    text = persona.fact_block(a, ctx)
    assert goals.NEEDS["know the land"] not in text and "the land you have not seen" in text


def test_a_valley_strives_reaches_and_resumes_exactly(tmp_path):
    reference = Simulation(millennium())
    reference.run(4 * YEAR_DAYS)
    types = [e.type for e in reference.log.pending]
    assert types.count(ev.GOAL_SET) >= 5 and ev.GOAL_REACHED in types

    path = tmp_path / "m.sqlite"
    sim = Simulation(millennium())
    store = WorldStore.create(path, sim)
    for _ in range(4 * 200 + 3):
        sim.tick()
        if sim.tick_count % 4 == 0:
            store.save(sim)
    store.close()
    store = WorldStore(path)
    resumed = store.load()
    store.close()
    assert any(a.goal for a in resumed.population.get_alive())  # goals are saved
    resumed.run(4 * YEAR_DAYS - resumed.tick_count)
    assert resumed.state() == reference.state()

    from fastapi.testclient import TestClient

    from genesis.dashboard.server import create_app

    store = WorldStore(path)
    store.save(resumed)
    store.close()
    client = TestClient(create_app(path))
    data = client.get("/api/goals").json()
    assert sum(n["set"] for n in data["needs"]) == types.count(ev.GOAL_SET)
    with_goal = next((c for c in data["current"]), None)
    if with_goal:
        detail = client.get(f"/api/agents/{with_goal['id']}").json()
        assert detail["goal"]["text"] == with_goal["text"] and detail["goals"]
