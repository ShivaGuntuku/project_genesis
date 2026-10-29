"""Talking, giving and gossiping between two adjacent agents. See docs/04-society.md (4.1).

Every message is a structured speech act. The simulation acts on the act and its payload.

From M4 a mind (the local LLM) may decide at *decision points*: it chooses one of the options
built here (all valid right now) and says it in its own words. If there is no mind, or it
fails, or the budget is spent, the rules below decide, exactly as in M3.

All checks happen here, so invalid acts can't change the world:
- you can only GIVE what you carry;
- a truthful SHARE_INFO must come from your own memory (otherwise it's a lie, marked as such).
"""

from enum import StrEnum

from genesis.agents import talents
from genesis.culture import inventions
from genesis.history import events as ev
from genesis.llm.mind import REJECTED, Decision, Option, Point, voiced
from genesis.llm.prompts import direction
from genesis.society import conflict, economy, family, groups
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


# =================================================================
# Basics: saying something, and feeling something about someone
# =================================================================


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


# =================================================================
# Starting a conversation
# =================================================================


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


# =================================================================
# Talking with a mind: options, acting on a choice, and taking turns
# =================================================================


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
        options.append(Option("group", f"Ask {name} to join {g.name}"))
    elif groups.may_found(ctx, speaker, listener):
        options.append(Option("group", f"Ask {name} to stand together with you as a group"))
    options.extend(groups.order_options(ctx, speaker, listener))
    if groups.may_challenge(ctx, speaker, listener):
        options.append(Option("challenge", f"Challenge {name} for the lead of {g.name}"))
    if conflict.may_attack(ctx, speaker, listener):
        options.append(Option("attack", f"Attack {name}"))
    if r.inventions:
        for concept in inventions.teachable(speaker, listener, ctx.world.concepts)[:2]:
            from genesis.culture.materials import FUNCTIONS

            effect = FUNCTIONS[concept.function]["effect"]
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


# =================================================================
# Information (truthful or not)
# =================================================================


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


def _ask(ctx, asker, helper, resource, label) -> Decision | None:
    return think(ctx, "ask", asker, helper, [Option("ask", label)], resource=resource)


def _asked(asker, ask: Decision | None) -> str | None:
    return f'{asker.name} said: "{ask.say}"' if ask is not None else None


# =================================================================
# Items
# =================================================================


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
        willing = ctx.rng.random() < willingness
    if willing:
        given = give(ctx, helper, asker, resource, reply_to=event_id, voice=decision)
        if given is not None:
            return given
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


# =================================================================
# Gossip
# =================================================================


def _opinion(rel: Relationship) -> float:
    """How good (+) or bad (-) the speaker thinks someone is."""
    return rel.affinity + 0.5 * rel.gratitude - 0.7 * rel.grievance


def _gossip_subject(speaker, listener):
    """The third person the speaker feels most strongly about (if strongly enough)."""
    best, strength = None, 0.3
    for target_id in sorted(speaker.relationships):
        if target_id in (listener.id, speaker.id):
            continue
        opinion = _opinion(speaker.relationships[target_id])
        if abs(opinion) >= strength:
            best, strength = target_id, abs(opinion)
    return best


def gossip(ctx, speaker, listener, subject_id, reply_to=None, voice=None):
    """Tell the listener what you think of someone. The listener believes it as much as they
    trust you, and may now like or distrust someone they have never met."""
    subject_rel = speaker.relationships[subject_id]
    opinion = _opinion(subject_rel)
    sign = 1 if opinion > 0 else -1
    claim = (
        "is kind"
        if sign > 0
        else ("can't be trusted" if subject_rel.grievance >= 0.3 else "is unpleasant")
    )
    event_id = say(
        ctx,
        speaker,
        listener,
        Act.GOSSIP,
        {
            "about": subject_id,
            "about_name": subject_rel.target_name,
            "claim": claim,
            "sentiment": sign,
        },
        reply_to=reply_to,
        voice=voice,
    )

    speaker.relationship(listener.id, listener.name).last_gossip_day = ctx.day
    belief = listener.relationship(speaker.id, speaker.name).trust
    subject = ctx.agents.get(subject_id)
    if subject is not None:
        effect = ctx.world.rules.gossip_effect
        changes = {"affinity": effect * sign * belief, "trust": 0.7 * effect * sign * belief}
        feel(ctx, listener, subject, changes, event_id, met=listener.memory.knows_agent(subject_id))
    return event_id
