"""Nightly reflection (M5): each agent thinks over its day, in its own words.

Once a night, an agent whose day held something important (a memory of importance ≥
`reflection_min_importance`) is asked by the model:
- 0–2 new **beliefs** (kept for life, recalled in every prompt),
- its **self-story**, rewritten (shown in every prompt as "in your own words"),
- whom it now feels **warmer or colder** towards (at most ±`reflection_feeling_step` each).

The model can't swing feelings wildly, can only name people the agent knows, and every
reflection is recorded like any other call, so replays reproduce it exactly.
"""

import json
import re
from dataclasses import dataclass, field

from genesis.culture.words import leak_words
from genesis.history import events as ev
from genesis.llm import persona, schemas
from genesis.llm.mind import Question, ask_many
from genesis.mind import stream as memory

MAX_BELIEFS = 2
MAX_FEELINGS = 2
MAX_PEOPLE = 8
STORY_CHARS = 400
CHANGES = ("warmer", "colder")


@dataclass
class Reflection:
    thought: str
    beliefs: list[str]
    story: str
    feelings: list[tuple[str, str]] = field(default_factory=list)  # (name, warmer|colder)
    record: dict | None = None
    goal: tuple[str, str] | None = None  # (need, their words) (M8c)


def reflect(ctx, sim, agent):
    built = question(ctx, sim, agent)
    if built is None:
        return
    q, people = built
    (result,) = ask_many(ctx.mind, ctx, [q])
    if result is not None:
        apply(ctx, sim, agent, result, people)


def reflect_all(ctx, sim, agents):
    """Everyone's reflection, asked together (M8.5 workers), applied in order."""
    built = [(a, question(ctx, sim, a)) for a in agents]
    built = [(a, b) for a, b in built if b is not None]
    if not built:
        return
    results = ask_many(ctx.mind, ctx, [q for _, (q, _) in built])
    for (agent, (_, people)), result in zip(built, results, strict=True):
        if result is not None:
            apply(ctx, sim, agent, result, people)


def question(ctx, sim, agent):
    """(the reflection question, {name: person}) or None if there is nothing to think over."""
    mind = ctx.mind
    if not getattr(mind, "reflects", False) or agent.stage == "child":
        return None
    r = ctx.world.rules
    every = max(1, r.reflect_every_days)  # what happened since the last reflection
    day_start = sim.tick_count - ctx.world.calendar.ticks_per_day * every
    today = [m for m in agent.stream.since(day_start, r.reflection_min_importance)]
    today = [m for m in today if m.kind != memory.BELIEF]
    if not today:
        return None

    people = _people(agent, ctx)
    names = [p.name for p in people]
    needs = _goal_needs(ctx)
    config = getattr(mind, "config", None)
    q = Question(
        kind="reflect",
        agent=agent,
        other=None,
        prompt=lambda: build_prompt(ctx, sim, agent, today, people, mind),
        schema=schema(names, needs),
        parse=lambda text, record: parse(text, names, record, needs),
        max_tokens=config.reflection_max_tokens if config else 300,
        budgeted=False,
    )
    return q, {p.name: p for p in people}


def _goal_needs(ctx) -> list[str]:
    if not ctx.world.rules.goals:
        return []
    from genesis.mind import goals

    return goals.needs_allowed(ctx.world.rules)


def _people(agent, ctx) -> list:
    """Living people the agent has met, the ones it feels most about first."""
    known = []
    for other_id, rel in agent.relationships.items():
        other = ctx.agents.get(other_id)
        if rel.met and other is not None and other.alive:
            known.append((-(abs(rel.affinity) + rel.familiarity + rel.grievance), other_id, other))
    return [other for *_, other in sorted(known)[:MAX_PEOPLE]]


def build_prompt(ctx, sim, agent, today, people, mind) -> str:
    cal = ctx.world.calendar
    night = (
        f"the night after Day {cal.day(ctx.tick)}"
        if not cal.watches
        else (f"night, {cal.when(ctx.tick)}")
    )
    lines = [
        persona.fact_block(agent, ctx),
        "Your story: " + persona.self_story(agent, ctx),
        persona.beliefs(agent, 5),
        "",
        f"It is {night}. Nobody hears your thoughts now.",
        "Today you remember:",
        *[f"- {m.text}" for m in today[-12:]],
    ]
    if hasattr(mind, "recall"):
        query = " ".join(m.text for m in today[-6:])
        older = [
            m for m in mind.recall(agent, ctx, query) if m.tick < sim.tick_count - cal.ticks_per_day
        ]
        if older:
            lines.append("Older memories this brings back:")
            lines += [f"- {cal.when(m.tick)}: {m.text}" for m in older[:3]]
    if people:
        lines.append(
            "People you know: "
            + ", ".join(f"{p.name} ({agent.relationships[p.id].label()})" for p in people)
        )
    lines += [
        "",
        "Think over your day, as yourself. Answer in JSON:",
        '- "thought": what you feel about today, under 15 words',
        f'- "beliefs": 0 to {MAX_BELIEFS} short new beliefs you now hold (about people, the '
        "valley, or how to live), each under 20 words. Only what today taught you.",
        '- "story": your life story so far, in the first person, under 60 words. '
        "Keep what still matters and add what changed.",
    ]
    if people:
        lines.append(
            f'- "feelings": up to {MAX_FEELINGS} people you now feel "warmer" or "colder" '
            "towards because of today (an empty list if none)"
        )
    needs = _goal_needs(ctx)
    if needs:
        from genesis.mind import goals as goal_rules

        if agent.goal and agent.goal.get("how") == "own words":
            now = f'Your goal now: "{agent.goal["text"]}". Keep it if it still matters to you.'
        elif agent.goal:
            now = (
                f"Lately you keep thinking about {goal_rules.NEED_HINTS[agent.goal['need']]}; "
                "say what you want, in words of your own, from your own life."
            )
        else:
            now = "You have no goal yet."
        lines += [
            f'- "goal": what you most want to achieve in the coming years, in your own words, '
            f"under 20 words. {now}",
            f'- "goal_need": what your goal is about, one of: {", ".join(needs)}',
        ]
    return "\n".join(lines)


