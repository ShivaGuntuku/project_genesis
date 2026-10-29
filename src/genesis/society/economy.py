"""Trade, promises and money of their own (M8d). See docs/15 (15.4).

Nothing called money exists here. Agents only:
- **barter**: offer some of what they carry for something the other carries;
- **promise**: take something now and promise to bring the same back within thirty days;
- **judge** each good by its use to them now, and by how often they have seen it accepted
  (its *saleability*, which each agent believes from the trades it took part in or saw).

Accepting a good mostly to pass it on later is an **indirect exchange**. When one good makes up
most of a valley's indirect exchanges over a season, a **currency** has emerged: its users name
it with a word of their own, and from then on prices in it are recorded.
"""

import json
import math
from dataclasses import dataclass

from genesis.culture import inventions as inv
from genesis.culture.materials import MATERIALS, SOLVES
from genesis.culture.words import is_valley_word
from genesis.history import events as ev

WORTH = 1.0  # what a good everyone takes is worth, on top of its use
PRIOR = (1, 4)  # before seeing any trade: accepted 1 time in 4
MAX_LOTS = 4
SEE_DISTANCE = 3


@dataclass(frozen=True)
class Offer:
    give: str | None  # what the speaker gives (None: a promise)
    n: int  # how many lots of it
    get: str  # what the speaker wants (one lot)

    @property
    def promise(self) -> bool:
        return self.give is None

    @property
    def key(self) -> str:
        return f"promise:{self.get}" if self.promise else f"trade:{self.n}:{self.give}:{self.get}"


# =================================================================
# Goods
# =================================================================


def pretty(good: str) -> bool:
    return good in MATERIALS and MATERIALS[good]["shiny"] >= 0.7


def holdings(agent, rules) -> dict[str, int]:
    """Lots each good can be handed over in: meals of food, drinks, pieces, made things."""
    out = {}
    meals = agent.inventory.get("food", 0) // max(1, rules.food_per_meal)
    if meals:
        out["food"] = meals
    if agent.inventory.get("water", 0):
        out["water"] = agent.inventory["water"]
    for m, n in agent.materials.items():
        if n > 0:
            out[m] = n
    for item in agent.items:
        key = f"item:{item['concept']}"
        out[key] = out.get(key, 0) + 1
    return out


def room_for(agent, good: str, n: int, rules) -> bool:
    if good == "food":
        return agent.inventory.get("food", 0) + n * rules.food_per_meal <= agent.carry_capacity(
            "food", rules
        )
    if good == "water":
        return agent.inventory.get("water", 0) + n <= agent.carry_capacity("water", rules)
    if good in MATERIALS:
        return inv.carrying_materials(agent) + n <= rules.materials_capacity
    return True


def move(giver, receiver, good: str, n: int, ctx):
    r = ctx.world.rules
    if good in ("food", "water"):
        units = n * (r.food_per_meal if good == "food" else 1)
        giver.inventory[good] -= units
        receiver.inventory[good] = receiver.inventory.get(good, 0) + units
    elif good in MATERIALS:
        giver.materials[good] -= n
        if giver.materials[good] <= 0:
            del giver.materials[good]
        receiver.materials[good] = receiver.materials.get(good, 0) + n
    else:
        cid = int(good.split(":")[1])
        for _ in range(n):
            item = next(i for i in giver.items if i["concept"] == cid)
            giver.items.remove(item)
            receiver.items.append(item)
        inv.refresh_bonus(giver, ctx.world.concepts)
        inv.refresh_bonus(receiver, ctx.world.concepts)


def label(good: str, concepts, n: int = 1) -> str:
    if good == "food":
        return f"{n} meal{'s' if n != 1 else ''} of berries"
    if good == "water":
        return f"{n} drink{'s' if n != 1 else ''} of water"
    if good.startswith("item:"):
        c = concepts.get(int(good.split(":")[1]))
        name = c.name if c else "thing"
        return f"{n} {name}" + (f" (it {c.function})" if c else "")
    return f"{n} {good}"


# =================================================================
# Value: use now, and what others will take it for
# =================================================================


def saleability(agent, good: str) -> float:
    accepted, seen = agent.market.get(good, (0, 0))
    return (accepted + PRIOR[0]) / (seen + PRIOR[1])


