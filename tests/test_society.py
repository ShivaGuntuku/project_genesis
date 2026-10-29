"""M3: communication and relationships (rules only)."""

from dataclasses import replace

from genesis.history import events as ev
from genesis.mind.decision import ActionType
from genesis.rules import Rules
from genesis.society import interaction
from genesis.society.relationships import STRANGER_TRUST, Relationship
from genesis.world.world import Position
from helpers import World, make_agent, make_ctx, perceive, step

WATER_SHORE = Position(11, 13)  # north-east shore of the lake


def honest(**kw):
    genes = {"honesty": 1.0, **kw.pop("genes", {})}
    return make_agent(genes=genes, **kw)


def messages(log, act=None):
    return [e for e in log.recent if e.type == ev.MESSAGE and (act is None or e.data["act"] == act)]


# ------------------------------------------------------------------ the M3 demo scenarios


def test_agent_who_never_saw_water_walks_to_it_after_being_told():
    world = World()
    guide = honest(x=11, y=4, agent_id=1, name="Guide", thirst=0, hunger=0)
    guide.memory.remember_location("water", WATER_SHORE)
    newcomer = make_agent(
        x=10, y=4, agent_id=2, name="New", thirst=60, hunger=0, genes={"perception": 0.0}
    )

    log, drank_at = None, None
    for tick in range(30):
        log = step(newcomer, world, agents=[guide, newcomer], tick=tick, log=log)
        guide.thirst = guide.hunger = 0  # keep the guide around
        if newcomer.current_action == ActionType.DRINK:
            drank_at = tick
            break

    assert drank_at is not None, "the newcomer should reach the water"
    asked = messages(log, "request")
    told = messages(log, "share_info")
    assert asked and told and told[0].data["truthful"]
    learned = next(e for e in log.recent if e.type == ev.DISCOVERED and e.actors == (2,))
    assert learned.data["how"] == "told" and learned.cause_id == told[0].id
    assert newcomer.memory.get_exploration_goal() is None  # walked there, didn't search


def test_gifts_in_need_make_a_friend_and_feelings_are_one_sided():
    world = World()
    giver = make_agent(agent_id=1, name="Giver", genes={"generosity": 1.0})
    taker = make_agent(x=11, agent_id=2, name="Taker", hunger=80)
    ctx = make_ctx(world, [giver, taker])

    for day in range(4):
        ctx.tick = day * 24
        giver.inventory["food"] = 10
        taker.inventory["food"] = 0
        interaction.converse(ctx, giver, taker)  # first contact of the day: a greeting
        assert interaction.give(ctx, giver, taker, "food") is not None

    assert taker.relationships[giver.id].label() in ("friend", "close friend")
    assert giver.relationships[taker.id].label() == "acquaintance"  # not symmetric
    assert taker.relationships[giver.id].gratitude > giver.relationships[taker.id].gratitude
    by_id = {e.id: e for e in ctx.log.recent}
    became = [e for e in ctx.log.recent if e.type == ev.RELATION_CHANGED]
    assert became and became[-1].actors == (taker.id,)
    assert by_id[became[-1].cause_id].data["act"] == "give"  # the gift caused the friendship


def test_gossip_makes_someone_distrust_a_stranger():
    world = World()
    teller = make_agent(agent_id=1, name="Teller")
    listener = make_agent(x=11, agent_id=2, name="Listener")
    stranger = make_agent(x=19, y=19, agent_id=3, name="Stranger")
    ctx = make_ctx(world, [teller, listener, stranger])

    teller.relationships[3] = Relationship(3, "Stranger", affinity=-0.5, grievance=0.6)
    listener.relationships[1] = Relationship(1, "Teller", trust=0.9)

    interaction.gossip(ctx, teller, listener, 3)

    view = listener.relationships[3]
    assert not view.met and view.label() in ("stranger", "rival")
    assert view.trust < STRANGER_TRUST and view.affinity < 0
    assert messages(ctx.log, "gossip")[0].data["payload"]["claim"] == "can't be trusted"


# ------------------------------------------------------------------ the M3 checks


def test_cannot_give_what_you_dont_have():
    world = World()
    a, b = make_agent(agent_id=1, name="A"), make_agent(x=11, agent_id=2, name="B")
    ctx = make_ctx(world, [a, b])
    a.inventory = {"food": 0, "water": 0}

    assert interaction.give(ctx, a, b, "food") is None
    assert interaction.give(ctx, a, b, "water") is None
    assert b.inventory == {"food": 0, "water": 0}
    assert messages(ctx.log) == []


def test_giving_moves_items_and_respects_capacity():
    world = World()
    a, b = make_agent(agent_id=1, name="A"), make_agent(x=11, agent_id=2, name="B")
    ctx = make_ctx(world, [a, b])
    a.inventory = {"food": 25, "water": 2}
    b.inventory = {"food": 0, "water": world.rules.water_capacity}  # waterskin full

    assert interaction.give(ctx, a, b, "food") is not None
    assert (a.inventory["food"], b.inventory["food"]) == (15, 10)  # one meal moved
    assert interaction.give(ctx, a, b, "water") is None  # no room
    assert a.inventory["water"] == 2


