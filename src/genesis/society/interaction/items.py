"""Items: giving, asking for things, and threats."""

from genesis.llm.mind import Option
from genesis.society import conflict, family
from genesis.society.interaction.basics import (
    NEED_OF,
    Act,
    _ask,
    _asked,
    _can_give,
    _count,
    _reject,
    _unit,
    feel,
    looks_in_need,
    say,
    think,
)


def give(ctx, giver, receiver, resource, reply_to=None, under_threat=False, voice=None):
    """Hand over carried food (one meal) or water (one drink). Returns the event id, or None
    if nothing could be given (the giver carries none, or the receiver can carry no more)."""
    r = ctx.world.rules
    have = giver.inventory.get(resource, 0)
    if have <= 0:
        return None

    unit = r.food_per_meal if resource == "food" else 1
    room = receiver.carry_capacity(resource, r) - receiver.inventory.get(resource, 0)
    amount = min(have, unit, room)
    if amount <= 0:
        return None

    giver.inventory[resource] = have - amount
    receiver.inventory[resource] = receiver.inventory.get(resource, 0) + amount

    event_id = say(
        ctx,
        giver,
        receiver,
        Act.GIVE,
        {"resource": resource, "amount": amount, "under_threat": under_threat},
        reply_to=reply_to,
        voice=voice,
    )
    if under_threat:
        feel(ctx, giver, receiver, "robbed", event_id)
    else:
        in_need = looks_in_need(receiver, resource, r)
        feel(ctx, receiver, giver, "gift_in_need" if in_need else "gift", event_id)
        feel(ctx, giver, receiver, "gave", event_id)
    return event_id


def request_item(ctx, asker, helper, resource):
    r = ctx.world.rules
    ask = _ask(ctx, asker, helper, resource, f"Ask {helper.name} for {_unit(resource)}")
    event_id = say(ctx, asker, helper, Act.REQUEST, {"item": resource}, voice=ask)

    if helper.inventory.get(resource, 0) <= 0:
        return say(
            ctx,
            helper,
            asker,
            Act.REPLY,
            {"answer": "have none", "about": resource},
            reply_to=event_id,
        )

    decision = None
    if _can_give(ctx, helper, asker, resource):
        options = [
            Option(
                "give",
                f"Give {asker.name} {_unit(resource)} (you carry {_count(helper, resource, r)})",
            ),
            Option("refuse", "Refuse"),
        ]
        decision = think(
            ctx, "request", helper, asker, options, heard=_asked(asker, ask), resource=resource
        )

    if decision is not None:
        willing = decision.choice == "give"
    else:
        rel = helper.relationship(asker.id, asker.name)
        asker_urgent = getattr(asker, NEED_OF[resource]) >= r.urgent_threshold
        willingness = (
            0.2
            + 0.5 * helper.genome.generosity
            + 0.3 * rel.affinity
            + 0.3 * rel.gratitude
            - 0.3 * looks_in_need(helper, resource, r)
            + 0.2 * asker_urgent
            + 0.4 * family.close_kin(helper, asker)  # family looks after its own
        )
        if r.culture:  # S2.4: their group's way of sharing
            from genesis.culture import ideas

            willingness += 0.25 * ideas.sharing_norm(helper, ctx.world)
        willing = ctx.rng.random() < willingness
    if willing:
        given = give(ctx, helper, asker, resource, reply_to=event_id, voice=decision)
        if given is not None:
            if r.culture:
                from genesis.culture import ideas

                ideas.did(ctx, helper, "share")
            return given
    elif r.culture and helper.inventory.get(resource, 0) > 0:
        from genesis.culture import ideas

        ideas.broke_norm(ctx, helper, asker, resource)
        if decision is not None:
            _reject(decision, f"could not give {resource}")
            decision = None

    refusal = say(
        ctx,
        helper,
        asker,
        Act.REPLY,
        {"answer": "no", "about": resource},
        reply_to=event_id,
        voice=decision,
    )
    in_need = looks_in_need(asker, resource, r)
    feel(ctx, asker, helper, "refused_in_need" if in_need else "refused", refusal)
    return refusal


def threaten(ctx, bully, victim, resource, reply_to=None, voice=None):
    event_id = say(
        ctx, bully, victim, Act.THREATEN, {"demand": resource}, reply_to=reply_to, voice=voice
    )
    feel(ctx, victim, bully, "threatened", event_id)

    decision = None
    if _can_give(ctx, victim, bully, resource):
        stronger = bully.genome.strength - victim.genome.strength
        build = "stronger" if stronger > 0.1 else "weaker" if stronger < -0.1 else "about as strong"
        heard = f"{bully.name} is {build} than you."
        if voice is not None:
            heard = f'{bully.name} said: "{voice.say}" ' + heard
        options = [
            Option("comply", f"Hand over {_unit(resource)}"),
            Option("refuse", "Refuse and stand your ground"),
        ]
        decision = think(ctx, "threat", victim, bully, options, heard=heard, resource=resource)

    if decision is not None:
        comply = decision.choice == "comply"
    else:
        fear = victim.relationship(bully.id, bully.name).fear
        strength_gap = bully.genome.strength - victim.genome.strength
        comply = ctx.rng.random() < 0.2 + fear + 0.5 * strength_gap
    if comply:
        given = give(
            ctx, victim, bully, resource, reply_to=event_id, under_threat=True, voice=decision
        )
        if given is not None:
            return given
        if decision is not None:
            _reject(decision, f"could not hand over {resource}")
            decision = None

    refusal = say(
        ctx,
        victim,
        bully,
        Act.REPLY,
        {"answer": "refuse", "about": resource},
        reply_to=event_id,
        voice=decision,
    )
    feel(ctx, bully, victim, "defied", refusal)
    return _escalate(ctx, bully, victim, refusal, decision) or refusal


def _escalate(ctx, bully, victim, refusal, refused_with):
    """After a refused threat the bully may back down or fight (M7)."""
    if not conflict.may_attack(ctx, bully, victim):
        return None
    heard = f"{victim.name} refused you."
    if refused_with is not None:
        heard = f'{victim.name} refused: "{refused_with.say}"'
    options = [Option("attack", f"Attack {victim.name}"), Option("back_down", "Back down")]
    decision = think(ctx, "fight", bully, victim, options, heard=heard)
    if decision is not None:
        attack = decision.choice == "attack"
    else:
        attack = ctx.rng.random() < 0.6 * bully.genome.aggression
    if not attack:
        return None
    return conflict.fight(ctx, bully, victim, reply_to=refusal, voice=decision)
