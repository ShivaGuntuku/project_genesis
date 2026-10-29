"""Drives, goals and long-term projects (M8c). See docs/15 (15.1).

- **Drives** (0..1) come from genes and life: curiosity, ambition, care (family, group) and
  legacy (growing with age).
- A **goal** is something an agent wants to achieve, in words (the rules' plain words, or the
  agent's own, written in its nightly reflection) plus a **need** the simulation understands.
  The need lets the world judge success; the words are the agent's.
- In free time, an agent works on its goal (see `DecisionEngine._free_time`). Every night the
  simulation checks whether the goal was reached, or has gone on too long and is given up.
"""

from genesis.culture import inventions as inv
from genesis.culture.materials import SOLVES
from genesis.history import events as ev

# need -> the rules' plain words for it (an LLM reflection may rewrite them).
NEEDS: dict[str, str] = {
    "carry water": "I want a way to carry more water when the lake is far.",
    "carry food": "I want a way to carry more food with me.",
    "gather food": "I want to get more out of every meal I gather.",
    "win fights": "I never want to lose a fight again.",
    "rest": "I want a place of my own where I can sleep well.",
    "be respected": "I want the others to look up to me.",
    "provide": "I want my people never to go hungry.",
    "be remembered": "I want what I know to live on in others.",
    "know the land": "I want to know every corner of the valley.",
}
DRIVES = ("curiosity", "ambition", "care", "legacy")
DRIVE_WORDS = {
    "curiosity": "finding out how things work",
    "ambition": "standing above others",
    "care": "looking after your people",
    "legacy": "leaving something behind",
}
FED_TO_PROVIDE = 20  # meals given or stored, to reach "provide"
TAUGHT_TO_BE_REMEMBERED = 3  # lessons that stuck
RESPECTED_BY = 3  # people who respect them (or lead a group)
RESPECT_LEVEL = 0.4
LAND_SHARE = 0.6  # of the walkable land seen with their own eyes
REPEATABLE = ("provide", "be remembered")  # measured from when the goal is set


# =================================================================
# Drives
# =================================================================


def drives(agent, ctx) -> dict[str, float]:
    g = agent.genome
    alive_child = any((c := ctx.agents.get(cid)) is not None and c.alive for cid in agent.children)
    bound = agent.group_id is not None or agent.partner is not None
    age_share = min(1.0, agent.age(ctx.tick) / max(1, agent.lifespan(ctx.world)))
    return {
        "curiosity": round(g.curiosity, 3),
        "ambition": round(g.ambition, 3),
        "care": round(min(1.0, 0.5 * g.generosity + 0.25 * alive_child + 0.25 * bound), 3),
        "legacy": round(min(1.0, age_share * (0.4 + 0.6 * max(g.ambition, g.generosity))), 3),
    }


def strongest_drive(agent, ctx) -> str:
    d = drives(agent, ctx)
    return max(DRIVES, key=lambda k: d[k])  # first of equals: deterministic


# =================================================================
# Choosing a goal (rules)
# =================================================================


def needs_allowed(rules) -> list[str]:
    return [n for n in NEEDS if rules.inventions or n not in SOLVES]


def seen_share(agent, world) -> float:
    walkable = world.walkable
    return len(agent.memory.seen_positions & walkable) / max(1, len(walkable))


def scores(agent, ctx) -> dict[str, float]:
    """How strongly each need pulls at this agent now (0 = not at all)."""
    r, world = ctx.world.rules, ctx.world
    d = drives(agent, ctx)
    out = {}
    for need in needs_allowed(r):
        if need not in REPEATABLE and reached(agent, ctx, need, start={}):
            continue
        given_up = agent.asked.get(f"gave_up:{need}")
        if given_up is not None and ctx.tick - given_up < world.calendar.ticks(
            days=2 * r.goal_every_days
        ):
            continue
        done = agent.asked.get(f"reached:{need}")  # a repeatable aim rests for a year
        if done is not None and ctx.tick - done < world.calendar.ticks(
            days=world.calendar.days_per_year
        ):
            continue
        if need in SOLVES:
            pressure = min(1.5, agent.problems.get(need, 0.0) / r.problem_threshold)
            out[need] = pressure * (0.5 + 0.5 * d["curiosity"])
        elif need == "be respected":
            out[need] = 0.8 * d["ambition"]  # M8f: it crowded out every other aim
        elif need == "provide":
            if agent.group_id is not None or agent.children or agent.partner is not None:
                out[need] = d["care"]
        elif need == "be remembered":
            if _someone_to_teach(agent, ctx):
                out[need] = d["legacy"]
        elif need == "know the land":
            out[need] = d["curiosity"] * (1 - seen_share(agent, world) / LAND_SHARE)
    return {k: round(v, 4) for k, v in out.items() if v > 0}


def _someone_to_teach(agent, ctx) -> bool:
    """Someone grown and alive who does not yet know what this agent knows."""
    if not agent.knowledge:
        return False
    return any(
        o.alive and o is not agent and o.stage != "child" and agent.knowledge - o.knowledge
        for o in ctx.agents.values()
    )


def choose(agent, ctx) -> str | None:
    s = scores(agent, ctx)
    if not s:
        return None
    best = max(s, key=s.get)  # first of equals, in NEEDS order
    return best if s[best] >= ctx.world.rules.goal_min_drive else None