def test_truthful_information_must_come_from_memory():
    world = World()
    speaker = honest(agent_id=1, name="S")
    listener = make_agent(x=11, agent_id=2, name="L")
    ctx = make_ctx(world, [speaker, listener])

    interaction.share_info(ctx, speaker, listener, "water")  # knows nothing
    assert messages(ctx.log)[-1].data["act"] == "reply"
    assert not listener.memory.get_known_locations("water")

    speaker.memory.remember_location("water", WATER_SHORE)
    interaction.share_info(ctx, speaker, listener, "water")
    told = messages(ctx.log, "share_info")[-1]
    assert told.data["truthful"] and (told.data["payload"]["x"], told.data["payload"]["y"]) == (
        WATER_SHORE.x,
        WATER_SHORE.y,
    )


def test_a_liar_is_found_out_and_trust_collapses():
    rules = replace(Rules(), lie_chance_disliked=1.0)  # disliked listeners are always lied to
    world = World(rules=rules)
    liar = make_agent(agent_id=1, name="Liar", genes={"honesty": 0.0})
    victim = make_agent(x=11, agent_id=2, name="Victim")
    ctx = make_ctx(world, [liar, victim])
    liar.relationships[2] = Relationship(2, "Victim", affinity=-0.5)
    liar.memory.remember_location("water", WATER_SHORE)

    interaction.share_info(ctx, liar, victim, "water")
    lie = messages(ctx.log, "share_info")[-1]
    assert lie.data["truthful"] is False
    fake = Position(lie.data["payload"]["x"], lie.data["payload"]["y"])
    assert not world.can_drink_at(fake)
    trust_before = victim.relationships[1].trust

    victim.position = fake  # go and look
    perceive(victim, world, [liar, victim], tick=5)

    found = [e for e in victim.memory.events if e > lie.id]
    assert not victim.memory.knows_location("water", fake)
    assert victim.relationships[1].trust < trust_before - 0.2
    assert victim.relationships[1].grievance > 0.2
    assert found, "the discovery should be recorded"


def test_refusing_someone_in_need_breeds_grievance():
    world = World()
    miser = make_agent(agent_id=1, name="Miser", genes={"generosity": 0.0})
    asker = make_agent(x=11, agent_id=2, name="Asker", thirst=90)
    miser.inventory["water"] = 2
    miser.relationships[2] = Relationship(2, "Asker", affinity=-0.8)
    ctx = make_ctx(world, [miser, asker], seed=1)

    interaction.request_item(ctx, asker, miser, "water")

    reply = messages(ctx.log)[-1]
    assert reply.data["act"] == "reply" and reply.data["payload"]["answer"] == "no"
    assert asker.relationships[1].grievance > 0 and asker.relationships[1].affinity < 0
    assert miser.inventory["water"] == 2


def test_threats_bring_fear_and_grievance():
    world = World()
    bully = make_agent(agent_id=1, name="Bully", genes={"aggression": 1.0, "strength": 1.0})
    victim = make_agent(x=11, agent_id=2, name="Victim", genes={"strength": 0.0})
    victim.inventory["food"] = 10
    ctx = make_ctx(world, [bully, victim])

    interaction.threaten(ctx, bully, victim, "food")

    view = victim.relationships[1]
    assert view.fear > 0 and view.grievance > 0 and view.affinity < 0
    outcome = messages(ctx.log)[-1]
    if outcome.data["act"] == "give":
        assert outcome.data["payload"]["under_threat"] and bully.inventory["food"] == 10
    else:
        assert bully.relationships[2].respect > 0


def test_others_can_see_who_looks_needy_and_who_carries_things():
    world = World()
    me = make_agent(agent_id=1, name="Me")
    other = make_agent(x=11, agent_id=2, name="Other", thirst=70)
    other.inventory["food"] = 5
    perceive(me, world, [me, other])
    seen = me.last_observation.visible_agents[0]
    assert seen["looks"] == ["thirsty"]
    assert seen["carrying"] == {"food": True, "water": False}


def test_relationship_labels_follow_the_numbers():
    rel = Relationship(1, "X")
    assert rel.label() == "acquaintance"
    rel.affinity, rel.trust = 0.6, 0.6
    assert rel.label() == "friend"
    rel.familiarity = 0.9
    assert rel.label() == "close friend"
    rel = Relationship(1, "X", affinity=-0.7, grievance=0.7)
    assert rel.label() == "enemy"
    assert Relationship(1, "X", met=False).label() == "stranger"


def test_feelings_saturate_and_fade():
    rel = Relationship(1, "X")
    for _ in range(50):
        rel.apply({"affinity": 0.2}, None)
    assert 0.9 < rel.affinity <= 1.0
    rel.gratitude = 1.0
    rel.decay()
    assert rel.gratitude < 1.0
