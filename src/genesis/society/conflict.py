"""Fights (M7). See docs/04-society.md (4.5).

Escalation: an insult or a threat comes first; a refused threat, or a deep grudge, can
turn into a fight. It takes provocation: temper (aggression) + grudge (grievance, or
dislike) + desperate need ≥ `fight_provocation`. The attacker chooses (the LLM in persona,
else aggression).

    power  = strength + 0.5 × combat skill + 0.15 × allies within 2 tiles + luck (σ 0.25)

The loser is hurt (more for a bigger gap) and, if the attacker won, loses something they
carry. Death is possible but rare. Everyone who saw it remembers and takes sides; the
loser's family and group take on half the grudge: that is how feuds start.
"""

from genesis.agents import lifecycle
from genesis.history import events as ev
from genesis.society import family

WITNESS_RANGE = 3
ALLY_RANGE = 2


def _rel(a, b):
    return a.relationship(b.id, b.name)


def may_attack(ctx, a, b) -> bool:
    r = ctx.world.rules
    if not (a.alive and b.alive) or lifecycle.CHILD in (a.stage, b.stage):
        return False
    if a.health < r.fight_min_health or a.genome.aggression < r.fight_min_aggression:
        return False  # the gentlest never start fights
    last = a.asked.get(f"{b.id}:fight")
    if last is not None and ctx.tick - last < ctx.world.calendar.ticks(days=3):  # 3 days
        return False
    rel = _rel(a, b)
    close = a.partner == b.id or family.close_kin(a, b)
    if close and rel.grievance < 0.8:
        return False  # family and partners don't come to blows without a deep grudge
    needy = any(
        getattr(a, need) >= r.urgent_threshold and b.inventory.get(res, 0) > 0
        for res, need in (("water", "thirst"), ("food", "hunger"))
    )
    # Provocation: temper + grudge (+ desperate need) - fear. A gentle person needs a deep
    # grudge; someone who was beaten by them thinks twice.
    grudge = max(rel.grievance, -rel.affinity, 0.0)
    provocation = a.genome.aggression + grudge + (0.3 if needy else 0.0) - rel.fear
    return provocation >= r.fight_provocation


def allies(ctx, fighter, enemy) -> list:
    """Family and group members standing close by, ready to help."""
    out = []
    for other in sorted(ctx.agents.values(), key=lambda x: x.id):
        if other.id in (fighter.id, enemy.id) or not other.alive:
            continue
        if other.stage == lifecycle.CHILD or other.position.distance(fighter.position) > ALLY_RANGE:
            continue
        same_group = fighter.group_id is not None and other.group_id == fighter.group_id
        kin = family.close_kin(fighter, other) or fighter.partner == other.id
        if (same_group or kin) and _rel(other, enemy).affinity < 0.5:
            out.append(other)
    return out


def fight(ctx, attacker, defender, reply_to=None, voice=None, cause=None):
    """Returns the fight event id."""
    from genesis.society.interaction import Act, say

    r = ctx.world.rules
    attacker.asked[f"{defender.id}:fight"] = ctx.tick
    defender.asked[f"{attacker.id}:fight"] = ctx.tick
    if cause is None and reply_to is None:  # a grudge: what last changed the attacker's mind
        rel = _rel(attacker, defender)
        cause = rel.grudge_event_id or rel.last_event_id
    start = say(
        ctx, attacker, defender, Act.ATTACK, {}, reply_to=reply_to, voice=voice, cause_id=cause
    )
    sides = {
        attacker.id: allies(ctx, attacker, defender),
        defender.id: allies(ctx, defender, attacker),
    }

    def power(x):
        return (
            x.genome.strength
            + 0.5 * x.abilities.get("combat")
            + 0.1 * min(1, x.bonus.get("cuts", 0))  # tools (M8b)
            + 0.25 * min(1, x.bonus.get("reaches", 0))
            + 0.15 * len(sides[x.id])
            + 0.25 * (r.talents and x.talent == "strong-armed")  # M8d
            + ctx.rng.gauss(0, r.fight_luck)
        )

    pa, pd = power(attacker), power(defender)
    winner, loser = (attacker, defender) if pa >= pd else (defender, attacker)
    margin = min(1.0, abs(pa - pd))
    damage = round(r.fight_damage_base + r.fight_damage_margin * margin, 1)
    loser.health = max(0.0, loser.health - damage)

    took = None
    if winner is attacker:
        for resource in ("water", "food"):
            have = loser.inventory.get(resource, 0)
            unit = r.food_per_meal if resource == "food" else 1
            room = winner.carry_capacity(resource, r) - winner.inventory.get(resource, 0)
            amount = min(have, unit, room)
            if amount > 0:
                loser.inventory[resource] -= amount
                winner.inventory[resource] = winner.inventory.get(resource, 0) + amount
                took = {"resource": resource, "amount": amount}
                break

    witnesses = [
        w
        for w in sorted(ctx.agents.values(), key=lambda x: x.id)
        if w.alive
        and w.id not in (attacker.id, defender.id)
        and w.position.distance(attacker.position) <= WITNESS_RANGE
    ]
    fight_id = ctx.log.emit(
        ctx.tick,
        ev.FIGHT,
        actors=(attacker.id,),
        targets=(defender.id,),
        position=defender.position,
        data={
            "winner": winner.id,
            "loser": loser.id,
            "winner_name": winner.name,
            "loser_name": loser.name,
            "damage": damage,
            "took": took,
            "allies": {str(k): [a.id for a in v] for k, v in sides.items()},
            "witnesses": [w.id for w in witnesses],
        },
        cause_id=start,
    )
    for x in (attacker, defender, *witnesses):
        x.memory.remember_event(fight_id)

    _feelings(ctx, attacker, defender, winner, loser, witnesses, fight_id)
    for x, amount in ((winner, 1.0), (loser, 0.5)):
        x._practice("combat", amount, ctx.world, ctx.tick, ctx.log)

    if ctx.world.rules.inventions:
        from genesis.culture import inventions

        inventions.lost_a_fight(loser)
    if loser.health <= 0:
        loser._die(ctx.tick, ctx.log, "killed", fight_id)
    return fight_id


def _feelings(ctx, attacker, defender, winner, loser, witnesses, fight_id):
    from genesis.society.interaction import feel

    feel(ctx, loser, winner, "beaten", fight_id)
    feel(ctx, winner, loser, "won_against", fight_id)
    if defender is winner:
        feel(ctx, attacker, defender, {"respect": 0.15}, fight_id)

    # Witnesses: an attack on someone they don't side with frightens and repels them.
    for w in witnesses:
        on_attackers_side = w.group_id is not None and w.group_id == attacker.group_id
        on_attackers_side = on_attackers_side or family.close_kin(w, attacker)
        if on_attackers_side:
            feel(ctx, w, attacker, {"respect": 0.05}, fight_id)
        else:
            feel(ctx, w, attacker, "saw_attack", fight_id)

    # Feuds: the loser's family and group take on half the grudge.
    for other in sorted(ctx.agents.values(), key=lambda x: x.id):
        if not other.alive or other.id in (winner.id, loser.id):
            continue
        same_group = loser.group_id is not None and other.group_id == loser.group_id
        kin = family.close_kin(other, loser) or other.partner == loser.id
        if (same_group or kin) and not family.close_kin(other, winner):
            feel(ctx, other, winner, "feud", fight_id, met=other.memory.knows_agent(winner.id))
