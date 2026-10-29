"""Disasters (M8e). See docs/15 (15.5).

Every 15-25 years (seeded), one disaster strikes one valley or all of them. The schedule is a
pure function of the seed, so which lakes are dry or which tiles have burned on any day can be
worked out again after loading. Each disaster has something that helps against it:

| kind      | what happens                                   | what helps                        |
|-----------|------------------------------------------------|-----------------------------------|
| drought   | all but the biggest lake dry; berries grow less | carrying water, moving            |
| winter    | the cold wears down anyone sleeping alone      | a shelter, sleeping close together |
| flood     | berry patches near water are lost              | moving, other patches             |
| sickness  | spreads by being close; drains health          | healers, keeping apart            |
| predators | beasts attack people alone at night            | weapons, staying together         |
| wildfire  | woods burn (for years); patches in them lost   | moving                            |
"""

import random
from dataclasses import dataclass

from genesis.history import events as ev

KINDS = ("drought", "winter", "flood", "sickness", "predators", "wildfire")
DAYS = {  # how long each lasts (days), drawn between these
    "drought": (90, 180),
    "winter": (60, 120),
    "flood": (20, 45),
    "sickness": (60, 120),
    "predators": (60, 150),
    "wildfire": (10, 30),
}
REGROW_FACTOR = {"drought": 0.5, "winter": 0.3}  # berries grow less in the region
BURNED_YEARS = 10  # woods take this long to grow back
CAUSES = {"winter": "cold", "sickness": "sickness", "predators": "beasts", "wildfire": "fire"}


@dataclass(frozen=True)
class Disaster:
    id: int
    kind: str
    region: str  # a valley's name, or "all"
    day: int  # first day (0-based day index)
    days: int

    def active(self, day: int) -> bool:
        return self.day <= day < self.day + self.days

    def hits(self, region: str) -> bool:
        return self.region in ("all", region)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "region": self.region,
            "day": self.day,
            "days": self.days,
        }


def schedule(seed: int, rules, days_per_year: int, regions: list[str]) -> tuple[Disaster, ...]:
    """Every disaster for the next `disaster_horizon_years`, from the seed alone."""
    if not rules.disasters:
        return ()
    rng = random.Random(f"disasters:{seed}")
    names = regions or ["valley"]
    out = []
    year = rng.uniform(rules.disaster_min_years, rules.disaster_max_years)
    while year < rules.disaster_horizon_years:
        kind = rng.choice(KINDS)
        region = "all" if len(names) == 1 or rng.random() < 1 / 3 else rng.choice(names)
        days = rng.randint(*DAYS[kind])
        out.append(Disaster(len(out) + 1, kind, region, int(year * days_per_year), days))
        year += rng.uniform(rules.disaster_min_years, rules.disaster_max_years)
    return tuple(out)


# =================================================================
# Conditions on a day (pure: from the schedule and the map)
# =================================================================


def conditions(world, day: int) -> dict:
    """What the disasters do to the land on this day."""
    dry, famine, burned = set(), set(), set()
    factor: dict[str, float] = {}
    for d in world.disasters:
        if (
            d.kind == "wildfire"
            and d.day <= day < d.day + world.calendar.days_per_year * BURNED_YEARS
        ):
            burned |= {p for p in world.forest if d.hits(world.region_at[p])}
        if not d.active(day):
            continue
        for region in world.region_names:
            if not d.hits(region):
                continue
            if d.kind in REGROW_FACTOR:
                factor[region] = min(factor.get(region, 1.0), REGROW_FACTOR[d.kind])
            if d.kind == "drought":
                lakes = world.lakes_in.get(region, [])
                dry |= set(lakes[1:])  # all but the biggest
            elif d.kind == "flood":
                famine |= {
                    f.id
                    for f in world.food_sources
                    if world.food_region[f.id] == region and world.near_water(f.position, 3)
                }
            elif d.kind == "wildfire":
                famine |= {
                    f.id
                    for f in world.food_sources
                    if world.food_region[f.id] == region and world.near_forest(f.position)
                }
    return {
        "dry": frozenset(dry),
        "famine": frozenset(famine),
        "burned": frozenset(burned),
        "factor": factor,
    }


def active(world, day: int) -> list[Disaster]:
    return [d for d in world.disasters if d.active(day)]


# =================================================================
# Starting, ending, and each tick (the engine calls these)
# =================================================================


def _people_in(ctx, d: Disaster) -> list:
    return [
        a
        for a in sorted(ctx.agents.values(), key=lambda a: a.id)
        if a.alive and d.hits(ctx.world.region_at.get(a.position, ""))
    ]


