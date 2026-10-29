"""Reproduction and families (M6). See docs/04-society.md (4.3).

A child exists only when two agents agree:

    1. PROPOSE   A asks B in conversation (A must like and trust B; both must be grown-up)
    2. DECIDE    B accepts or declines (the LLM in persona, else a rule from B's feelings)
    3. VALIDATE  the simulation checks again: health, food, cooldowns, kin, population cap
    4. CONCEIVE  chance = 0.25 + 0.75 × the pair's average fertility (elders count half);
                 the pair gives up `child_food_cost` food units
    5. BIRTH     after `gestation_days`: genes = inherit(A, B), a name the parents choose,
                 born next to the parent who accepted; family bonds; event "birth"

The causes chain: birth ← conceived ← the "yes" ← the proposal, so `genesis why` explains
every child. Families then matter: parents feed their children (decision engine), children
stay near a parent, and a death in the family brings grief.
"""

import re

from genesis.agents import lifecycle
from genesis.agents.genome import Genome
from genesis.history import events as ev
from genesis.llm.mind import Option, Question

TICKS_PER_DAY = 24

# Syllables for names when the parents' list of names runs out (and no LLM names the child).
SYLLABLES = ("ka ri to mi sa lo ne da vi ru el an or ta ze bo li en ia mo su ha ni ve").split()


def _emit(ctx, tick, type, actors=(), targets=(), **kwargs) -> int:
    """Record a family event; everyone involved remembers taking part in it."""
    event_id = ctx.log.emit(tick, type, actors=actors, targets=targets, **kwargs)
    for agent_id in (*actors, *targets):
        agent = ctx.agents.get(agent_id)
        if agent is not None:
            agent.memory.remember_event(event_id)
    return event_id


# =================================================================
# Kinship
# =================================================================


def close_kin(a, b) -> bool:
    """Parent, child or sibling (sharing a parent)."""
    if a.id in b.parents or b.id in a.parents:
        return True
    return bool(set(a.parents) & set(b.parents))


def relation(a, b, agents: dict) -> str | None:
    """What b is to a: partner, parent, child, sibling, grandparent, grandchild (or None)."""
    if a.partner == b.id:
        return "partner"
    if b.id in a.parents:
        return "parent"
    if a.id in b.parents:
        return "child"
    if set(a.parents) & set(b.parents):
        return "sibling"
    grandparents = {g for p in a.parents if (pa := agents.get(p)) for g in pa.parents}
    if b.id in grandparents:
        return "grandparent"
    if any(a.id in agents[p].parents for p in b.parents if p in agents):
        return "grandchild"
    return None


def family(agent, agents: dict) -> dict[int, str]:
    """{id: relation} for everyone related to the agent."""
    out = {}
    for other in agents.values():
        if other.id != agent.id:
            rel = relation(agent, other, agents)
            if rel is not None:
                out[other.id] = rel
    return out


# =================================================================
# Who may have a child with whom
# =================================================================


def why_not(ctx, a, b) -> str | None:
    """None if a and b may have a child now, else the reason (for logs and tests)."""
    r = ctx.world.rules
    for x in (a, b):
        if not x.alive:
            return f"{x.name} is dead"
        if x.stage == lifecycle.CHILD:
            return f"{x.name} is a child"
        if x.health < r.mate_min_health:
            return f"{x.name} is not healthy enough"
        if x.hunger >= r.urgent_threshold or x.thirst >= r.urgent_threshold:
            return f"{x.name} is starving or parched"
        if ctx.tick < x.next_mate_tick:
            return f"{x.name} had a child too recently"
        if x.pregnant:
            return f"{x.name} is already expecting"
    if r.kin_rule and close_kin(a, b):
        return "they are close kin"
    if r.monogamy:
        for x, y in ((a, b), (b, a)):
            partner = ctx.agents.get(x.partner) if x.partner is not None else None
            if partner is not None and partner.alive and partner.id != y.id:
                return f"{x.name} already has a partner"
    if a.inventory.get("food", 0) + b.inventory.get("food", 0) < r.child_food_cost:
        return "they don't carry enough food for a child"
    alive = sum(1 for x in ctx.agents.values() if x.alive)
    if alive + len(ctx.pregnancies) >= r.max_population:
        return "the valley is full"
    return None


def may_propose(ctx, a, b) -> bool:
    """a wants to (likes and trusts b enough), may (rules), and hasn't asked lately."""
    r = ctx.world.rules
    rel = a.relationships.get(b.id)
    if rel is None or rel.affinity < r.propose_min_affinity or rel.trust < r.propose_min_trust:
        return False
    last = a.asked.get(f"{b.id}:propose")
    cal = ctx.world.calendar
    if last is not None and ctx.tick - last < cal.ticks(days=r.propose_cooldown_days):
        return False
    return why_not(ctx, a, b) is None


# =================================================================
# Proposal → agreement → conception
# =================================================================


