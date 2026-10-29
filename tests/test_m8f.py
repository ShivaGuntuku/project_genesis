"""M8f: the world behaves as designed - the LLM decides what matters, no words of our world,
inventions and goals of their own, talents become callings, money only when it is real."""

import json
from dataclasses import replace

from fakes import FakeModel
from genesis.config import WorldConfig
from genesis.culture import inventions as inv
from genesis.culture.words import is_valley_word, leak_words
from genesis.history import events as ev
from genesis.llm import persona, reflection
from genesis.llm.mind import LLMMind, Point, ReplayMind, _decision, attach, voiced
from genesis.llm.settings import LLMConfig
from genesis.mind import goals, plans
from genesis.mind.decision import ActionType
from genesis.persistence.db import WorldStore
from genesis.rules import Rules
from genesis.simulation.engine import Simulation
from genesis.society import economy
from helpers import World, make_agent, make_ctx

TWO = "worlds/two_valleys.toml"


def llm_world(**llm):
    config = WorldConfig.from_file(TWO).with_overrides(llm=LLMConfig(enabled=True, **llm))
    sim = Simulation(config)
    return sim


def m8f_world(**rules):
    return World(
        ticks_per_day=4,
        start_hour=6,
        rules=replace(Rules(), inventions=1, goals=1, talents=1, trade=1, **rules),
    )


# =================================================================
# Words of our world
# =================================================================


def test_words_of_our_world_are_caught_and_valley_words_are_not():
    assert leak_words("The fish are biting this week") == ["fish", "week"]
    assert leak_words("We went hunting by the river with our baskets") == [
        "hunting",
        "river",
        "baskets",
    ]
    assert leak_words("I brought berries to the store; beasts came in the night") == []
    assert leak_words("a shelter of wood and reed by the lake") == []
    assert not is_valley_word("bottle", set())


def test_a_leaking_line_keeps_the_choice_but_is_not_spoken():
    a, b = make_agent(agent_id=1, name="A"), make_agent(agent_id=2, name="B")
    from genesis.llm.mind import Option

    point = Point("turn", a, b, [Option("chat", "Just talk"), Option("praise", "Praise B")])
    record = {}
    text = json.dumps({"thought": "nice", "choice": "praise", "say": "The fish are biting!"})
    decision = _decision(text, point, 160, record)
    assert decision.choice == "praise" and decision.say is None
    assert record["error"] == "leak dropped: fish"
    clean = _decision(
        json.dumps({"thought": "", "choice": "chat", "say": "Berries!"}), point, 160, {}
    )
    assert clean.say == "Berries!"


def test_the_system_prompt_says_what_exists_and_what_does_not():
    from genesis.llm.prompts import SYSTEM

    for word in ("lakes", "clay", "flint", "seasons"):
        assert word in SYSTEM
    for word in ("fish", "weeks", "farming", "metal"):
        assert word in SYSTEM  # named as things that do not exist


# =================================================================
# Where the LLM's time goes
# =================================================================


def test_greetings_and_some_small_talk_can_be_left_to_the_rules():
    sim = llm_world()
    attach(sim, gateway=FakeModel())
    ctx = make_ctx(sim.world, sim.population.agents)
    ctx.mind = sim.mind
    a, b = sim.population.agents[:2]
    assert voiced(ctx, "greet", a, b) and voiced(ctx, "turn", a, b)
    sim.mind.config = replace(sim.mind.config, voice_greetings=False, chatter_share=0.0)
    assert not voiced(ctx, "greet", a, b) and not voiced(ctx, "turn", a, b)
    sim.mind.config = replace(sim.mind.config, chatter_share=0.5)
    share = sum(voiced(make_ctx(sim.world, [a, b], tick=t), "turn", a, b) for t in range(400))
    ctx2 = make_ctx(sim.world, [a, b], tick=7)
    ctx2.mind = sim.mind
    assert (
        120 <= sum(voiced(make_ctx_with(sim, t), "turn", a, b) for t in range(400)) <= 280
        and share >= 0
    )
    assert voiced(ctx2, "turn", a, b) == voiced(ctx2, "turn", a, b)  # no chance used


def make_ctx_with(sim, tick):
    ctx = make_ctx(sim.world, sim.population.agents, tick=tick)
    ctx.mind = sim.mind
    return ctx


