"""M8d: talents, healing, professions, trade, promises and money of their own."""

import ast
import pathlib
from dataclasses import replace

from fakes import FakeModel
from genesis.agents import talents
from genesis.config import WorldConfig
from genesis.culture.words import is_valley_word
from genesis.history import events as ev
from genesis.llm.mind import attach
from genesis.llm.settings import LLMConfig
from genesis.persistence.db import WorldStore
from genesis.rules import Rules
from genesis.simulation.engine import Simulation
from genesis.society import economy
from genesis.society.economy import Offer
from helpers import World, make_agent, make_ctx

MILLENNIUM = "worlds/two_valleys.toml"


def econ_world(**rules):
    return World(
        ticks_per_day=4,
        start_hour=6,
        rules=replace(Rules(), inventions=1, talents=1, trade=1, **rules),
    )


class Sim:  # what the nightly checks need from a Simulation
    def __init__(self, agents):
        self.population = type("P", (), {"get_alive": lambda _: [a for a in agents if a.alive]})()


def test_no_two_rules_share_a_name():
    """A repeated field silently overrides the first one's default (it happened once)."""
    tree = ast.parse(pathlib.Path("src/genesis/rules.py").read_text())
    rules = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Rules")
    names = [n.target.id for n in rules.body if isinstance(n, ast.AnnAssign)]
    assert len(names) == len(set(names))


# =================================================================
# Talents, healing, professions
# =================================================================


def test_everyone_is_born_with_one_talent_mostly_from_a_parent():
    sim = Simulation(WorldConfig.from_file(MILLENNIUM))
    assert all(a.talent in talents.TALENTS for a in sim.population.agents)
    assert talents.born_with(9, 4) == talents.born_with(9, 4)  # its own seeded stream
    mother = make_agent(agent_id=1, name="M")
    mother.talent = "healer"
    children = [talents.born_with(9, i, [mother]) for i in range(100, 200)]
    assert 0.8 <= children.count("healer") / len(children) <= 1.0  # some are born different


def test_talents_work_only_when_the_world_has_them():
    world, plain = econ_world(), World()
    a = make_agent(agent_id=1, name="A")
    base_sight = a.vision_radius(world)
    base_food = a.carry_capacity("food", world.rules)
    a.talent = "keen-eyed"
    assert a.vision_radius(world) == base_sight + 2 and a.vision_radius(plain) == base_sight
    a.talent = "strong-armed"
    assert a.carry_capacity("food", world.rules) > base_food
    assert talents.learning(a, "combat", world.rules) == 2.0
    assert talents.learning(a, "crafting", world.rules) == 1.0
    assert talents.learning(a, "combat", plain.rules) == 1.0


def test_wounds_can_be_tended_and_healers_do_it_better():
    world = econ_world()
    healer = make_agent(agent_id=1, name="Healer")
    plain = make_agent(agent_id=2, name="Plain")
    hurt = make_agent(agent_id=3, name="Hurt", health=30)
    ctx = make_ctx(world, [healer, plain, hurt])
    assert economy.may_heal(ctx, plain, hurt)
    assert not economy.may_heal(ctx, plain, healer)  # not hurt
    economy.heal(ctx, plain, hurt)
    by_plain = hurt.health - 30
    assert not economy.may_heal(ctx, plain, hurt)  # once a day
    hurt.health = 30
    healer.talent = "healer"
    economy.heal(ctx, healer, hurt)
    assert hurt.health - 30 > by_plain
    assert healer.abilities.get("healing") > 0
    assert hurt.relationships[healer.id].gratitude > 0


def test_professions_come_from_the_work_people_actually_do():
    skills = {"crafting": 0.5}
    assert talents.profession_of({"maker": 8.0, "provider": 2.0}, skills) == "maker"
    assert talents.profession_of({"maker": 2.0}, skills) is None  # not enough of it
    assert talents.profession_of({"maker": 6.0, "scout": 6.0, "fighter": 6.0}, skills) is None
    assert talents.profession_of({"maker": 8.0}, {"crafting": 0.1}) is None  # not skilled
    a = make_agent(agent_id=1, name="A")
    log = make_ctx(econ_world(), [a]).log
    log.emit(0, ev.MADE, actors=(1,))
    talents.count_work(log.pending[-1], {1: a})
    assert a.work == {"maker": 1.0}
    talents.fade_work(a)
    assert a.work["maker"] < 1.0


