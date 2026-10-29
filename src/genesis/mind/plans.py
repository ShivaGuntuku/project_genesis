"""Each person's own plan for the coming days (M8f).

Every `llm.plan_every_days`, each awake adult is asked by the LLM what to do with the coming
days, from options the simulation builds from their life (their goal, what they know how to
make, their talent's work, friends, trade, their valley's troubles). The answer and its reason
are theirs; the rules then lean their free time that way (needs always come first). This is
how the LLM steers what people *do*, not only what they say.
"""

import json

from genesis.history import events as ev
from genesis.llm.mind import Question, ask_many

CALLINGS = {  # a talent's work, as offered in a plan
    "healer": "Look after anyone who is hurt or sick",
    "storyteller": "Show others what you know how to make",
    "tinkerer": "Work things out with your hands: try to make something new",
    "keen-eyed": "Scout the land, so everyone knows it better",
    "water-sense": "Search out water and show others where it is",
    "strong-armed": "Carry food and materials for your people",
    "tireless": "Work long days for your people",
}


def due(agent, ctx) -> bool:
    r, cal = ctx.world.rules, ctx.world.calendar
    every = getattr(getattr(ctx.mind, "config", None), "plan_every_days", 0)
    if not every or agent.stage == "child" or not agent.alive or agent.sleeping:
        return False
    if agent.current_action is not None and agent.current_action.value == "sleep":
        return False
    plan = agent.plan
    if plan is None:
        return (cal.day_index(ctx.tick) + agent.id) % max(1, round(every)) == 0
    return ctx.tick >= plan["until"] and r is not None


def options(agent, ctx) -> list[tuple[str, str]]:
    """(key, words) the person could set out to do these days."""
    from genesis.culture import inventions as inv

    world, r = ctx.world, ctx.world.rules
    out = [("explore", "Go and see land you don't know yet")]
    if agent.goal:
        out.append(("goal", f'Work towards what you want: "{agent.goal["text"]}"'))
    if r.inventions:
        out.append(("tinker", "Gather materials and try to make something new"))
        for cid in sorted(agent.knowledge):
            c = world.concepts.get(cid)
            if (
                c
                and c.lost_tick is None
                and not inv.has_item_for(agent, world.concepts, c.function)
            ):
                out.append((f"make:{cid}", f"Make a {c.name} (it {c.function})"))
                break
    if r.talents and agent.talent in CALLINGS:
        out.append(("calling", CALLINGS[agent.talent]))
    friends = sorted(
        (rel for rel in agent.relationships.values() if rel.label() in ("friend", "close friend")),
        key=lambda rel: (-rel.affinity, rel.target_id),
    )
    for rel in friends[:2]:
        other = ctx.agents.get(rel.target_id)
        if other is not None and other.alive:
            out.append((f"company:{other.id}", f"Spend time with {other.name}"))
    if r.trade:
        out.append(("trade", "Trade what you have for what you need"))
    if agent.group_id is not None:
        out.append(("provide", "Bring food to your group's store"))
    here = world.region_at.get(agent.position)
    if here in world.troubled:
        for name in world.region_names:
            if name != here and name not in world.troubled:
                out.append((f"move:{name}", f"Leave for {name}"))
    out.append(("rest", "Take it easy and rest"))
    return out


def question(ctx, agent):
    """The question for this person's plan, and the options it offers."""
    import zlib

    from genesis.llm import persona

    # Listed in an order of their own (the first option tends to be picked): shuffled from the
    # tick and the person, without using the world's chance, so replays agree.
    opts = sorted(
        options(agent, ctx),
        key=lambda o: zlib.crc32(f"{ctx.tick}:{agent.id}:{o[0]}".encode()),
    )
    keys = [k for k, _ in opts]

    def prompt():
        return "\n".join(
            [
                persona.fact_block(agent, ctx),
                "Your story: " + persona.self_story(agent, ctx),
                persona.beliefs(agent, 5),
                "",
                "Think about the coming days. What will you set out to do?",
                *[f"- {k}: {words}" for k, words in opts],
                'Answer in JSON: "thought", "choice" (one key from the list), "why" (your own '
                "reason, under 20 words).",
            ]
        )

    def parse(text, record):
        from genesis.culture.words import leak_words
        from genesis.llm import schemas

        try:
            data = json.loads(text)
            choice = data.get("choice")
            why = schemas.clean_line(str(data.get("why", "")), 160)
        except (ValueError, TypeError, AttributeError):
            choice, why = None, ""
        if choice not in keys:
            if record is not None:
                record["outcome"], record["error"] = "invalid", f"no such option {choice!r}"
            return None
        if leak_words(why):
            if record is not None:
                record["error"] = "leak dropped: " + ", ".join(sorted(set(leak_words(why))))
            why = ""
        if record is not None:
            record.update(choice=choice, say=why, thought=str(data.get("thought", ""))[:160])
        return choice, why

    q = Question(
        kind="plan",
        agent=agent,
        other=None,
        prompt=prompt,
        schema={
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "choice": {"type": "string", "enum": keys},
                "why": {"type": "string"},
            },
            "required": ["thought", "choice", "why"],
        },
        parse=parse,
        max_tokens=100,
        budgeted=False,
    )
    return q, opts


def apply(ctx, agent, answer, opts):
    if answer is None:
        return None
    choice, why = answer
    label = dict(opts)[choice]
    every = ctx.mind.config.plan_every_days
    agent.plan = {
        "focus": choice,
        "label": label,
        "why": why,
        "tick": ctx.tick,
        "until": ctx.tick + ctx.world.calendar.ticks(days=every),
    }
    if choice.startswith("move:"):
        agent.plan["move"] = choice.split(":", 1)[1]
    event_id = ctx.log.emit(
        ctx.tick,
        ev.PLANNED,
        actors=(agent.id,),
        position=agent.position,
        data={"focus": choice, "label": label, "why": why},
    )
    agent.memory.remember_event(event_id)
    return event_id


def ask(ctx, agent):
    q, opts = question(ctx, agent)
    (answer,) = ask_many(ctx.mind, ctx, [q])
    return apply(ctx, agent, answer, opts)


def ask_all(ctx, agents):
    """Everyone whose plan is due, asked together (M8.5 workers), applied in order."""
    due_now = [a for a in agents if due(a, ctx)]
    if not due_now:
        return
    built = [question(ctx, a) for a in due_now]
    answers = ask_many(ctx.mind, ctx, [q for q, _ in built])
    for agent, (_, opts), answer in zip(due_now, built, answers, strict=True):
        apply(ctx, agent, answer, opts)


def focus(agent) -> str | None:
    return agent.plan["focus"] if agent.plan else None
