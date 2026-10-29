"""Who an agent is, in words the model can play: the fact block and the self-story.

Everything here is derived from simulation state (genes, needs, skills, feelings), so the
persona always matches what the agent really is. From M5 the self-story is rewritten by the
agent's own daily reflection; in M4 it is built from facts.
"""

from genesis.agents.abilities import describe_skill
from genesis.agents.genome import BODY_GENES, DESCRIPTORS, HIGH, LOW
from genesis.history import events as ev
from genesis.history.describe import describe_message

TICKS_PER_DAY = 24

# Mind genes in everyday words: (high, low).
CHARACTER = {
    "curiosity": ("curious", "incurious"),
    "sociability": ("sociable", "solitary"),
    "aggression": ("quick-tempered", "gentle"),
    "risk_tolerance": ("bold", "cautious"),
    "generosity": ("generous", "selfish"),
    "ambition": ("ambitious", "content with little"),
    "honesty": ("honest", "willing to lie when it suits you"),
    "intelligence": ("clever", "slow-witted"),
}


def character(genome) -> list[str]:
    """The agent's strongest mind traits first. Honesty is always stated, because lying is
    one of the choices agents get."""
    words = []
    for gene in sorted(CHARACTER, key=lambda g: -abs(getattr(genome, g) - 0.5)):
        if gene == "honesty":
            words.append(_honesty(genome.honesty))
            continue
        value = getattr(genome, gene)
        high, low = CHARACTER[gene]
        if value >= 0.8:
            words.append(f"very {high}")
        elif value >= 0.65:
            words.append(high)
        elif value <= 0.2:
            words.append(f"very {low}" if " " not in low else low)
        elif value <= 0.35:
            words.append(low)
    return words


def _honesty(value: float) -> str:
    if value >= 0.8:
        return "very honest: you never lie"
    if value >= 0.65:
        return "honest"
    if value > 0.35:
        return "mostly honest, though you might lie if it really helps you"
    if value > 0.2:
        return "willing to lie when it suits you"
    return "a habitual liar"


def need_words(agent) -> list[str]:
    out = []
    for value, word in ((agent.thirst, "thirsty"), (agent.hunger, "hungry")):
        if value >= 75:
            out.append(f"desperately {word}")
        elif value >= 50:
            out.append(word)
        elif value >= 30:
            out.append(f"a little {word}")
    if agent.energy < 25:
        out.append("exhausted")
    elif agent.energy < 50:
        out.append("tired")
    if agent.health < 50:
        out.append("weak and unwell")
    return out or ["comfortable"]


def carrying(inventory: dict, rules) -> str:
    food = inventory.get("food", 0) // max(1, rules.food_per_meal)
    water = inventory.get("water", 0)
    parts = []
    if water:
        parts.append(f"{water} drink{'s' if water != 1 else ''} of water")
    if food:
        parts.append(f"{food} meal{'s' if food != 1 else ''} of berries")
    return " and ".join(parts) if parts else "nothing"