# =================================================================
# Setting, reaching, giving up
# =================================================================


NEED_HINTS = {  # how a rules goal is shown to the LLM: a feeling, not a sentence to copy
    "carry water": "running short of water far from the lake",
    "carry food": "running short of food far from the berries",
    "gather food": "how slow gathering is",
    "win fights": "losing fights",
    "rest": "not resting well",
    "be respected": "how others see you",
    "provide": "your people having enough",
    "be remembered": "what will be left of you",
    "know the land": "the land you have not seen",
}


def too_close(text: str) -> bool:
    """A goal that just repeats the rules' words (or the hint) is not their own."""
    import re

    def words(t):
        return set(re.findall(r"[a-z]+", t.lower())) - {"i", "to", "the", "a", "my", "of", "and"}

    mine = words(text)
    if not mine:
        return True
    for t in list(NEEDS.values()) + list(NEED_HINTS.values()):
        other = words(t)
        if len(mine & other) / len(mine | other) >= 0.6:
            return True
    return False


def _snapshot(agent) -> dict:
    return {"fed": agent.deeds.get("fed", 0), "taught": agent.deeds.get("taught", 0)}


def set_goal(ctx, agent, need: str, text: str | None = None, how: str = "rules", cause=None):
    old = agent.goal
    text = text or NEEDS[need]
    agent.goal = {"need": need, "text": text, "tick": ctx.tick, "how": how, **_snapshot(agent)}
    event_id = ctx.log.emit(
        ctx.tick,
        ev.GOAL_SET,
        actors=(agent.id,),
        position=agent.position,
        data={
            "need": need,
            "text": text,
            "how": how,
            "replaced": old["text"] if old else None,
        },
        cause_id=cause,
    )
    agent.memory.remember_event(event_id)
    return event_id


def reached(agent, ctx, need: str, start: dict) -> bool:
    if need in SOLVES:
        return bool(inv.known_for(agent, ctx.world.concepts, SOLVES[need]))
    if need == "be respected":
        if any(g.leader == agent.id and g.ended_tick is None for g in ctx.groups.values()):
            return True
        fans = 0
        for other in ctx.agents.values():
            rel = other.relationships.get(agent.id) if other.alive else None
            if rel is not None and rel.respect >= RESPECT_LEVEL:
                fans += 1
        return fans >= RESPECTED_BY
    if need == "provide":
        return agent.deeds.get("fed", 0) - start.get("fed", 0) >= FED_TO_PROVIDE
    if need == "be remembered":
        return agent.deeds.get("taught", 0) - start.get("taught", 0) >= TAUGHT_TO_BE_REMEMBERED
    if need == "know the land":
        return seen_share(agent, ctx.world) >= LAND_SHARE
    return False


def _end(ctx, agent, kind: str, reason: str | None = None):
    goal = agent.goal
    days = ctx.world.calendar.days_between(goal["tick"], ctx.tick)
    event_id = ctx.log.emit(
        ctx.tick,
        kind,
        actors=(agent.id,),
        position=agent.position,
        data={"need": goal["need"], "text": goal["text"], "days": days, "reason": reason},
    )
    agent.memory.remember_event(event_id)
    if kind == ev.GOAL_ABANDONED:
        agent.asked[f"gave_up:{goal['need']}"] = ctx.tick
    else:
        agent.deeds["goals"] = agent.deeds.get("goals", 0) + 1
        agent.asked[f"reached:{goal['need']}"] = ctx.tick
    agent.goal = None
    return event_id


def nightly(ctx, sim):
    """Check every adult's goal; give a goal to those without one (spread over the days)."""
    r, cal = ctx.world.rules, ctx.world.calendar
    every = max(1, round(r.goal_every_days))
    day = cal.day_index(ctx.tick)
    for agent in sim.population.get_alive():
        if agent.stage == "child":
            continue
        goal = agent.goal
        if goal is not None:
            if reached(agent, ctx, goal["need"], goal):
                _end(ctx, agent, ev.GOAL_REACHED)
            elif ctx.tick - goal["tick"] >= cal.ticks(days=r.goal_give_up_days):
                _end(ctx, agent, ev.GOAL_ABANDONED, "it was taking too long")
            elif goal["need"] not in needs_allowed(r):
                _end(ctx, agent, ev.GOAL_ABANDONED, "it no longer made sense")
            continue
        if (day + agent.id) % every != 0:
            continue
        need = choose(agent, ctx)
        if need is not None:
            set_goal(ctx, agent, need)


# =================================================================
# Deeds: what the goal checks count (from events, once each)
# =================================================================


def count_deeds(event, agents: dict):
    def add(agent_id, key):
        a = agents.get(agent_id)
        if a is not None:
            a.deeds[key] = a.deeds.get(key, 0) + 1

    d = event.data
    if event.type == ev.GROUP_STORE and d.get("action") == "put in":
        add(event.actors[0], "fed")
    elif event.type == ev.MESSAGE and d.get("act") == "give":
        add(event.actors[0], "fed")
    elif event.type == ev.LEARNED and d.get("learned") and event.targets:
        add(event.targets[0], "taught")
    elif event.type == ev.INVENTED:
        add(event.actors[0], "invented")
