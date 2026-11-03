"""Research in the world (S2.3): people find things out on purpose, and keep what they learn.

    question   a curious person with no pressing trouble picks a question: what does this
               material do when worked this way? ("what does reed do when soaked?")
    study      they test it (an hour, one unit of the material, the tools it needs) and see
               the result. The result is *lore*: a fact about the world, true by the physics.
    use        lore guides invention: tries lean on what is known (and the LLM is told it).
               Each invention records the lore that led to it.
    sharing    lore spreads in conversation (trust helps); children and apprentices pick up
               their parents' lore and ways of making by working beside them.
    records    once something that marks exists, people leave records at their group's home:
               marked lore and ways of making. Records outlive their makers; whoever stands
               by one can learn from it, and a way of making kept in a record is not lost.

On only in worlds with `inventions = 2` and `research = 1`. Rules decide every step (the LLM
is told the lore it knows when it proposes an invention). See docs/08 (S2.3).
"""

import zlib

from genesis.culture import tech
from genesis.culture.materials import Recipe
from genesis.history import events as ev

PAST = {
    "hollow": "hollowed",
    "sharpen": "sharpened",
    "weave": "woven",
    "shape": "shaped",
    "smooth": "smoothed",
    "heat": "heated",
    "burn": "burnt",
    "twist": "twisted",
    "grind": "ground",
    "split": "split",
    "polish": "polished",
    "soak": "soaked",
}
WORDS = {
    "hard": "hard",
    "sharp": "sharp",
    "flexible": "bendy",
    "long": "long",
    "hollow": "hollow",
    "watertight": "watertight",
    "heavy": "heavy",
    "shiny": "shiny",
    "burns": "quick to burn",
    "heatproof": "fire-proof",
    "strong": "strong",
    "warm": "warm",
    "floats": "floaty",
    "sticky": "sticky",
    "absorbs": "soaking",
    "edge": "fine-edged",
}
SHARE_CHANCE = 0.15  # telling a fact in a free-time chat (+ trust)
APPRENTICE_LORE = 0.3  # a day beside a parent: a fact ...
APPRENTICE_MAKING = 0.05  # ... or a way of making
RECORD_CHANCE = 0.05  # a day at home with a marking tool: a record made or added to
RECORD_ITEMS = 3  # facts and ways added at a time
READ_CHANCE = 0.3  # a day by a record: one thing learned from it
NEAR = 2  # tiles: beside a parent, by a record


def enabled(rules) -> bool:
    return rules.inventions >= 2 and bool(rules.research)


def key(material: str, action: str) -> str:
    return f"{material}:{action}"


def finding(material: str, action: str, made=None) -> dict[str, float]:
    """What the physics says happens: {property: change}. Empty: nothing (it can't be worked
    that way)."""
    before, _ = tech.properties(Recipe.of([material], []), made)
    after, _ = tech.properties(Recipe.of([material], [action]), made)
    return {
        p: round(after[p] - before[p], 2)
        for p in tech.PROPERTIES
        if abs(after[p] - before[p]) >= 0.05
    }


