"""Starting a conversation, choosing what to say, and taking turns with a mind."""

from genesis.agents import talents
from genesis.culture import inventions
from genesis.llm.mind import Decision, Option, voiced
from genesis.llm.prompts import direction
from genesis.society import conflict, economy, family, groups
from genesis.society.interaction.basics import (
    RESOURCES,
    Act,
    _can_give,
    _count,
    _lie_chance,
    _nearest,
    _reject,
    _unit,
    feel,
    looks_in_need,
    say,
    think,
)
from genesis.society.interaction.gossip import _gossip_subject, gossip
from genesis.society.interaction.info import request_info, share_info
from genesis.society.interaction.items import give, request_item, threaten


def converse(ctx, speaker, listener, purpose=None):
    """One exchange: the speaker says something, the listener reacts (and maybe replies).

    purpose: None for free social time, or ("info" | "item", resource) when asking for help.
    With a mind, free-time talk can go on for a few spoken turns (llm.conversation_turns).
    """
    r = ctx.world.rules
    first_today = _first_contact_today(ctx, speaker, listener)

    if purpose is not None:
        kind, resource = purpose
        if first_today:
            _greet(ctx, speaker, listener)
        if kind == "info":
            return request_info(ctx, speaker, listener, resource)
        return request_item(ctx, speaker, listener, resource)

    if first_today:
        voice = (
            think(ctx, "greet", speaker, listener, [Option("greet", f"Greet {listener.name}")])
            if voiced(ctx, "greet", speaker, listener)
            else None
        )
        event_id = _greet(ctx, speaker, listener, voice=voice)
        _talk_on(ctx, speaker, listener, event_id, voice)
        return event_id

    if r.trade and speaker.owes:
        repaid = economy.repay_due(ctx, speaker, listener)
        if repaid is not None:
            return repaid

    decision = (
        think(ctx, "turn", speaker, listener, lambda: _options(ctx, speaker, listener))
        if voiced(ctx, "turn", speaker, listener)
        else None
    )
    if decision is not None:
        event_id = perform(ctx, speaker, listener, decision)
        _talk_on(ctx, speaker, listener, event_id, decision)
        return event_id

    act, arg = _choose_act(ctx, speaker, listener, r)
    match act:
        case Act.SHARE_INFO:
            return share_info(ctx, speaker, listener, arg)
        case Act.GIVE:
            return give(ctx, speaker, listener, arg)
        case Act.GOSSIP:
            return gossip(ctx, speaker, listener, arg)
        case Act.PRAISE:
            return praise(ctx, speaker, listener)
        case Act.INSULT:
            return insult(ctx, speaker, listener)
        case Act.THREATEN:
            return threaten(ctx, speaker, listener, arg)
        case Act.PROPOSE:
            return family.propose(ctx, speaker, listener)
        case Act.INVITE:
            return groups.ask_to_join(ctx, speaker, listener)
        case Act.ORDER:
            return groups.give_order(ctx, speaker, listener, arg)
        case Act.CHALLENGE:
            return groups.challenge(ctx, speaker, listener)
        case Act.ATTACK:
            return conflict.fight(ctx, speaker, listener)
        case Act.TEACH:
            return inventions.teach(ctx, speaker, listener, ctx.world.concepts[arg])
        case Act.TRADE:
            return economy.trade(ctx, speaker, listener, arg)
        case Act.HEAL:
            return economy.heal(ctx, speaker, listener)
        case _:
            return say(ctx, speaker, listener, Act.CHAT)


def praise(ctx, speaker, listener, reply_to=None, voice=None):
    event_id = say(ctx, speaker, listener, Act.PRAISE, reply_to=reply_to, voice=voice)
    feel(ctx, listener, speaker, "praised", event_id)
    speaker.relationship(listener.id, listener.name).last_praise_day = ctx.day
    return event_id


def insult(ctx, speaker, listener, reply_to=None, voice=None):
    event_id = say(ctx, speaker, listener, Act.INSULT, reply_to=reply_to, voice=voice)
    feel(ctx, listener, speaker, "insulted", event_id)
    speaker.relationship(listener.id, listener.name).last_insult_day = ctx.day
    return event_id


def _first_contact_today(ctx, a, b) -> bool:
    rel = a.relationship(b.id, b.name)
    return rel.last_chat_day != ctx.day