# =================================================================
# Value and barter
# =================================================================


def test_goods_are_worth_their_use_and_what_others_take_them_for():
    world = econ_world()
    hungry = make_agent(agent_id=1, name="Hungry", hunger=80)
    full = make_agent(agent_id=2, name="Full", hunger=0)
    ctx = make_ctx(world, [hungry, full])
    assert economy.use_value(hungry, "food", ctx) > economy.use_value(full, "food", ctx)
    vain = make_agent(agent_id=3, name="Vain", genes={"ambition": 1.0})
    assert economy.use_value(vain, "shell", ctx) > economy.use_value(full, "shell", ctx)
    assert economy.saleability(full, "shell") == 0.25  # never seen a trade
    full.market["shell"] = [9, 10]
    assert economy.value(full, "shell", ctx) > economy.use_value(full, "shell", ctx)


def _traders(world):
    hungry = make_agent(agent_id=1, name="Hungry", hunger=75)
    hungry.materials = {"shell": 3}
    full = make_agent(x=11, agent_id=2, name="Full", hunger=0)
    full.inventory["food"] = 4 * world.rules.food_per_meal
    return hungry, full


def test_a_fair_barter_moves_goods_and_counts_as_taken_to_pass_on():
    world = econ_world()
    hungry, full = _traders(world)
    full.genome = full.genome.with_genes(ambition=0.0)  # no use for pretty things
    full.hunger, full.inventory["food"] = 30, 2 * world.rules.food_per_meal  # food is of use
    full.market["shell"] = [8, 10]  # Full has seen shells taken again and again
    hungry.market["shell"] = [8, 10]
    ctx = make_ctx(world, [hungry, full])
    offer = economy.best_offer(ctx, hungry, full)
    assert offer is not None and offer.give == "shell" and offer.get == "food"
    economy.trade(ctx, hungry, full, offer)
    reply = ctx.log.pending[-1]
    p = reply.data["payload"]
    assert p["answer"] == "accept_trade" and p["indirect"]  # Full has no use for shells
    assert full.materials["shell"] == offer.n == 1
    assert hungry.inventory["food"] == world.rules.food_per_meal
    assert full.inventory["food"] == world.rules.food_per_meal
    assert world.economy["indirect"] and world.economy["indirect"][0][2] == "shell"
    assert full.market["shell"][1] == 11  # everyone there saw it


def test_nobody_gives_up_what_they_need_for_what_they_dont():
    world = econ_world()
    hungry, full = _traders(world)
    full.hunger = 90  # now Full needs the food too
    ctx = make_ctx(world, [hungry, full])
    offer = Offer("shell", 1, "food")
    assert not economy.accepts(ctx, full, hungry, offer)
    economy.trade(ctx, hungry, full, offer)
    assert ctx.log.pending[-1].data["payload"]["answer"] == "refuse_trade"
    assert "shell" not in full.materials


# =================================================================
# Promises
# =================================================================


def test_promises_are_kept_or_broken_and_trust_follows():
    world = econ_world(promise_days=2)
    hungry, full = _traders(world)
    hungry.materials = {}
    full.relationship(hungry.id, hungry.name).trust = 0.9
    ctx = make_ctx(world, [hungry, full], seed=1)
    offer = economy.best_offer(ctx, hungry, full)
    assert offer is not None and offer.promise
    accepted = False
    for seed in range(10):
        ctx = make_ctx(world, [hungry, full], seed=seed)
        economy.trade(ctx, hungry, full, offer)
        if hungry.owes:
            accepted = True
            break
    assert accepted and hungry.owes[0]["to"] == full.id
    trust = full.relationships[hungry.id].trust

    hungry.hunger = 0
    hungry.inventory["food"] = 2 * world.rules.food_per_meal
    full.inventory["food"] = 0  # room to be paid back
    economy.repay_due(ctx, hungry, full)
    assert not hungry.owes and full.relationships[hungry.id].trust > trust
    assert ctx.log.pending[-1].data["act"] == "repay"

    hungry.owes.append({"to": full.id, "good": "food", "due": 3, "event": None})
    trust = full.relationships[hungry.id].trust
    ctx = make_ctx(world, [hungry, full], tick=4)
    economy.nightly(ctx, Sim([hungry, full]))
    assert not hungry.owes and full.relationships[hungry.id].trust < trust
    assert any(e.type == ev.PROMISE_BROKEN for e in ctx.log.pending)