def fact_block(agent, ctx) -> str:
    world, rules = ctx.world, ctx.world.rules
    g = agent.genome
    cal = world.calendar
    lines = [
        f"You are {agent.name}, {span(agent.age(ctx.tick), cal)} old, {_stage(agent.stage)} "
        f"(people like you live about {span(agent.lifespan(world), cal)}).",
        "Your nature: " + (", ".join(character(g)) or "even-tempered, ordinary") + ".",
    ]
    body = [
        DESCRIPTORS[gene][0] if getattr(g, gene) >= HIGH else DESCRIPTORS[gene][1]
        for gene in BODY_GENES
        if gene != "fertility" and not LOW < getattr(g, gene) < HIGH
    ]
    if body:
        lines.append("Your body: " + ", ".join(body) + ".")
    skills = [describe_skill(k, v) for k, v in agent.abilities.trained().items() if v >= 0.25]
    if skills:
        lines.append("You are " + ", ".join(skills) + ".")
    g = ctx.groups.get(agent.group_id) if agent.group_id is not None else None
    if g is not None:
        leader = ctx.agents.get(g.leader)
        lead = "you lead it" if g.leader == agent.id else f"led by {leader.name}" if leader else ""
        lines.append(f"You belong to a group called {g.name} ({len(g.members)} people, {lead}).")
    if agent.order is not None and agent.order.get("kind") != "shun":
        target = ctx.agents.get(agent.order.get("target")) if agent.order.get("target") else None
        what = (
            "bring food to your group's store"
            if agent.order["kind"] == "gather"
            else (f"help {target.name}" if target else "help someone")
        )
        lines.append(f"Your leader told you to {what}, and you agreed.")
    if world.disasters:  # M8e: what is happening to their valley, and to them
        from genesis.history.describe import DISASTER_WORDS
        from genesis.world import disasters

        here = world.region_at.get(agent.position)
        for d in disasters.active(world, cal.day_index(ctx.tick)):
            if d.hits(here):
                lines.append(f"Right now {DISASTER_WORDS[d.kind]} has struck the valley.")
        if agent.sick_until is not None:
            lines.append("You are sick.")
    if rules.talents and agent.talent:  # M8d: their gift, and what others see them as
        from genesis.agents import talents

        lines.append(f"Your gift: {talents.DESCRIPTIONS[agent.talent]}.")
        job = talents.profession(agent)
        if job:
            lines.append(f"Others know you for your work as a {job}.")
    if rules.trade:
        from genesis.society import economy

        for debt in agent.owes:
            to = ctx.agents.get(debt["to"])
            what = economy.label(debt["good"], world.concepts)
            lines.append(
                f"You promised to bring {to.name if to else 'someone'} {what} "
                f"(within {span(max(0, debt['due'] - ctx.tick), cal)})."
            )
        money = economy.currency_in(world, economy.region_of(world, agent.position))
        if money is not None:
            what = economy.label(money["good"], world.concepts).removeprefix("1 ")
            lines.append(
                f"Around here people take {what} in trade to pass on; they call it {money['name']}."
            )
    if agent.plan:  # M8f: what they set out to do these days
        lines.append(f"These days you set out to: {agent.plan['label']}.")
    if rules.goals:  # M8c: what moves them, and what they are after
        from genesis.mind import goals

        lines.append(
            f"What drives you most: {goals.DRIVE_WORDS[goals.strongest_drive(agent, ctx)]}."
        )
        if agent.goal:
            since = span(ctx.tick - agent.goal["tick"], cal)
            if agent.goal.get("how") == "own words":
                lines.append(f'Your goal (for {since}): "{agent.goal["text"]}"')
            else:  # M8f: a feeling, not the rules' sentence (so their words stay their own)
                lines.append(
                    f"Lately you keep thinking about {goals.NEED_HINTS[agent.goal['need']]}."
                )
    concepts = ctx.world.concepts
    known = [concepts[c] for c in sorted(agent.knowledge) if c in concepts]
    if known:  # M8b: what they know how to make, in the valley's own words
        lines.append(
            "You know how to make: "
            + "; ".join(f"{c.name} (a thing that {c.function})" for c in known)
            + "."
        )
    owned = sorted({concepts[i["concept"]].name for i in agent.items if i["concept"] in concepts})
    if owned:
        lines.append("You have with you: " + ", ".join(owned) + ".")
    lines.append(
        f"Right now you are {', '.join(need_words(agent))}. "
        f"You carry {carrying(agent.inventory, rules)}."
    )
    return "\n".join(lines)


def self_story(agent, ctx) -> str:
    """Facts of the agent's life, then the story in their own words (written by their nightly
    reflection, from M5)."""
    since = ctx.tick - max(agent.born_tick, 0)
    ago = (
        "today"
        if ctx.world.calendar.age_days(since) == 0
        else f"{span(since, ctx.world.calendar)} ago"
    )
    if agent.generation == 0:
        start = f"You were one of the first people to come to the valley, {ago}."
    else:
        start = f"You were born in the valley {ago}."

    knows_food = len(agent.memory.get_known_locations("food"))
    knows_water = bool(agent.memory.get_known_locations("water"))
    places = []
    if knows_water:
        places.append("where the lake is")
    if knows_food:
        places.append(f"{knows_food} berry patch{'es' if knows_food != 1 else ''}")
    know = f" You know {' and '.join(places)}." if places else " You don't know the land yet."

    friends = [r.target_name for r in agent.relationships.values() if r.label() in FRIENDLY]
    rivals = [r.target_name for r in agent.relationships.values() if r.label() in HOSTILE]
    people = ""
    if friends:
        people += f" Your friends: {', '.join(sorted(friends))}."
    if rivals:
        people += f" You dislike {', '.join(sorted(rivals))}."
    own = f"\nIn your own words: {agent.story}" if agent.story else ""
    return start + know + people + own