def propose(ctx, a, b, reply_to=None, voice=None):
    """a asks b to have a child together. Returns the event id of b's answer."""
    from genesis.society.interaction import Act, feel, say, think

    a.asked[f"{b.id}:propose"] = ctx.tick
    event_id = say(ctx, a, b, Act.PROPOSE, {}, reply_to=reply_to, voice=voice)
    feel(ctx, b, a, "proposed_to", event_id)

    heard = f'{a.name} said: "{voice.say}"' if voice is not None else None
    options = [
        Option("agree", f"Agree to have a child with {a.name}"),
        Option("decline", "Decline, at least for now"),
    ]
    decision = think(ctx, "mate", b, a, options, heard=heard)
    if decision is not None:
        accept = decision.choice == "agree"
    else:
        rel = b.relationship(a.id, a.name)
        chance = (
            0.1
            + 0.6 * max(0.0, rel.affinity)
            + 0.2 * rel.trust
            + 0.1 * b.genome.sociability
            + (0.2 if b.partner == a.id else 0.0)
        )
        accept = ctx.rng.random() < chance

    answer = say(
        ctx,
        b,
        a,
        Act.REPLY,
        {"answer": "agree" if accept else "decline", "about": "child"},
        reply_to=event_id,
        voice=decision,
    )
    if accept:
        agree(ctx, a, b, answer)
    else:
        feel(ctx, a, b, "declined", answer)
    return answer


def agree(ctx, a, b, agreement_id):
    """Both said yes: they become partners, and may conceive."""
    from genesis.society.interaction import feel

    r = ctx.world.rules
    cal = ctx.world.calendar
    for x, y in ((a, b), (b, a)):
        if x.partner != y.id:
            x.partner = y.id
        feel(ctx, x, y, "partnered", agreement_id)

    if why_not(ctx, a, b) is not None:  # validated again: something changed mid-talk
        return None

    def fertile(x):
        factor = r.elder_fertility_factor if x.stage == lifecycle.ELDER else 1.0
        return x.genome.fertility * factor

    fertility = (
        r.conceive_base_chance + (1 - r.conceive_base_chance) * (fertile(a) + fertile(b)) / 2
    )
    if ctx.rng.random() >= fertility:
        for x in (a, b):
            x.next_mate_tick = ctx.tick + cal.ticks(days=r.failed_conceive_cooldown_days)
        return None

    # The cost of a child: food the pair gives up (the richer one gives first).
    owed = r.child_food_cost
    for x in sorted((a, b), key=lambda x: (-x.inventory.get("food", 0), x.id)):
        take = min(owed, x.inventory.get("food", 0))
        x.inventory["food"] -= take
        owed -= take

    for x in (a, b):
        x.next_mate_tick = ctx.tick + cal.ticks(days=r.conceive_cooldown_days)
    b.pregnant = True
    event_id = _emit(
        ctx,
        ctx.tick,
        ev.CONCEIVED,
        actors=(a.id, b.id),
        position=b.position,
        data={"food_cost": r.child_food_cost, "due_in_days": r.gestation_days},
        cause_id=agreement_id,
    )
    ctx.pregnancies.append(
        {
            "parents": [a.id, b.id],
            "carrier": b.id,
            "conceived": ctx.tick,
            "due": ctx.tick + cal.ticks(days=r.gestation_days),
            "cause": event_id,
        }
    )
    return event_id


# =================================================================
# Birth
# =================================================================


def births(ctx, sim):
    """Children whose time has come are born (start of a tick, before anyone acts)."""
    still = []
    for p in ctx.pregnancies:
        carrier = ctx.agents.get(p["carrier"])
        other_id = next(i for i in p["parents"] if i != p["carrier"])
        if carrier is None or not carrier.alive:
            _emit(
                ctx,
                ctx.tick,
                ev.PREGNANCY_LOST,
                actors=tuple(p["parents"]),
                data={"reason": "the carrier died"},
                cause_id=p["cause"],
            )
            if carrier is not None:
                carrier.pregnant = False
            continue
        if ctx.tick < p["due"]:
            still.append(p)
            continue
        born(ctx, sim, carrier, ctx.agents[other_id], p)
    ctx.pregnancies[:] = still


def born(ctx, sim, carrier, other, pregnancy):
    r = ctx.world.rules
    first, second = (ctx.agents[i] for i in pregnancy["parents"])
    genome = Genome.inherit(first.genome, second.genome, ctx.rng, r.mutation_sigma, r.blend_chance)
    name = choose_name(ctx, sim, carrier, other)

    child = sim.population.create_agent(
        name,
        carrier.position,
        born_tick=ctx.tick,
        genome=genome,
        generation=max(first.generation, second.generation) + 1,
        parents=(first.id, second.id),
    )
    child.stage = lifecycle.CHILD
    child.hunger = child.thirst = r.newborn_need
    ctx.agents[child.id] = child
    carrier.pregnant = False

    # Taught by the parent who bore them: where food and water are.
    for loc in carrier.memory.get_known_locations():
        child.memory.remember_location(loc.resource_type, loc.position, seen=False)

    event_id = _emit(
        ctx,
        ctx.tick,
        ev.BIRTH,
        actors=(child.id,),
        targets=(first.id, second.id),
        position=carrier.position,
        data={
            "name": name,
            "parents": [first.name, second.name],
            "generation": child.generation,
        },
        cause_id=pregnancy["cause"],
    )

    from genesis.society import groups

    groups.on_birth(ctx, child, carrier, event_id)
    _bond(ctx, child, first, event_id, parent=True)
    _bond(ctx, child, second, event_id, parent=True)
    for sibling_id in sorted(set(first.children) | set(second.children)):
        sibling = ctx.agents.get(sibling_id)
        if sibling is not None and sibling.alive:
            _bond(ctx, child, sibling, event_id, parent=False)
    for parent in (first, second):  # a parent who died before the birth is not changed by it
        if parent.alive:  # (the dead are not saved again: resume would differ, M8f fix)
            parent.children.append(child.id)
    return child