def words(lore_key: str, concepts=None, made=None) -> str:
    """The fact in plain words: "soaked reed is more bendy, less hard"."""
    from genesis.culture.inventions import material_name

    material, action = lore_key.split(":")
    name = material_name(material, concepts or {})
    changes = finding(material, action, made)
    if not changes:
        return f"{name} can't be {PAST.get(action, action)}"
    parts = [
        f"{'more' if v > 0 else 'less'} {WORDS.get(p, p)}"
        for p, v in sorted(changes.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[:3]
    ]
    return f"{PAST.get(action, action)} {name} is {', '.join(parts)}"


def lore_of(world) -> dict:
    return world.research["lore"]


def records_of(world) -> list:
    return world.research["records"]


# =================================================================
# A question and its study
# =================================================================


def plan(agent, world, tick):
    """A research project for a curious person with nothing pressing: ("study" | "gather",
    material, action), or None. Decided without chance (a hash of the person, day and
    question), so it can be part of a decision."""
    from genesis.culture import inventions as inv

    r = world.rules
    if (
        not enabled(r)
        or agent.stage == "child"
        or agent.genome.curiosity < r.research_min_curiosity
    ):
        return None
    last = agent.asked.get("researched")
    wait = r.research_cooldown_days / max(0.25, agent.genome.curiosity)
    if last is not None and tick - last < world.calendar.ticks(days=wait):
        return None
    tools = inv.tools_of(agent, world.concepts)
    actions = [
        a
        for a, spec in sorted(tech.ACTIONS.items())
        if not spec.get("binds") and spec.get("tool", "") in {"", *tools}
    ]
    in_hand = [m for m in inv.material_kinds(agent) if not tech.is_made(m)]
    day = world.calendar.day_index(tick)
    # What bothers them most (even a little) makes some questions worth more.
    worry = max(sorted(agent.problems.items()), key=lambda kv: kv[1], default=(None, 0))[0]
    needs = tech.FUNCTIONS[tech.SOLVES[worry]]["needs"] if worry in tech.SOLVES else {}

    def worth(m, a):
        gain = sum(tech.ACTIONS[a]["changes"].get(p, 0.0) for p in needs)
        return round(gain + sum(tech.MATERIALS[m].get(p, 0.0) for p in needs) / 2, 2)

    questions = [
        (
            0 if m in in_hand else 1,
            -worth(m, a),
            zlib.crc32(f"{agent.id}:{day}:{m}:{a}".encode()),
            m,
            a,
        )
        for m in sorted(tech.MATERIALS)
        for a in actions
        if key(m, a) not in agent.lore
    ]
    for held, _, _, material, action in sorted(questions)[:6]:
        if held == 0:
            return ("study", material, action)
        if inv.gather_spot(agent, world, material) is not None:
            return ("gather", material, action)
    return None


def study(ctx, agent, material: str, action: str):
    """An hour's test: one unit of the material, worked; the result is a fact they keep."""
    from genesis.culture import inventions as inv

    if agent.materials.get(material, 0) < 1:
        return None
    agent.materials[material] -= 1
    if agent.materials[material] <= 0:
        del agent.materials[material]
    tool = tech.ACTIONS[action].get("tool")
    if tool:
        inv.wear(ctx, agent, tool)
    agent.asked["researched"] = ctx.tick
    agent._practice("crafting", 0.3, ctx.world, ctx.tick, ctx.log)
    k = key(material, action)
    agent.lore.add(k)
    found = lore_of(ctx.world)
    first = k not in found
    if first:
        found[k] = {"by": agent.id, "tick": ctx.tick}
    return inv._emit(
        ctx,
        ev.LORE_FOUND,
        actors=(agent.id,),
        position=agent.position,
        data={
            "lore": k,
            "text": words(k, ctx.world.concepts),
            "changes": finding(material, action),
            "first": first,
        },
    )


def guided(agent, material: str, action: str, needs) -> float:
    """How much what they know says this way of working gives what a use needs."""
    if key(material, action) not in agent.lore or material not in tech.MATERIALS:
        return 0.0
    changes = finding(material, action)
    return sum(changes.get(p, 0.0) for p in needs)


def used(agent, recipe: Recipe) -> list[str]:
    """The facts they knew that this recipe follows (a material worked a way they studied)."""
    return sorted(
        key(m, a) for m in recipe.materials for a in recipe.actions if key(m, a) in agent.lore
    )


def told_lines(agent, materials, concepts, n: int = 6) -> list[str]:
    """For a prompt: what they have found out about the things in hand."""
    keys = sorted(k for k in agent.lore if k.split(":")[0] in materials)[:n]
    return [words(k, concepts) for k in keys]


# =================================================================
# Sharing, apprenticeship and records
# =================================================================


def share(ctx, speaker, listener):
    """After a free-time chat: maybe tell one fact the listener lacks (about what they hold
    first). No event: it is part of the talk."""
    if not enabled(ctx.world.rules) or not speaker.lore:
        return
    new = sorted(speaker.lore - listener.lore)
    if not new:
        return
    rel = listener.relationships.get(speaker.id)
    chance = SHARE_CHANCE + 0.3 * max(0.0, rel.trust if rel is not None else 0.0)
    if ctx.rng.random() >= chance:
        return
    held = set(listener.materials)
    new.sort(key=lambda k: (k.split(":")[0] not in held, k))
    listener.lore.add(new[0])


def daily(ctx, sim):
    """Once a day: apprentices learn beside their parents; records are made and read."""
    if not enabled(ctx.world.rules):
        return
    alive = [a for a in sorted(ctx.agents.values(), key=lambda a: a.id) if a.alive]
    for a in alive:
        _apprentice(ctx, a)
    for a in alive:
        _record(ctx, a)
    for a in alive:
        _read(ctx, a)


def _apprentice(ctx, agent):
    from genesis.culture import inventions as inv

    if (
        agent.stage not in ("child", "adult")
        or agent.stage == "adult"
        and agent.age(ctx.tick)
        > (ctx.world.rules.adult_age_days + 5 * ctx.world.calendar.days_per_year)
        * ctx.world.calendar.ticks_per_day
    ):
        return  # children and the young (their first five adult years) are apprentices
    for pid in agent.parents:
        parent = ctx.agents.get(pid)
        if parent is None or not parent.alive or parent.position.distance(agent.position) > NEAR:
            continue
        lore = sorted(parent.lore - agent.lore)
        if lore and ctx.rng.random() < APPRENTICE_LORE:
            agent.lore.add(lore[0])
        ways = [
            c
            for c in sorted(parent.knowledge - agent.knowledge)
            if c in ctx.world.concepts and ctx.world.concepts[c].lost_tick is None
        ]
        if ways and ctx.rng.random() < APPRENTICE_MAKING:
            c = ctx.world.concepts[ways[0]]
            agent.knowledge.add(c.id)
            inv._emit(
                ctx,
                ev.LEARNED,
                actors=(agent.id,),
                targets=(parent.id,),
                data={"concept": c.id, "name": c.name, "learned": True, "how": "apprentice"},
            )


def _home(ctx, agent):
    g = ctx.groups.get(agent.group_id) if agent.group_id is not None else None
    return g.home_position if g is not None and g.ended_tick is None else None


def _record(ctx, agent):
    """With a marking tool, at their group's home: mark what they know that the record there
    doesn't hold yet."""
    from genesis.culture import inventions as inv

    if not agent.bonus.get("marks"):
        return
    home = _home(ctx, agent)
    if home is None or agent.position.distance(home) > ctx.world.rules.home_radius:
        return
    if ctx.rng.random() >= RECORD_CHANCE:
        return
    records = records_of(ctx.world)
    here = next((rec for rec in records if agent.position.distance(_at(rec)) <= NEAR), None)
    known_lore = set(here["lore"]) if here else set()
    known_ways = set(here["concepts"]) if here else set()
    lore = sorted(agent.lore - known_lore)
    ways = sorted(c for c in agent.knowledge - known_ways if c in ctx.world.concepts)
    adding = [("concepts", c) for c in ways] + [("lore", k) for k in lore]
    adding = adding[:RECORD_ITEMS]
    if not adding:
        return
    if here is None:
        here = {
            "id": max((rec["id"] for rec in records), default=0) + 1,
            "x": agent.position.x,
            "y": agent.position.y,
            "by": agent.id,
            "group": agent.group_id,
            "tick": ctx.tick,
            "lore": [],
            "concepts": [],
        }
        records.append(here)
    for kind, item in adding:
        here[kind].append(item)
    inv.wear(ctx, agent, "marks")
    inv._emit(
        ctx,
        ev.RECORDED,
        actors=(agent.id,),
        position=agent.position,
        data={
            "record": here["id"],
            "lore": [i for k, i in adding if k == "lore"],
            "concepts": [i for k, i in adding if k == "concepts"],
        },
    )


def _at(rec):
    from genesis.world.world import Position

    return Position(rec["x"], rec["y"])


def _read(ctx, agent):
    """Standing by a record: maybe learn one thing from it (a way of making first)."""
    from genesis.culture import inventions as inv

    if agent.stage == "child":
        return
    for rec in records_of(ctx.world):
        if agent.position.distance(_at(rec)) > NEAR:
            continue
        ways = [c for c in rec["concepts"] if c not in agent.knowledge and c in ctx.world.concepts]
        lore = [k for k in rec["lore"] if k not in agent.lore]
        if not (ways or lore) or ctx.rng.random() >= READ_CHANCE:
            continue
        if ways:
            c = ctx.world.concepts[ways[0]]
            agent.knowledge.add(c.id)
            if c.lost_tick is not None:  # found again in the marks
                c.lost_tick = None
            inv._emit(
                ctx,
                ev.LEARNED,
                actors=(agent.id,),
                data={"concept": c.id, "name": c.name, "learned": True, "how": "record"},
            )
        else:
            agent.lore.add(lore[0])
        return


def recorded(world, concept_id: int) -> bool:
    """A way of making kept in some record is not lost while the record stands."""
    return any(concept_id in rec["concepts"] for rec in records_of(world))