def start(ctx, d: Disaster) -> int:
    """The first day of a disaster: its event, and what happens at once."""
    world, r = ctx.world, ctx.world.rules
    people = _people_in(ctx, d)
    event_id = ctx.log.emit(
        ctx.tick,
        ev.DISASTER,
        actors=tuple(a.id for a in people),
        data={**d.to_dict(), "started": True, "people": len(people)},
        cause_id=getattr(world, "god_causes", {}).get(d.id),  # M9: sent by God
    )
    world.disaster_events[d.id] = event_id
    for a in people:
        a.memory.remember_event(event_id)
    if d.kind in ("flood", "wildfire"):  # the patches it takes are lost at once
        lost = conditions(world, world.calendar.day_index(ctx.tick))["famine"]
        for f in world.food_sources:
            if f.id in lost:
                f.quantity = 0
    if d.kind == "wildfire":
        for a in people:
            if a.position in world.forest:
                harm(ctx, a, r.fire_damage, "wildfire", event_id)
    if d.kind == "sickness" and people:
        for a in ctx.rng.sample(people, max(1, len(people) // 5)):
            fall_sick(ctx, a, event_id)
    return event_id


def end(ctx, d: Disaster):
    people = _people_in(ctx, d)
    started = ctx.world.disaster_events.pop(d.id, None)
    ctx.log.emit(
        ctx.tick,
        ev.DISASTER,
        actors=tuple(a.id for a in people),
        data={**d.to_dict(), "started": False, "people": len(people)},
        cause_id=started,
    )


def each_tick(ctx, now: list[Disaster]):
    """Cold, sickness and beasts, for the disasters active now (and sickness running on)."""
    world, r, cal = ctx.world, ctx.world.rules, ctx.world.calendar
    per_tick = cal.hours_per_tick / 24  # rules are per day
    agents = [a for a in sorted(ctx.agents.values(), key=lambda a: a.id) if a.alive]
    night = cal.hour(ctx.tick) < 6 or cal.hour(ctx.tick) >= 22

    for d in now:
        hit = [a for a in agents if a.alive and d.hits(world.region_at.get(a.position, ""))]
        cause = world.disaster_events.get(d.id)
        if d.kind == "winter":
            for a in hit:
                if not _sheltered(ctx, a) and _company(a, agents, 1) < 2:
                    harm(ctx, a, r.cold_damage_per_day * per_tick, "winter", cause)
        elif d.kind == "predators" and night:
            for a in hit:
                if _company(a, agents, 2) == 0 and ctx.rng.random() < r.predator_chance:
                    _beasts(ctx, a, cause)
        elif d.kind == "sickness":
            for a in hit:
                if a.sick_until is None or not a.alive:
                    continue
                for b in hit:
                    if b is a or not b.alive or b.sick_until is not None:
                        continue
                    if b.immune_until > ctx.tick or a.position.distance(b.position) > 1:
                        continue
                    if ctx.rng.random() < r.sickness_spread:
                        fall_sick(ctx, b, cause, by=a)

    for a in agents:  # sickness runs its course, disaster or not
        if a.sick_until is None or not a.alive:
            continue
        if ctx.tick >= a.sick_until:
            a.sick_until = None
            a.immune_until = ctx.tick + cal.ticks(days=r.immune_days)
            a._emit(ctx.log, ctx.tick, ev.RECOVERED)
        else:
            harm(ctx, a, r.sick_damage_per_day * per_tick, "sickness", a.sick_event)


def _sheltered(ctx, agent) -> bool:
    from genesis.culture import inventions

    return inventions.sheltered(agent, ctx.world.structures)


def _company(agent, agents, distance) -> int:
    return sum(
        1
        for b in agents
        if b is not agent and b.alive and agent.position.distance(b.position) <= distance
    )


def harm(ctx, agent, amount: float, kind: str, cause_id):
    agent.health = max(0.0, agent.health - amount)
    if agent.health <= 0 and agent.alive:
        agent._die(ctx.tick, ctx.log, CAUSES.get(kind, kind), cause_id)


def fall_sick(ctx, agent, cause_id, by=None):
    r, cal = ctx.world.rules, ctx.world.calendar
    days = ctx.rng.randint(r.sick_min_days, r.sick_max_days)
    agent.sick_until = ctx.tick + cal.ticks(days=days)
    agent.sick_event = agent._emit(
        ctx.log,
        ctx.tick,
        ev.FELL_SICK,
        targets=(by.id,) if by is not None else (),
        data={"days": days, "from": by.name if by is not None else None},
        cause_id=cause_id,
    )


def tend_sickness(ctx, healer, patient):
    """Tending a sick person halves the sickness left."""
    if patient.sick_until is None:
        return
    left = patient.sick_until - ctx.tick
    patient.sick_until = ctx.tick + max(1, left // 2)


def _beasts(ctx, agent, cause_id):
    r = ctx.world.rules
    armed = agent.bonus.get("reaches", 0) > 0 or agent.bonus.get("cuts", 0) > 0
    driven_off = armed and ctx.rng.random() < r.armed_drive_off
    damage = 0.0 if driven_off else ctx.rng.uniform(r.beast_damage_min, r.beast_damage_max)
    event_id = agent._emit(
        ctx.log,
        ctx.tick,
        ev.BEAST_ATTACK,
        data={"driven_off": driven_off, "damage": round(damage, 1)},
        cause_id=cause_id,
    )
    if damage:
        harm(ctx, agent, damage, "predators", event_id)