def _greet(ctx, speaker, listener, voice=None):
    event_id = say(ctx, speaker, listener, Act.GREET, voice=voice)
    for one, other in ((speaker, listener), (listener, speaker)):
        rel = feel(ctx, one, other, "chatted", event_id)
        rel.last_chat_day = ctx.day
        if ctx.world.concepts and inventions.has_item_for(other, ctx.world.concepts, "adorns"):
            feel(ctx, one, other, {"respect": 0.03}, event_id)  # M8f: they wear something fine
    return event_id


# Acts that serve a goal (M8c): need -> acts made likelier.
GOAL_ACTS = {
    "be respected": (Act.FOUND, Act.INVITE, Act.CHALLENGE, Act.ORDER),
    "be remembered": (Act.TEACH,),
    "provide": (Act.GIVE, Act.HEAL),
}


def _choose_act(ctx, speaker, listener, r):
    """Weighted choice among the acts that make sense right now."""
    g = speaker.genome
    rel = speaker.relationship(listener.id, listener.name)
    options: list[tuple[Act, object, float]] = [(Act.CHAT, None, 1.0)]

    for resource in RESOURCES:
        if not looks_in_need(listener, resource, r):
            continue
        if speaker.memory.get_known_locations(resource):
            options.append((Act.SHARE_INFO, resource, 1 + 2 * g.generosity))
        if speaker.inventory.get(resource, 0) > 0 and not looks_in_need(speaker, resource, r):
            options.append((Act.GIVE, resource, 1 + 3 * g.generosity + 2 * max(0, rel.affinity)))

    subject = _gossip_subject(speaker, listener)
    if subject is not None and rel.last_gossip_day != ctx.day:
        options.append((Act.GOSSIP, subject, 1.5 * g.sociability))

    if rel.affinity >= 0.3 and rel.last_praise_day != ctx.day:  # praise means less if daily
        options.append((Act.PRAISE, None, g.sociability * rel.affinity))
    if (rel.affinity <= -0.2 or rel.grievance >= 0.3) and rel.last_insult_day != ctx.day:
        options.append((Act.INSULT, None, 2 * g.aggression))

    if g.aggression >= r.threaten_min_aggression:
        for resource in RESOURCES:
            if looks_in_need(speaker, resource, r) and listener.inventory.get(resource, 0) > 0:
                options.append((Act.THREATEN, resource, 2 * g.aggression))

    if family.may_propose(ctx, speaker, listener):
        partners = speaker.partner == listener.id
        options.append((Act.PROPOSE, None, (8 if partners else 4) * rel.affinity))

    if groups.may_found(ctx, speaker, listener) or groups.may_invite(ctx, speaker, listener):
        options.append((Act.INVITE, None, 2 * g.ambition * rel.affinity))
    for option in groups.order_options(ctx, speaker, listener):
        options.append((Act.ORDER, option.key, 0.8 * g.ambition))
    if groups.may_challenge(ctx, speaker, listener):
        options.append((Act.CHALLENGE, None, 2 * g.ambition))
    if conflict.may_attack(ctx, speaker, listener):
        grudge = max(rel.grievance, -rel.affinity, 0.2)
        options.append((Act.ATTACK, None, 2 * (g.aggression + grudge)))

    if r.inventions and rel.affinity >= 0:
        teachable = inventions.teachable(speaker, listener, ctx.world.concepts)
        if teachable:
            want = inventions.wants_to_learn(listener, teachable[0], r)
            if r.talents and speaker.talent == "storyteller":  # M8f: their calling
                want += 1
            options.append(
                (Act.TEACH, teachable[0].id, 1 + g.generosity + g.sociability + 3 * want)
            )

    if r.trade:
        offer = economy.best_offer(ctx, speaker, listener)
        if offer is not None:
            weight = 2 + 2 * speaker.abilities.get("persuasion")
            if speaker.plan and speaker.plan["focus"] == "trade":  # M8f: their plan
                weight *= 3
            options.append((Act.TRADE, offer, weight))
    if economy.may_heal(ctx, speaker, listener):
        healer = talents.has(speaker, "healer", r)
        options.append((Act.HEAL, None, 1 + 2 * g.generosity + 3 * healer))

    need = speaker.goal["need"] if r.goals and speaker.goal else None
    if need is not None:  # a goal makes the acts that serve it likelier (M8c)
        boost = GOAL_ACTS.get(need, ())
        options = [(a, arg, w * 2.5 if a in boost else w) for a, arg, w in options]

    total = sum(w for _, _, w in options)
    pick = ctx.rng.random() * total
    for act, arg, weight in options:
        pick -= weight
        if pick < 0:
            return act, arg
    return options[-1][0], options[-1][1]


