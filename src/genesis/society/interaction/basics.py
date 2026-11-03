"""Saying something, feeling something about someone, and small shared helpers."""

from enum import StrEnum

from genesis.history import events as ev
from genesis.llm.mind import REJECTED, Decision, Option, Point
from genesis.society.relationships import EFFECTS, Relationship

RESOURCES = ("water", "food")


NEED_OF = {"water": "thirst", "food": "hunger"}


class Act(StrEnum):
    GREET = "greet"
    CHAT = "chat"
    SHARE_INFO = "share_info"
    REQUEST = "request"
    GIVE = "give"
    GOSSIP = "gossip"
    PRAISE = "praise"
    INSULT = "insult"
    THREATEN = "threaten"
    REPLY = "reply"
    PROPOSE = "propose"  # "shall we have a child together?" (M6)
    FOUND = "found"  # "let's stand together as a group" (M7)
    INVITE = "invite"  # "join our group"
    ORDER = "order"  # a leader to a member
    CHALLENGE = "challenge"  # a member to the leader: "I should lead us"
    ATTACK = "attack"  # starts a fight
    TEACH = "teach"  # shows how to make something (M8b)
    TRADE = "trade"  # offers some of this for some of that, or asks on a promise (M8d)
    REPAY = "repay"  # brings back what was promised
    HEAL = "heal"  # tends someone's wounds


def say(
    ctx,
    speaker,
    listener,
    act: Act,
    payload=None,
    truthful=True,
    reply_to=None,
    cause_id=None,
    voice: Decision | None = None,
):
    """Record one speech act. Returns its event id.

    voice: the mind's decision, when the agent chose this act and put it in its own words."""
    data = {
        "act": str(act),
        "payload": payload or {},
        "truthful": truthful,
        "reply_to": reply_to,
    }
    if voice is not None:
        data["line"] = voice.say
        data["thought"] = voice.thought
    event_id = ctx.log.emit(
        ctx.tick,
        ev.MESSAGE,
        actors=(speaker.id,),
        targets=(listener.id,),
        position=speaker.position,
        data=data,
        cause_id=cause_id,
    )
    speaker.memory.remember_event(event_id)
    listener.memory.remember_event(event_id)
    if voice is not None and voice.call is not None and voice.call.get("event_id") is None:
        voice.call["event_id"] = event_id
    return event_id


def think(ctx, kind, agent, other, options=(), heard=None, resource=None) -> Decision | None:
    """Ask the mind (if any) to decide. None means: the rules decide."""
    mind = ctx.mind
    if mind is None or not mind.active:
        return None
    if agent.current_action is not None and agent.current_action.value == "sleep":
        return None  # sleepers never talk to the LLM (the rules answer for them)
    options = options() if callable(options) else list(options)
    point = Point(kind, agent, other, options, heard=heard, resource=resource)
    return mind.decide(ctx, ctx.next_point(), point)


def _reject(decision: Decision, reason: str):
    """The mind chose something the world didn't allow after all: logged, rules take over."""
    if decision.call is not None:
        decision.call["outcome"] = REJECTED
        decision.call["error"] = reason


def feel(ctx, agent, other, effect, event_id, met=True) -> Relationship:
    """Apply an effect (a name from EFFECTS, or a dict of deltas) to agent's view of other."""
    rel = agent.relationship(other.id, other.name, met=met)
    changes = EFFECTS[effect] if isinstance(effect, str) else effect
    before = rel.label()

    for dimension, old, new in rel.apply(changes, event_id):
        ctx.relationship_history.append(
            (ctx.tick, agent.id, other.id, dimension, old, new, event_id)
        )

    after = rel.label()
    if after != before:
        agent._emit(
            ctx.log,
            ctx.tick,
            ev.RELATION_CHANGED,
            targets=(other.id,),
            data={"from": before, "to": after, "name": other.name},
            cause_id=event_id,
        )
    return rel


def looks_in_need(agent, resource, rules) -> bool:
    return getattr(agent, NEED_OF[resource]) >= rules.seek_threshold


def _nearest(known, agent):
    return min(
        known, key=lambda k: (agent.position.distance(k.position), k.position.x, k.position.y)
    ).position


def _unit(resource: str) -> str:
    return "a drink of water" if resource == "water" else "a meal of berries"


def _count(agent, resource, rules) -> str:
    have = agent.inventory.get(resource, 0)
    if resource == "food":
        meals = have // max(1, rules.food_per_meal)
        return f"{meals} meal{'s' if meals != 1 else ''}"
    return f"{have} drink{'s' if have != 1 else ''}"


def _can_give(ctx, giver, receiver, resource) -> bool:
    r = ctx.world.rules
    room = receiver.carry_capacity(resource, r) - receiver.inventory.get(resource, 0)
    return giver.inventory.get(resource, 0) > 0 and room > 0


def _lie_chance(ctx, speaker, listener) -> float:
    r = ctx.world.rules
    rel = speaker.relationship(listener.id, listener.name)
    disliked = rel.affinity < 0 or rel.grievance >= 0.3
    return (1 - speaker.genome.honesty) ** 2 * (
        r.lie_chance_disliked if disliked else r.lie_chance_base
    )


def _ask(ctx, asker, helper, resource, label) -> Decision | None:
    return think(ctx, "ask", asker, helper, [Option("ask", label)], resource=resource)


def _asked(asker, ask: Decision | None) -> str | None:
    return f'{asker.name} said: "{ask.say}"' if ask is not None else None