def use_value(agent, good: str, ctx) -> float:
    """What one more lot is worth to this agent for its own use (about 0..2.5)."""
    r, concepts = ctx.world.rules, ctx.world.concepts
    have = holdings(agent, r).get(good, 0)
    if good == "food":
        return max(0.05, 0.3 + 2.0 * agent.hunger / 100 - 0.25 * have)
    if good == "water":
        return max(0.05, 0.3 + 2.0 * agent.thirst / 100 - 0.4 * have)
    if good in MATERIALS:
        v = 0.05
        if pretty(good):  # people like to have pretty things (the ambitious more)
            v = max(v, 0.2 + 0.6 * agent.genome.ambition - 0.2 * have)
        project = agent.project or {}
        if project.get("material") == good:
            v = max(v, 1.5)
        for cid in agent.knowledge:  # a material for something they know how to make
            c = concepts.get(cid)
            if c and good in c.recipe[0] and not inv.has_item_for(agent, concepts, c.function):
                v = max(v, 1.2 if have == 0 else 0.4)
        if have == 0 and inv.main_problem(agent, r) is not None:
            v = max(v, 0.4)  # anything to tinker with
        return v
    cid = int(good.split(":")[1])
    c = concepts.get(cid)
    if c is None:
        return 0.05
    if inv.has_item_for(agent, concepts, c.function):
        return 0.15
    wanted = any(SOLVES.get(p) == c.function for p in agent.problems)
    return 2.0 if wanted else 0.8


def value(agent, good: str, ctx) -> float:
    """Worth to them: the best of using it and passing it on."""
    return max(use_value(agent, good, ctx), WORTH * saleability(agent, good))


# =================================================================
# Offers
# =================================================================


def _guess(speaker, listener, good, ctx) -> float:
    """What the speaker thinks one lot is worth to the listener: needs they can see, and how
    often the speaker has seen it taken."""
    r = ctx.world.rules
    seen_need = 0.0
    if good == "food" and listener.hunger >= r.seek_threshold:
        seen_need = 1.5
    elif good == "water" and listener.thirst >= r.seek_threshold:
        seen_need = 1.5
    elif pretty(good):
        seen_need = 0.3
    return max(seen_need, WORTH * saleability(speaker, good))


def best_offer(ctx, speaker, listener) -> Offer | None:
    """The trade the speaker would propose to this listener now, if any."""
    r = ctx.world.rules
    theirs, mine = holdings(listener, r), holdings(speaker, r)
    wants = sorted(
        (
            (-use_value(speaker, g, ctx), g)
            for g in theirs
            if room_for(speaker, g, 1, r) and not (g in ("food", "water") and theirs[g] <= 1)
        ),
    )
    for neg_use, want in wants[:2]:
        use = -neg_use
        if use < r.trade_min_want:
            break
        best = None
        for g in sorted(mine):
            if g == want or not room_for(listener, g, 1, r):
                continue
            guess = _guess(speaker, listener, g, ctx)
            n = max(1, math.ceil(0.5 / max(0.05, guess)))
            if n > MAX_LOTS or n > mine[g] or not room_for(listener, g, n, r):
                continue
            cost = n * value(speaker, g, ctx)
            if cost >= use:
                continue
            key = (use - cost, guess, g)
            if best is None or key > best[0]:
                best = (key, Offer(g, n, want))
        if best is not None:
            return best[1]
        rel = listener.relationships.get(speaker.id)
        if use >= 1.5 and rel is not None and rel.trust >= r.promise_trust:
            if not any(d["to"] == listener.id for d in speaker.owes):
                return Offer(None, 0, want)
    return None


def accepts(ctx, listener, speaker, offer: Offer) -> bool:
    """The listener's answer by the rules: is what they get worth more than what they give?"""
    r = ctx.world.rules
    rel = listener.relationship(speaker.id, speaker.name)
    if rel.affinity < -0.3:
        return False
    cost = value(listener, offer.get, ctx)
    if offer.promise:
        if rel.trust < r.promise_trust or cost >= 0.8:
            return False
        return ctx.rng.random() < rel.trust + 0.3 * listener.genome.generosity
    return offer.n * value(listener, offer.give, ctx) >= cost + 0.05