def test_zero_caps_mean_no_cap():
    mind = LLMMind(LLMConfig(enabled=True, max_calls_per_tick=0, max_calls_per_day=0), None)
    ctx = make_ctx(World(), [])
    mind._tick_calls = mind._day_calls = 10_000
    mind._tick, mind._day = ctx.tick, ctx.day
    assert mind._within_budget(ctx, "turn") and mind._within_budget(ctx, "request")


def test_only_chatter_is_cut_and_hard_choices_still_go_to_the_llm():
    sim = llm_world(voice_greetings=False, chatter_share=0.0, plan_every_days=3)
    attach(sim, gateway=FakeModel())
    sim.run(4 * 9)
    kinds = {c["kind"] for c in sim.pending_llm_calls}
    assert "greet" not in kinds and "turn" not in kinds
    assert "plan" in kinds


# =================================================================
# Their own plans
# =================================================================


def test_plans_are_built_from_their_life_and_chosen_by_them():
    world = m8f_world()
    a = make_agent(agent_id=1, name="A")
    a.talent = "healer"
    friend = make_agent(x=12, agent_id=2, name="F")
    a.relationship(2, "F").affinity = 0.9
    a.relationship(2, "F").familiarity = 0.9
    a.relationship(2, "F").trust = 0.9
    goals.set_goal(
        make_ctx(world, [a]), a, "know the land", "To see where the sun goes", "own words"
    )
    world.concepts[1] = inv.Concept(
        1, "Vrosk", "holds water", ["holds water"], [["clay"], ["hollow"]], 9, 0, ""
    )
    a.knowledge.add(1)
    ctx = make_ctx(world, [a, friend])
    keys = [k for k, _ in plans.options(a, ctx)]
    assert {"explore", "goal", "tinker", "make:1", "calling", "trade", "rest"} <= set(keys)
    assert "company:2" in keys
    assert "Look after anyone who is hurt or sick" in dict(plans.options(a, ctx)).values()


def test_a_plan_leans_free_time_its_way_and_the_persona_knows_it():
    world = m8f_world()
    a = make_agent(agent_id=1, name="A", energy=40)  # tired: would rest
    ctx = make_ctx(world, [a])
    a.perceive(ctx)
    a.decide(world)
    assert a.current_action == ActionType.REST
    a.plan = {
        "focus": "explore",
        "label": "Go and see land you don't know yet",
        "why": "",
        "tick": 0,
        "until": 999,
    }
    a.decide(world)
    assert a.current_action == ActionType.EXPLORE
    assert "These days you set out to: Go and see land" in persona.fact_block(a, ctx)


def test_the_valley_plans_with_the_llm_and_replays_exactly():
    sim = llm_world(plan_every_days=3, voice_greetings=False, chatter_share=0.2)
    attach(sim, gateway=FakeModel(prefer=("explore",)))
    sim.run(4 * 9)
    planned = [e for e in sim.log.pending if e.type == ev.PLANNED]
    assert planned and all(e.data["focus"] == "explore" for e in planned)
    assert any(a.plan for a in sim.population.agents)
    records = {(c["tick"], c["point"]): dict(c) for c in sim.pending_llm_calls}
    replay = Simulation(sim.config)
    replay.mind = ReplayMind(records, sim.config.llm)
    replay.run(4 * 9)
    assert replay.state() == sim.state()


# =================================================================
# Inventions and goals of their own
# =================================================================


def test_an_idea_named_after_our_things_keeps_its_recipe_but_not_the_name():
    world = m8f_world(invent_base_chance=1.0)
    maker = make_agent(agent_id=1, name="Maker")
    maker.materials = {"clay": 1}

    class Mind:
        active = True

        def ask(self, ctx, point, q):
            if q.kind == "invent":
                text = {
                    "thought": "",
                    "idea": "a water bottle",
                    "materials": ["clay"],
                    "actions": ["hollow"],
                }
            else:
                text = {"thought": "", "name": "Vrosk"}
            return q.parse(json.dumps(text), {})

    ctx = make_ctx(world, [maker])
    ctx.mind = Mind()
    inv.experiment(ctx, maker, "carry water")
    (concept,) = world.concepts.values()
    assert concept.description == "" and concept.recipe == [["clay"], ["hollow"]]