# =================================================================
# Money of their own
# =================================================================


def _season_of_shells(world, users=3, extra=0):
    world.economy["indirect"] = [[1, "valley", "shell", 10 + i % users] for i in range(8)]
    world.economy["indirect"] += [[1, "valley", "clay", 20] for _ in range(extra)]


def test_money_emerges_when_one_good_is_taken_to_pass_on_by_many():
    world = econ_world()
    a, b = make_agent(agent_id=1, name="A"), make_agent(x=11, agent_id=2, name="B")
    ctx = make_ctx(world, [a, b], tick=10)
    _season_of_shells(world, users=2)
    economy.nightly(ctx, Sim([a, b]))
    assert not world.economy["currencies"]  # only two people: not yet the valley's
    _season_of_shells(world, users=3, extra=10)
    economy.nightly(ctx, Sim([a, b]))
    assert not world.economy["currencies"]  # not most of the season's indirect exchanges
    _season_of_shells(world, users=3)
    economy.nightly(ctx, Sim([a, b]))
    (money,) = world.economy["currencies"]
    assert money["good"] == "shell" and is_valley_word(money["name"].lower(), set())
    assert any(e.type == ev.CURRENCY_EMERGED for e in ctx.log.pending)

    a.materials, a.hunger = {"shell": 4}, 75
    b.inventory["food"] = 4 * world.rules.food_per_meal
    b.market["shell"] = a.market["shell"] = [9, 10]
    economy.trade(ctx, a, b, Offer("shell", 1, "food"))
    price = ctx.log.pending[-1].data["payload"]["price"]
    assert price == {"currency": 1, "good": "food", "amount": 1}


def test_old_season_exchanges_drop_out_of_the_count():
    world = econ_world(currency_window_days=1)
    _season_of_shells(world)
    ctx = make_ctx(world, [], tick=100)
    economy.nightly(ctx, Sim([]))
    assert world.economy["indirect"] == [] and not world.economy["currencies"]


# =================================================================
# With the LLM, in a whole valley, saved and shown
# =================================================================


def test_the_mind_can_turn_a_trade_down():
    config = WorldConfig.from_file(MILLENNIUM).with_overrides(llm=LLMConfig(enabled=True))
    sim = Simulation(config)
    attach(sim, gateway=FakeModel(prefer=("refuse",)))
    world = sim.world
    hungry, full = _traders(world)
    full.market["shell"] = [8, 10]
    ctx = make_ctx(world, [hungry, full])
    ctx.mind = sim.mind
    economy.trade(ctx, hungry, full, Offer("shell", 1, "food"))
    assert ctx.log.pending[-1].data["payload"]["answer"] == "refuse_trade"
    assert any(c["kind"] == "trade" for c in ctx.llm_calls)


def test_a_valley_trades_and_resumes_exactly_and_the_dashboard_shows_it(tmp_path):
    config = WorldConfig.from_file(MILLENNIUM)
    reference = Simulation(config)
    reference.run(4 * 360)
    acts = [e.data.get("act") for e in reference.log.pending if e.type == ev.MESSAGE]
    assert acts.count("trade") >= 10

    path = tmp_path / "m.sqlite"
    sim = Simulation(config)
    store = WorldStore.create(path, sim)
    for _ in range(4 * 200 + 2):
        sim.tick()
        if sim.tick_count % 4 == 0:
            store.save(sim)
    store.close()
    store = WorldStore(path)
    resumed = store.load()
    store.close()
    resumed.run(4 * 360 - resumed.tick_count)
    assert resumed.state() == reference.state()
    assert resumed.world.economy == reference.world.economy

    from fastapi.testclient import TestClient

    from genesis.dashboard.server import create_app

    store = WorldStore(path)
    store.save(resumed)
    store.close()
    client = TestClient(create_app(path))
    data = client.get("/api/economy").json()
    assert sum(y["offers"] for y in data["years"]) == acts.count("trade")
    assert all(p["talent"] in talents.TALENTS for p in data["people"])
    one = data["people"][0]["id"]
    assert client.get(f"/api/agents/{one}").json()["talent"] == data["people"][0]["talent"]


def test_every_skill_can_be_named():
    from genesis.agents.abilities import SKILL_NOUNS, SKILLS, describe_skill

    assert set(SKILL_NOUNS) == set(SKILLS)
    assert describe_skill("healing", 0.8) == "expert healer"
