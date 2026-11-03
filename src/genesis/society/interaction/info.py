"""Information, truthful or not: telling where things are, and asking."""

from genesis.llm.mind import Option
from genesis.llm.prompts import direction
from genesis.society.interaction.basics import (
    Act,
    _ask,
    _asked,
    _lie_chance,
    _nearest,
    feel,
    say,
    think,
)


def share_info(ctx, speaker, listener, resource, reply_to=None, lying=None, voice=None):
    """Tell the listener where a resource is. May be a lie (low honesty, disliked listener).

    lying: decided by the mind; None lets the rules decide (by honesty and feelings)."""
    known = speaker.memory.get_known_locations(resource)
    if lying is None:
        lying = ctx.rng.random() < _lie_chance(ctx, speaker, listener)

    if lying:
        position = _fake_location(ctx, resource)
    elif known:
        position = _nearest(known, speaker)
    else:
        return say(
            ctx,
            speaker,
            listener,
            Act.REPLY,
            {"answer": "don't know", "about": resource},
            reply_to=reply_to,
            voice=voice,
        )

    if position is None:  # nowhere plausible to lie about
        return say(
            ctx,
            speaker,
            listener,
            Act.REPLY,
            {"answer": "don't know", "about": resource},
            reply_to=reply_to,
            voice=voice,
        )

    event_id = say(
        ctx,
        speaker,
        listener,
        Act.SHARE_INFO,
        {"resource": resource, "x": position.x, "y": position.y},
        truthful=not lying,
        reply_to=reply_to,
        voice=voice,
    )
    learned = listener.learn_told(resource, position, speaker, event_id, ctx)
    if learned:
        feel(ctx, listener, speaker, "told_info", event_id)
    return event_id


def _fake_location(ctx, resource):
    """A walkable place where the resource is NOT (and not right next to it)."""
    world = ctx.world
    patches = {f.position for f in world.food_sources}
    candidates = []
    for p in world.terrain:  # dict order is fixed, so this is deterministic
        if not world.is_walkable(p):
            continue
        if resource == "water" and (
            world.can_drink_at(p) or any(world.can_drink_at(n) for n in p.neighbors())
        ):
            continue
        if resource == "food" and p in patches:
            continue
        candidates.append(p)
    return ctx.rng.choice(candidates) if candidates else None


def request_info(ctx, asker, helper, resource):
    ask = _ask(ctx, asker, helper, resource, f"Ask {helper.name} where to find {resource}")
    event_id = say(ctx, asker, helper, Act.REQUEST, {"info": resource}, voice=ask)

    options = []
    known = helper.memory.get_known_locations(resource)
    if known:
        where = direction(helper.position, _nearest(known, helper))
        options.append(
            Option("truth", f"Tell {asker.name} truthfully where {resource} is ({where})")
        )
    can_lie = _lie_chance(ctx, helper, asker) >= 0.05
    if can_lie:
        options.append(Option("mislead", f"Lie: send {asker.name} to a place with no {resource}"))
    if known:
        options.append(Option("refuse", "Keep it to yourself"))
    elif can_lie:
        options.append(Option("dont_know", f"Tell the truth: you don't know where {resource} is"))

    decision = None
    if len(options) > 1:
        decision = think(
            ctx, "info", helper, asker, options, heard=_asked(asker, ask), resource=resource
        )
    if decision is None:
        return share_info(ctx, helper, asker, resource, reply_to=event_id)
    if decision.choice == "refuse":
        return say(
            ctx,
            helper,
            asker,
            Act.REPLY,
            {"answer": "won't say", "about": resource},
            reply_to=event_id,
            voice=decision,
        )
    if decision.choice == "dont_know":
        return say(
            ctx,
            helper,
            asker,
            Act.REPLY,
            {"answer": "don't know", "about": resource},
            reply_to=event_id,
            voice=decision,
        )
    lying = decision.choice == "mislead"
    return share_info(ctx, helper, asker, resource, event_id, lying=lying, voice=decision)
