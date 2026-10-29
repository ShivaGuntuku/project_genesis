"""Prompt text. Order (for prefix reuse, doc 13): fixed rules → persona → situation → task.

Prompts stay well under the 2,048-token context: persona ~150 tokens, situation and options
~200, the answer ~60.
"""

import re

from genesis.llm import persona

SYSTEM = """You voice one person living in a small valley. Speak only as that person, in the \
first person, in plain words.
The valley is the whole world you know, and only these things exist in it: lakes, woods, \
rocky hills, grassland, berry bushes, clay, reeds, fibre, wood, stone, flint, shells, bright \
stones, the people, and what the people themselves have made and named. Time is counted in \
days, watches and seasons.
Nothing else exists, so never mention it: no animals or fish, no rivers or sea, no farming, \
no buildings, no metal, no weeks or months, no towns, money, writing, rulers or gods, and \
none of the made things of other worlds. If you need a word for something new, make up a \
word of your own.
Answer with JSON only:
- "thought": your private reason, under 15 words
- "choice": exactly one option key, when options are given
- "say": what you say out loud to the other person, under 25 words. Never describe actions."""

# How each kind of decision is put to the agent. {other} and {resource} are filled in.
TASKS = {
    "greet": "You come across {other}; you two haven't spoken yet today. Greet them in your own "
    "words, as fits how well you know each other.",
    "turn": "You are spending some time with {other}. What do you do, and what do you say?",
    "reply": "{heard}\nRespond to {other}.",
    "ask": "You need {resource} and have come to {other} to ask for help: {wish}. "
    "Ask in your own words.",
    "request": "{heard}\n{other} is asking you for some of your {resource}. Decide.",
    "info": "{heard}\n{other} is asking you where to find {resource}. Decide.",
    "threat": "{heard}\n{other} is threatening you and demands your {resource}. Decide.",
    "mate": "{heard}\n{other} is asking you to have a child together. A child needs food "
    "and care for many days. Decide.",
    "join": "{heard}\n{other} is asking you to join them as a group. Decide.",
    "obey": "{heard}\nDecide whether to do it.",
    "fight": "{heard}\nYou threatened {other} and they refused. Decide.",
    "trade": "{heard}\nDecide whether to take this trade with {other}.",
}

DIRECTIONS = {
    (0, -1): "north",
    (1, -1): "north-east",
    (1, 0): "east",
    (1, 1): "south-east",
    (0, 1): "south",
    (-1, 1): "south-west",
    (-1, 0): "west",
    (-1, -1): "north-west",
}


def direction(start, end) -> str:
    """'about 6 steps north-east' (north is up on the map)."""
    dx, dy = end.x - start.x, end.y - start.y
    steps = abs(dx) + abs(dy)
    if steps == 0:
        return "right here"
    # Snap to one of 8 directions (diagonal only if both parts are substantial).
    sx = 0 if abs(dx) * 2 < abs(dy) else (1 if dx > 0 else -1)
    sy = 0 if abs(dy) * 2 < abs(dx) else (1 if dy > 0 else -1)
    return f"about {steps} steps {DIRECTIONS[(sx, sy)]}"


def place(agent, world) -> str:
    if world.can_drink_at(agent.position):
        return "by the lake"
    terrain = world.terrain.get(agent.position)
    return {"plains": "on the grassland", "forest": "in the woods", "mountain": "in the hills"}.get(
        getattr(terrain, "value", ""), "in the valley"
    )


def part_of_day(hour: int) -> str:
    if 5 <= hour < 9:
        return "early morning"
    if 9 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def build(point, ctx, start_hour: int = 8, recall=None) -> str:
    """The user prompt for one decision point.

    recall(agent, ctx, query, about_id, exclude) -> memories that come to mind (M5)."""
    agent, other = point.agent, point.other
    cal = ctx.world.calendar
    if cal.watches:
        now = f"{cal.when(ctx.tick)}, at {cal.watch(ctx.tick)}"
    else:
        now = f"Day {cal.day(ctx.tick)}, {part_of_day(cal.hour(ctx.tick))}"
    task = (
        TASKS[point.kind]
        .format(
            other=other.name,
            resource=point.resource or "help",
            heard=point.heard or "",
            wish=_lower_first(point.options[0].label) if point.options else "help",
        )
        .strip()
    )
    recent, shown = persona.recent_words(agent, other, ctx)
    remembered = ""
    if recall is not None:
        query = f"{other.name}. {task}"
        remembered = persona.memories_block(recall(agent, ctx, query, other.id, shown), cal)

    parts = [
        persona.fact_block(agent, ctx),
        "Your story: " + persona.self_story(agent, ctx),
        persona.beliefs(agent),
        "",
        f"{now}. You are {place(agent, ctx.world)} with {other.name}.",
        persona.feelings_about(agent, other, ctx),
        remembered,
        recent,
        "",
        task,
    ]
    if point.options and point.kind not in ("greet", "ask"):
        parts.append("Options (choose one key):")
        parts.extend(f"- {o.key}: {o.label}" for o in point.options)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]