def test_wanting_to_be_admired_can_lead_to_something_pretty():
    from genesis.culture.materials import SOLVES

    assert SOLVES["be admired"] == "adorns"
    world = m8f_world()
    proud = make_agent(agent_id=1, name="Proud")
    goals.set_goal(make_ctx(world, [proud]), proud, "be respected")
    for _ in range(3):
        inv.notice_problems(make_ctx(world, [proud]), proud)
    assert proud.problems["be admired"] > 0


def test_goals_that_copy_the_rules_words_are_not_their_own():
    assert goals.too_close("I want my people never to go hungry.")
    assert goals.too_close("I want the others to look up to me")
    assert not goals.too_close("To find where the morning light first touches the hills")
    names = []
    raw = json.dumps(
        {
            "thought": "",
            "beliefs": ["The fish come back in spring", "Sharing keeps us strong"],
            "story": "I came here young. I fished in the river. I learned clay.",
            "goal": "I want my people never to go hungry.",
            "goal_need": "provide",
        }
    )
    record = {}
    result = reflection.parse(raw, names, record, ["provide"])
    assert result.beliefs == ["Sharing keeps us strong"]
    assert "river" not in result.story and "I learned clay." in result.story
    assert result.goal is None  # the rules' own sentence
    assert "leak dropped" in record["error"]


# =================================================================
# Talents become callings
# =================================================================


def test_seeing_new_land_makes_a_scout_and_the_keen_eyed_see_more():
    world = m8f_world()
    plain, keen = make_agent(x=4, agent_id=1, name="P"), make_agent(x=15, agent_id=2, name="K")
    keen.talent = "keen-eyed"
    for t in (1, 2):
        for a in (plain, keen):
            a.position = a.position.__class__(a.position.x, a.position.y + t)
            a.perceive(make_ctx(world, [plain, keen], tick=t))
    assert keen.work["scout"] > plain.work["scout"] > 0
    assert keen.abilities.get("scouting") > plain.abilities.get("scouting") > 0


def test_a_healer_goes_to_tend_someone_hurt_in_view():
    world = m8f_world()
    healer = make_agent(agent_id=1, name="Healer")
    healer.talent = "healer"
    hurt = make_agent(x=12, agent_id=2, name="Hurt", health=40)
    ctx = make_ctx(world, [healer, hurt])
    healer.perceive(ctx)
    healer.decide(world)
    assert healer.current_action == ActionType.TEND
    healer.hours_left = 6
    healer.act(ctx)
    assert hurt.health > 40
    assert any(e.data.get("act") == "heal" for e in ctx.log.pending if e.type == ev.MESSAGE)


def test_gifts_are_kindness_not_a_profession():
    from genesis.agents import talents

    a = make_agent(agent_id=1, name="A")
    log = make_ctx(m8f_world(), [a]).log
    log.emit(0, ev.MESSAGE, actors=(1,), targets=(2,), data={"act": "give", "payload": {}})
    talents.count_work(log.pending[-1], {1: a})
    assert a.work == {}


# =================================================================
# Money only when it is real; the check tool
# =================================================================


def test_no_money_before_trading_has_gone_on_long_enough():
    world = m8f_world(currency_after_days=360)
    world.economy["indirect"] = [[1, "valley", "shell", 10 + i % 5] for i in range(30)]
    ctx = make_ctx(world, [], tick=100)

    class Sim:
        population = type("P", (), {"get_alive": lambda _: []})()

    economy.nightly(ctx, Sim())
    assert not world.economy["currencies"]
    ctx = make_ctx(world, [], tick=world.calendar.ticks(days=361))
    world.economy["indirect"] = [[ctx.tick, "valley", "shell", 10 + i % 5] for i in range(30)]
    economy.nightly(ctx, Sim())
    assert world.economy["currencies"]


def test_the_check_tool_grades_a_world(tmp_path):
    from genesis.history.check import check

    sim = Simulation(WorldConfig.from_file(TWO))
    path = tmp_path / "w.sqlite"
    store = WorldStore.create(path, sim)
    sim.run(4 * 60)
    store.save(sim)
    store.close()
    info, rows = check(path)
    assert info["days"] == 60 and not info["llm"]
    names = [r.requirement for r in rows]
    assert "No leakage from our world" in names and "Money they create themselves" in names
    assert all(r.mark in ("ok", "weak", "no", "wait", "n/a") for r in rows)