def feasible(ctx, speaker, listener, offer: Offer) -> bool:
    r = ctx.world.rules
    theirs, mine = holdings(listener, r), holdings(speaker, r)
    if theirs.get(offer.get, 0) < 1 or not room_for(speaker, offer.get, 1, r):
        return False
    if offer.promise:
        return True
    return mine.get(offer.give, 0) >= offer.n and room_for(listener, offer.give, offer.n, r)


def offer_options(ctx, speaker, listener) -> list:
    """For the LLM: the offer the speaker could make (one, the rules' best)."""
    from genesis.llm.mind import Option

    offer = best_offer(ctx, speaker, listener)
    if offer is None:
        return []
    c = ctx.world.concepts
    if offer.promise:
        text = (
            f"Ask {listener.name} for {label(offer.get, c)} now, promising to bring the same "
            "back within thirty days"
        )
    else:
        text = f"Offer {listener.name} {label(offer.give, c, offer.n)} for {label(offer.get, c)}"
    return [Option(offer.key, text)]


# =================================================================
# Trading
# =================================================================


def region_of(world, position) -> str:
    if not world.regions:
        return "valley"
    return min(world.regions, key=lambda s: abs(s[1] - position.x) + abs(s[2] - position.y))[0]


def currency_in(world, region: str) -> dict | None:
    return next((c for c in world.economy["currencies"] if c["region"] == region), None)


def trade(ctx, speaker, listener, offer: Offer, reply_to=None, voice=None):
    """Propose a trade; the listener accepts or refuses. Returns the last event id."""
    from genesis.llm.mind import Option
    from genesis.society.interaction import Act, feel, say, think

    r, concepts = ctx.world.rules, ctx.world.concepts
    payload = {
        "give": offer.give,
        "n": offer.n,
        "get": offer.get,
        "promise": offer.promise,
        "gives": label(offer.give, concepts, offer.n) if offer.give else None,
        "gets": label(offer.get, concepts),
    }
    event_id = say(ctx, speaker, listener, Act.TRADE, payload, reply_to=reply_to, voice=voice)

    wanted = label(offer.get, concepts)
    asked = (
        f"{speaker.name} asks for {wanted} now and promises to bring the same back "
        "within thirty days."
        if offer.promise
        else f"{speaker.name} offers you {label(offer.give, concepts, offer.n)} for {wanted}."
    )
    heard = asked + (f' {speaker.name} said: "{voice.say}"' if voice is not None else "")
    decision = think(
        ctx,
        "trade",
        listener,
        speaker,
        [Option("accept", "Accept"), Option("refuse", "Refuse")],
        heard=heard,
    )
    accepted = decision.choice == "accept" if decision is not None else None
    if accepted is None:
        accepted = accepts(ctx, listener, speaker, offer)
    accepted = accepted and feasible(ctx, speaker, listener, offer)

    region = region_of(ctx.world, speaker.position)
    indirect = False
    price = None
    if accepted and not offer.promise:
        # Taken mostly to pass on: its use to them is less than what they gave up for it.
        indirect = offer.n * use_value(listener, offer.give, ctx) < use_value(
            listener, offer.get, ctx
        )
        money = currency_in(ctx.world, region)
        if money is not None and money["good"] in (offer.give, offer.get):
            bought = offer.get if money["good"] == offer.give else offer.give
            paid = offer.n if money["good"] == offer.give else 1 / offer.n
            price = {"currency": money["id"], "good": bought, "amount": round(paid, 3)}

    reply = {
        "answer": "accept_trade" if accepted else "refuse_trade",
        "about": offer.get,
        **payload,
        "indirect": indirect,
        "region": region,
    }
    if price:
        reply["price"] = price
    reply_id = say(ctx, listener, speaker, Act.REPLY, reply, reply_to=event_id, voice=decision)

    if accepted:
        move(listener, speaker, offer.get, 1, ctx)
        if offer.promise:
            due = ctx.tick + ctx.world.calendar.ticks(days=r.promise_days)
            speaker.owes.append(
                {"to": listener.id, "good": offer.get, "due": due, "event": reply_id}
            )
        else:
            move(speaker, listener, offer.give, offer.n, ctx)
            if indirect:
                ctx.world.economy["indirect"].append([ctx.tick, region, offer.give, listener.id])
        speaker._practice("persuasion", 0.5, ctx.world, ctx.tick, ctx.log)
        feel(ctx, listener, speaker, "traded", reply_id)
        feel(ctx, speaker, listener, "gift" if offer.promise else "traded", reply_id)
    _observe(ctx, speaker, listener, offer, accepted)
    return reply_id