def _options(ctx, speaker, listener, reply=False) -> list[Option]:
    """Everything the speaker may do right now (the same limits as the rules, plus lying)."""
    r = ctx.world.rules
    rel = speaker.relationship(listener.id, listener.name)
    name = listener.name
    options = [Option("chat", "Just answer" if reply else "Just talk")]

    for resource in RESOURCES:
        if not looks_in_need(listener, resource, r):
            continue
        known = speaker.memory.get_known_locations(resource)
        if known:
            where = _nearest(known, speaker)
            options.append(
                Option(
                    f"share_info:{resource}",
                    f"Tell {name} truthfully where {resource} is "
                    f"({direction(speaker.position, where)})",
                )
            )
        if _lie_chance(ctx, speaker, listener) >= 0.05:
            options.append(
                Option(f"mislead:{resource}", f"Mislead {name} about where {resource} is")
            )
        if _can_give(ctx, speaker, listener, resource) and not looks_in_need(speaker, resource, r):
            options.append(
                Option(
                    f"give:{resource}",
                    f"Give {name} {_unit(resource)} (you carry {_count(speaker, resource, r)})",
                )
            )

    subject = _gossip_subject(speaker, listener)
    if subject is not None and rel.last_gossip_day != ctx.day:
        about = speaker.relationships[subject].target_name
        options.append(Option(f"gossip:{subject}", f"Tell {name} what you think of {about}"))
    if rel.last_praise_day != ctx.day:
        options.append(Option("praise", f"Praise {name}"))
    if rel.last_insult_day != ctx.day:
        options.append(Option("insult", f"Insult {name}"))

    if speaker.genome.aggression >= r.threaten_min_aggression:
        for resource in RESOURCES:
            if looks_in_need(speaker, resource, r) and listener.inventory.get(resource, 0) > 0:
                options.append(
                    Option(
                        f"threaten:{resource}",
                        f"Threaten {name} to make them hand over their {resource}",
                    )
                )
    if family.may_propose(ctx, speaker, listener):
        options.append(Option("propose", f"Ask {name} to have a child with you"))
    g = groups.group_of(ctx, speaker)
    if groups.may_invite(ctx, speaker, listener):
        options.append(Option("group", f"Ask {name} to join {g.name}"))  # type: ignore[union-attr]
    elif groups.may_found(ctx, speaker, listener):
        options.append(Option("group", f"Ask {name} to stand together with you as a group"))
    options.extend(groups.order_options(ctx, speaker, listener))
    if groups.may_challenge(ctx, speaker, listener):
        lead_of = g.name  # type: ignore[union-attr]
        options.append(Option("challenge", f"Challenge {name} for the lead of {lead_of}"))
    if conflict.may_attack(ctx, speaker, listener):
        options.append(Option("attack", f"Attack {name}"))
    if r.inventions:
        for concept in inventions.teachable(speaker, listener, ctx.world.concepts)[:2]:
            effect = inventions.effect_of(concept.function, r)
            options.append(
                Option(
                    f"teach:{concept.id}", f"Show {name} how to make a {concept.name} ({effect})"
                )
            )
    if r.trade and not reply:
        options.extend(economy.offer_options(ctx, speaker, listener))
    if economy.may_heal(ctx, speaker, listener):
        options.append(Option("heal", f"Tend {name}'s wounds"))
    return options