def schema(names: list[str], needs: list[str] = ()) -> dict:
    properties = {
        "thought": {"type": "string"},
        "beliefs": {"type": "array", "items": {"type": "string"}},
        "story": {"type": "string"},
    }
    if needs:
        properties["goal"] = {"type": "string"}
        properties["goal_need"] = {"type": "string", "enum": list(needs)}
    if names:
        properties["feelings"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": names},
                    "change": {"type": "string", "enum": list(CHANGES)},
                },
                "required": ["name", "change"],
            },
        }
    return {"type": "object", "properties": properties, "required": list(properties)}


def parse(
    text: str, names: list[str], record: dict | None, needs: list[str] = ()
) -> Reflection | None:
    def invalid(reason):
        if record is not None:
            record["outcome"] = "invalid"
            record["error"] = reason
        return None

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return invalid("not JSON")
    if not isinstance(data, dict):
        return invalid("not an object")

    raw_beliefs = data.get("beliefs") or []
    if not isinstance(raw_beliefs, list) or not all(isinstance(b, str) for b in raw_beliefs):
        return invalid("beliefs must be a list of text")
    beliefs, dropped = [], []
    for b in raw_beliefs:
        b = schemas.clean_line(b, 160)
        if leak_words(b):  # M8f: a belief about things the valley doesn't have
            dropped += leak_words(b)
            continue
        if b and b.lower() not in (x.lower() for x in beliefs):
            beliefs.append(b)
    beliefs = beliefs[:MAX_BELIEFS]

    story = data.get("story") or ""
    thought = data.get("thought") or ""
    if not isinstance(story, str) or not isinstance(thought, str):
        return invalid("story and thought must be text")
    story = schemas.clean_line(story, STORY_CHARS)
    if leak_words(story):  # keep only the sentences that belong to the valley
        dropped += leak_words(story)
        story = " ".join(s for s in re.split(r"(?<=[.!?])\s+", story) if s and not leak_words(s))
    thought = schemas.clean_line(thought, 160)

    feelings, seen = [], set()
    raw_feelings = data.get("feelings") or []
    if not isinstance(raw_feelings, list):
        return invalid("feelings must be a list")
    for f in raw_feelings:
        if not isinstance(f, dict):
            continue
        name, change = f.get("name"), f.get("change")
        if name in names and change in CHANGES and name not in seen:
            feelings.append((name, change))
            seen.add(name)
    feelings = feelings[:MAX_FEELINGS]

    goal = None
    goal_text, need = data.get("goal"), data.get("goal_need")
    if needs and isinstance(goal_text, str) and need in needs:
        from genesis.mind.goals import too_close

        goal_text = schemas.clean_line(goal_text, 160)
        if leak_words(goal_text):
            dropped += leak_words(goal_text)
        elif goal_text and not too_close(goal_text):  # M8f: their words, not ours
            goal = (need, goal_text)
    if dropped and record is not None:
        record["error"] = "leak dropped: " + ", ".join(sorted(set(dropped)))

    if not beliefs and not story and not feelings and not goal:
        return invalid("reflected nothing")
    if record is not None:
        record.update(
            choice=f"{len(beliefs)} beliefs, {len(feelings)} feelings",
            say=" | ".join(beliefs) or story,
            thought=thought,
        )
    return Reflection(thought, beliefs, story, feelings, record, goal)


def apply(ctx, sim, agent, result: Reflection, people: dict):
    from genesis.society.interaction import feel

    held = {m.text.lower() for m in agent.stream.beliefs(1000)}
    new_beliefs = [b for b in result.beliefs if b.lower() not in held]
    event_id = ctx.log.emit(
        ctx.tick,
        ev.REFLECTED,
        actors=(agent.id,),
        targets=tuple(people[name].id for name, _ in result.feelings),
        position=agent.position,
        data={
            "beliefs": new_beliefs,
            "story": result.story,
            "feelings": [list(f) for f in result.feelings],
            "thought": result.thought,
            "goal": list(result.goal) if result.goal else None,
        },
    )
    agent.memory.remember_event(event_id)
    if result.record is not None:
        result.record["event_id"] = event_id

    for belief in new_beliefs:
        sim.add_memory(
            agent,
            ctx.tick,
            source_event_id=event_id,
            kind=memory.BELIEF,
            text=belief,
            importance=7,
            about=(),
            source_agent_id=None,
        )
    if result.story:
        agent.story = result.story
    if result.goal is not None and agent.stage != "child":
        from genesis.mind import goals

        need, words = result.goal
        if agent.goal is not None and agent.goal["need"] == need:
            agent.goal["text"], agent.goal["how"] = words, "own words"  # same aim, own words
        elif need in goals.REPEATABLE or not goals.reached(agent, ctx, need, start={}):
            goals.set_goal(ctx, agent, need, words, how="own words", cause=event_id)

    step = ctx.world.rules.reflection_feeling_step
    for name, change in result.feelings:
        other = people[name]
        old = agent.relationship(other.id, other.name).affinity
        # Relationship.apply moves feelings up to 2x faster back towards neutral; divide that
        # out so a night's thinking never moves a feeling by more than `step`.
        boost = (1 - old) if change == "warmer" else (1 + old)
        delta = step / max(1.0, boost)
        feel(ctx, agent, other, {"affinity": delta if change == "warmer" else -delta}, event_id)