def _bond(ctx, child, kin, event_id, parent: bool):
    """Family starts close: parents and children love and trust each other."""
    for x, y, values in (
        (child, kin, (0.7, 0.8, 0.6) if parent else (0.4, 0.6, 0.4)),
        (kin, child, (0.8, 0.7, 0.6) if parent else (0.4, 0.5, 0.4)),
    ):
        if not x.alive:
            continue  # a parent who died before the birth
        rel = x.relationship(y.id, y.name)
        affinity, trust, familiarity = values
        for dimension, value in (
            ("affinity", affinity),
            ("trust", trust),
            ("familiarity", familiarity),
        ):
            old = getattr(rel, dimension)
            if value > old:
                setattr(rel, dimension, value)
                ctx.relationship_history.append(
                    (ctx.tick, x.id, y.id, dimension, old, value, event_id)
                )
        rel.last_event_id = event_id
        x.memory.remember_agent(y.id, y.name, y.position, ctx.tick, event_id)


# =================================================================
# Names
# =================================================================

NAME = re.compile(r"^[A-Za-z]{2,10}$")


def taken_names(sim) -> set[str]:
    return {a.name.lower() for a in sim.population.agents}


def choose_name(ctx, sim, parent, other) -> str:
    """The parents choose: the LLM in persona if there is a mind, else the next unused name
    from the world's list, else a new name made of syllables."""
    from genesis.llm import persona

    taken = taken_names(sim)
    mind = ctx.mind
    if mind is not None and mind.active:

        def prompt():
            used = ", ".join(sorted(a.name for a in sim.population.agents))
            return "\n".join(
                [
                    persona.fact_block(parent, ctx),
                    "Your story: " + persona.self_story(parent, ctx),
                    "",
                    f"Your child with {other.name} has just been born.",
                    "Choose the child's name: one word of 2 to 10 letters, a name nobody in "
                    f"the valley has had before (not: {used}).",
                    f'Answer in JSON: "thought", "name", and "say" (what you tell {other.name}).',
                ]
            )

        def parse(text, record):
            import json

            try:
                data = json.loads(text)
                name = str(data.get("name", "")).strip()
            except (ValueError, TypeError, AttributeError):
                name = ""
            if not NAME.match(name) or name.lower() in taken:
                if record is not None:
                    record["outcome"] = "invalid"
                    record["error"] = f"name {name!r} is not a new single word"
                return None
            name = name.capitalize()
            if record is not None:
                record.update(
                    choice=name,
                    say=str(data.get("say", ""))[:160],
                    thought=str(data.get("thought", ""))[:160],
                )
            return name

        question = Question(
            kind="name",
            agent=parent,
            other=other,
            prompt=prompt,
            schema={
                "type": "object",
                "properties": {
                    "thought": {"type": "string"},
                    "name": {"type": "string"},
                    "say": {"type": "string"},
                },
                "required": ["thought", "name", "say"],
            },
            parse=parse,
            max_tokens=80,
            budgeted=False,
        )
        name = mind.ask(ctx, ctx.next_point(), question)
        if name is not None:
            return name

    for name in sim.config.names:
        if name.lower() not in taken:
            return name
    while True:
        name = "".join(ctx.rng.choice(SYLLABLES) for _ in range(ctx.rng.choice((2, 2, 3))))
        name = name.capitalize()
        if name.lower() not in taken and NAME.match(name):
            return name


# =================================================================
# Grief
# =================================================================


def grieve(ctx, died_event):
    """Close family of the dead lose heart (energy), and a partner is widowed."""
    dead = ctx.agents.get(died_event.actors[0])
    if dead is None:
        return
    r = ctx.world.rules
    for other in sorted(ctx.agents.values(), key=lambda x: x.id):
        if not other.alive or other.id == dead.id:
            continue
        rel = relation(other, dead, ctx.agents)
        if rel is None:
            continue
        other.energy = max(0, other.energy - r.grief_energy_loss)
        widowed = other.partner == dead.id
        if widowed:
            other.partner = None
        _emit(
            ctx,
            ctx.tick,
            ev.GRIEVED,
            actors=(other.id,),
            targets=(dead.id,),
            position=other.position,
            data={"relation": rel, "name": dead.name, "widowed": widowed},
            cause_id=died_event.id,
        )