def _observe(ctx, speaker, listener, offer: Offer, accepted: bool):
    """Everyone who took part or stood near remembers what was wanted and what was taken."""
    for a in ctx.agents.values():
        if not a.alive or a.sleeping:
            continue
        if a is not speaker and a is not listener:
            if a.position.distance(speaker.position) > SEE_DISTANCE:
                continue
        _seen(a, offer.get, True)  # someone wanted it
        if not offer.promise:
            _seen(a, offer.give, accepted)


def _seen(agent, good, taken: bool):
    accepted, seen = agent.market.get(good, (0, 0))
    agent.market[good] = [accepted + int(taken), seen + 1]


# =================================================================
# Promises
# =================================================================


def repay_due(ctx, debtor, creditor):
    """A debtor meeting their creditor pays back, if they can spare it. Returns the event id."""
    from genesis.society.interaction import Act, feel, say

    r = ctx.world.rules
    for debt in list(debtor.owes):
        if debt["to"] != creditor.id:
            continue
        good = debt["good"]
        if holdings(debtor, r).get(good, 0) < 1 or not room_for(creditor, good, 1, r):
            continue
        if use_value(debtor, good, ctx) >= 1.5:
            continue  # they need it badly themselves; later
        move(debtor, creditor, good, 1, ctx)
        debtor.owes.remove(debt)
        what = label(good, ctx.world.concepts)
        event_id = say(
            ctx, debtor, creditor, Act.REPAY, {"good": good, "what": what}, cause_id=debt["event"]
        )
        feel(ctx, creditor, debtor, "promise_kept", event_id)
        return event_id
    return None