def perform(ctx, speaker, listener, decision: Decision, reply_to=None):
    """Carry out a chosen option. Returns the event id of what was said."""
    act, _, arg = decision.choice.partition(":")
    match act:
        case "greet":
            return _greet(ctx, speaker, listener, voice=decision)
        case "share_info" | "mislead":
            return share_info(
                ctx, speaker, listener, arg, reply_to, lying=act == "mislead", voice=decision
            )
        case "give":
            event_id = give(ctx, speaker, listener, arg, reply_to=reply_to, voice=decision)
            if event_id is not None:
                return event_id
            _reject(decision, f"could not give {arg}")
        case "gossip":
            if arg.isdigit() and int(arg) in speaker.relationships:
                return gossip(ctx, speaker, listener, int(arg), reply_to, voice=decision)
            _reject(decision, "no such person to gossip about")
        case "praise":
            return praise(ctx, speaker, listener, reply_to, voice=decision)
        case "insult":
            return insult(ctx, speaker, listener, reply_to, voice=decision)
        case "threaten":
            return threaten(ctx, speaker, listener, arg, reply_to, voice=decision)
        case "propose":
            if family.may_propose(ctx, speaker, listener):
                return family.propose(ctx, speaker, listener, reply_to, voice=decision)
            _reject(decision, "they may not have a child together now")
        case "group":
            if groups.may_invite(ctx, speaker, listener) or groups.may_found(
                ctx, speaker, listener
            ):
                return groups.ask_to_join(ctx, speaker, listener, reply_to, voice=decision)
            _reject(decision, "they can't join now")
        case "order":
            keys = {o.key for o in groups.order_options(ctx, speaker, listener)}
            if decision.choice in keys:
                return groups.give_order(
                    ctx, speaker, listener, decision.choice, reply_to, decision
                )
            _reject(decision, "not their leader, or already given an order")
        case "challenge":
            if groups.may_challenge(ctx, speaker, listener):
                return groups.challenge(ctx, speaker, listener, reply_to, voice=decision)
            _reject(decision, "can't challenge now")
        case "attack":
            if conflict.may_attack(ctx, speaker, listener):
                return conflict.fight(ctx, speaker, listener, reply_to, voice=decision)
            _reject(decision, "no reason to fight now")
        case "teach":
            concept = ctx.world.concepts.get(int(arg)) if arg.isdigit() else None
            if concept is not None and concept in inventions.teachable(
                speaker, listener, ctx.world.concepts
            ):
                return inventions.teach(ctx, speaker, listener, concept, reply_to, decision)
            _reject(decision, "nothing to teach")
        case "trade" | "promise":
            offer = economy.best_offer(ctx, speaker, listener) if ctx.world.rules.trade else None
            if offer is not None and offer.key == decision.choice:
                return economy.trade(ctx, speaker, listener, offer, reply_to, decision)
            _reject(decision, "that trade is not on offer now")
        case "heal":
            if economy.may_heal(ctx, speaker, listener):
                return economy.heal(ctx, speaker, listener, reply_to, decision)
            _reject(decision, "no wounds to tend")
        case "chat":
            return say(ctx, speaker, listener, Act.CHAT, reply_to=reply_to, voice=decision)
        case _:
            _reject(decision, f"unknown choice {decision.choice!r}")
    return say(ctx, speaker, listener, Act.CHAT, reply_to=reply_to)


def _talk_on(ctx, speaker, listener, event_id, decision):
    """Take turns while both speak in their own words, up to llm.conversation_turns."""
    turns = 1
    while decision is not None and turns < ctx.mind.conversation_turns:
        if decision.choice.startswith(
            ("threaten", "propose", "group", "order", "challenge", "attack", "trade", "promise")
        ):  # already answered, or no longer a conversation
            return
        heard = _heard(speaker, decision)
        speaker, listener = listener, speaker
        decision = think(
            ctx,
            "reply",
            speaker,
            listener,
            lambda s=speaker, li=listener: _options(ctx, s, li, reply=True),
            heard=heard,
        )
        if decision is None:
            return
        event_id = perform(ctx, speaker, listener, decision, reply_to=event_id)
        turns += 1


def _heard(speaker, decision: Decision) -> str:
    """What the listener noticed: the act (as they perceive it) and the words."""
    act, _, arg = decision.choice.partition(":")
    name = speaker.name
    did = {
        "give": f"{name} gave you {_unit(arg)}.",
        "share_info": f"{name} told you where {arg} is.",
        "mislead": f"{name} told you where {arg} is.",  # you can't tell it's a lie
        "praise": f"{name} praised you.",
        "insult": f"{name} insulted you.",
        "gossip": f"{name} told you about someone.",
        "propose": f"{name} asked you to have a child together.",
        "group": f"{name} asked you to join them.",
        "challenge": f"{name} challenged your leadership.",
        "attack": f"{name} attacked you.",
        "teach": f"{name} showed you how to make something.",
    }.get(act, "")
    return f'{did} {name} said: "{decision.say}"'.strip()