def beliefs(agent, n: int = 3) -> str:
    """What the agent has come to believe (from its own reflections)."""
    held = agent.stream.beliefs(n)
    if not held:
        return ""
    return "What you believe:\n" + "\n".join(f"- {m.text}" for m in held)


def span(ticks: int, calendar) -> str:
    """A length of time in words: '34 days' (story mode) or '34 years' (millennium)."""
    days = calendar.age_days(ticks)
    if calendar.watches and days >= calendar.days_per_year:
        years = days // calendar.days_per_year
        return f"{years} year{'s' if years != 1 else ''}"
    return f"{days} day{'s' if days != 1 else ''}"


def memories_block(memories, calendar=None) -> str:
    if not memories:
        return ""
    lines = [
        f"- {calendar.when(m.tick) if calendar else f'Day {m.tick // TICKS_PER_DAY + 1}'}: {m.text}"
        for m in memories
    ]
    return "What comes to mind:\n" + "\n".join(lines)


FRIENDLY = ("friend", "close friend")
HOSTILE = ("rival", "enemy")


def feelings_about(agent, other, ctx) -> str:
    """How the agent sees the other person, and what they can see of them right now."""
    rel = agent.relationships.get(other.id)
    name = other.name
    if rel is None or not rel.met:
        about = f"You have never met {name} before."
        if rel is not None and abs(rel.affinity) >= 0.05:
            heard = "good things" if rel.affinity > 0 else "bad things"
            about += f" You have heard {heard} about them."
    else:
        about = f"{name} is your {rel.label()}. " + _feeling(rel, name)

    looks = []
    r = ctx.world.rules
    if other.thirst >= r.seek_threshold:
        looks.append("thirsty")
    if other.hunger >= r.seek_threshold:
        looks.append("hungry")
    seen = f" {name} looks {' and '.join(looks)}." if looks else ""
    mine = ctx.groups.get(agent.group_id) if agent.group_id is not None else None
    theirs = ctx.groups.get(other.group_id) if other.group_id is not None else None
    if theirs is not None and mine is theirs:
        seen += f" {name} is {'your leader' if theirs.leader == other.id else 'in your group'}."
    elif theirs is not None:
        lead = "and leads it" if theirs.leader == other.id else ""
        seen += f" {name} belongs to another group, {theirs.name} {lead}".rstrip() + "."
    seen += f" They carry {carrying(other.inventory, r)}."
    if other.stage != "adult":
        seen += f" They are {_stage(other.stage)}."

    return about + seen


def _feeling(rel, name) -> str:
    parts = []
    a = rel.affinity
    if a >= 0.6:
        parts.append(f"you are fond of {name}")
    elif a >= 0.25:
        parts.append(f"you like {name}")
    elif a <= -0.5:
        parts.append(f"you can't stand {name}")
    elif a <= -0.2:
        parts.append(f"you dislike {name}")
    else:
        parts.append(f"you feel neutral about {name}")
    if rel.trust >= 0.7:
        parts.append("you trust them")
    elif rel.trust <= 0.2:
        parts.append("you don't trust them")
    if rel.gratitude >= 0.2:
        parts.append("you owe them for past help")
    if rel.grievance >= 0.3:
        parts.append("you hold a grudge against them")
    if rel.fear >= 0.4:
        parts.append("you are afraid of them")
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


def recent_words(agent, other, ctx, limit: int = 4) -> tuple[str, set[int]]:
    """The last few things said between the two (from the in-memory log), and their event ids
    (so recalled memories don't repeat them)."""
    names = {agent.id: "you", other.id: other.name}
    said, ids = [], set()
    for e in reversed(ctx.log.recent):
        if e.type != ev.MESSAGE or not e.actors or not e.targets:
            continue
        pair = {e.actors[0], e.targets[0]}
        if pair != {agent.id, other.id}:
            continue
        d = e.data
        line = d.get("line")
        speaker = names[e.actors[0]]
        if line:
            text = f'{speaker.capitalize()} said: "{line}"'
        else:
            text = describe_message(
                d["act"],
                d.get("payload", {}),
                speaker.capitalize(),
                names[e.targets[0]],
                True,  # the agent doesn't know which statements were lies
                {},
            )
        said.append(f"- {ctx.world.calendar.when(e.tick)}: {text}")
        ids.add(e.id)
        if len(said) >= limit:
            break
    if not said:
        return "", ids
    return "Recently between you:\n" + "\n".join(reversed(said)), ids


def _stage(stage: str) -> str:
    return {"child": "a child", "adult": "an adult", "elder": "an elder"}.get(stage, stage)