def nightly(ctx, sim):
    """Broken promises, the season's indirect exchanges, and whether money has emerged."""
    r, world = ctx.world.rules, ctx.world
    agents = ctx.agents
    from genesis.society.interaction import feel

    for debtor in sim.population.get_alive():
        for debt in list(debtor.owes):
            creditor = agents.get(debt["to"])
            if creditor is None or not creditor.alive:
                debtor.owes.remove(debt)  # the debt died with them
                continue
            if ctx.tick < debt["due"]:
                continue
            debtor.owes.remove(debt)
            event_id = ctx.log.emit(
                ctx.tick,
                ev.PROMISE_BROKEN,
                actors=(debtor.id,),
                targets=(creditor.id,),
                data={"good": debt["good"]},
                cause_id=debt["event"],
            )
            feel(ctx, creditor, debtor, "promise_broken", event_id)

    if ctx.tick < world.calendar.ticks(days=r.currency_after_days):
        return  # M8f: too soon for money to mean anything
    window = world.calendar.ticks(days=r.currency_window_days)
    recent = [x for x in world.economy["indirect"] if ctx.tick - x[0] < window]
    world.economy["indirect"] = recent
    by_region: dict[str, list] = {}
    for _tick, region, good, *who in recent:
        by_region.setdefault(region, []).append((good, who[0] if who else None))
    living: dict[str, int] = {}
    for a in sim.population.get_alive():
        here = region_of(world, a.position)
        living[here] = living.get(here, 0) + 1
    for region, taken in sorted(by_region.items()):
        # Review fix: the thresholds scale with the valley: 7 people can't make 20 pass-on
        # trades by 5 people in a season (none emerged in 40 years of a small valley).
        people = living.get(region, 0)
        need = max(8, min(r.currency_min_indirect, 2 * people))
        users_needed = max(3, min(r.currency_min_users, people // 3))
        if currency_in(world, region) is not None or len(taken) < need:
            continue
        goods = [g for g, _ in taken]
        counts = {g: goods.count(g) for g in sorted(set(goods))}
        good = max(counts, key=counts.get)
        users = {who for g, who in taken if g == good}
        if counts[good] / len(goods) >= r.currency_share and len(users) >= users_needed:
            _emerge(ctx, sim, region, good, counts[good], len(goods))


def _emerge(ctx, sim, region, good, uses, total):
    world = ctx.world
    users = sorted(
        (a for a in sim.population.get_alive() if region_of(world, a.position) == region),
        key=lambda a: (-a.market.get(good, (0, 0))[0], a.id),
    )
    namer = users[0] if users else None
    taken = {c["name"].lower() for c in world.economy["currencies"]}
    taken |= {c.name.lower() for c in world.concepts.values()}
    taken |= {a.name.lower() for a in ctx.agents.values()}
    name = _llm_name(ctx, namer, good, taken) if namer is not None else None
    if name is None:
        while True:
            name = "".join(ctx.rng.choice(inv.SYLLABLES) for _ in range(ctx.rng.choice((2, 3))))
            if name not in taken and is_valley_word(name, taken):
                break
    cid = len(world.economy["currencies"]) + 1
    money = {
        "id": cid,
        "name": name.capitalize(),
        "good": good,
        "region": region,
        "tick": ctx.tick,
        "namer": namer.id if namer else None,
        "share": round(uses / total, 3),
    }
    world.economy["currencies"].append(money)
    ctx.log.emit(
        ctx.tick,
        ev.CURRENCY_EMERGED,
        actors=tuple(a.id for a in users[:3]),
        data={**money, "uses": uses, "indirect": total},
    )


def _llm_name(ctx, agent, good, taken):
    from genesis.llm import persona
    from genesis.llm.mind import Question

    mind = ctx.mind
    if mind is None or not getattr(mind, "active", False):
        return None
    what = label(good, ctx.world.concepts).removeprefix("1 ")

    def prompt():
        return "\n".join(
            [
                persona.fact_block(agent, ctx),
                "",
                f"Lately people around you take {what} in trade even when they have no use for "
                "it, because everyone else will take it later for what they need.",
                f"What do you call {what} when it is used like that? One new word of your own "
                "(3 to 12 letters), not a word anyone here already uses.",
                'Answer in JSON: "thought", "name".',
            ]
        )

    def parse(text, record):
        try:
            name = str(json.loads(text).get("name", "")).strip()
        except (ValueError, TypeError, AttributeError):
            name = ""
        if not is_valley_word(name, taken):
            if record is not None:
                record["outcome"] = "invalid"
                record["error"] = f"{name!r} is not a new word of the valley"
            return None
        if record is not None:
            record.update(choice=name)
        return name

    question = Question(
        kind="name_currency",
        agent=agent,
        other=None,
        prompt=prompt,
        schema={
            "type": "object",
            "properties": {"thought": {"type": "string"}, "name": {"type": "string"}},
            "required": ["thought", "name"],
        },
        parse=parse,
        max_tokens=60,
        budgeted=False,
    )
    return mind.ask(ctx, ctx.next_point(), question)


# =================================================================
# Healing (a talent's work, but anyone can tend a wound)
# =================================================================


def may_heal(ctx, healer, patient) -> bool:
    r = ctx.world.rules
    hurt = patient.health < r.tend_below or patient.sick_until is not None
    if not r.talents or not hurt or healer.stage == "child":
        return False
    rel = healer.relationship(patient.id, patient.name)
    return rel.affinity >= 0 and healer.asked.get(f"healed:{patient.id}", -1) != ctx.day


def heal(ctx, healer, patient, reply_to=None, voice=None):
    from genesis.agents import talents
    from genesis.society.interaction import Act, feel, say

    r = ctx.world.rules
    amount = r.heal_base + r.heal_skill * healer.abilities.get("healing")
    if talents.has(healer, "healer", r):
        amount *= 1.5
    amount = round(min(100 - patient.health, amount), 2)
    patient.health = min(100, patient.health + amount)
    healer.asked[f"healed:{patient.id}"] = ctx.day
    event_id = say(
        ctx, healer, patient, Act.HEAL, {"amount": amount}, reply_to=reply_to, voice=voice
    )
    healer._practice("healing", 1.0, ctx.world, ctx.tick, ctx.log)
    if patient.sick_until is not None:  # M8e: tending shortens a sickness
        from genesis.world import disasters

        disasters.tend_sickness(ctx, healer, patient)
    feel(ctx, patient, healer, "healed", event_id)
    return event_id
