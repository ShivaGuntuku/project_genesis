"""Talents and professions (M8d). See docs/15 (15.3).

Every agent is born with one talent: founders at random, children from one parent (with a
small chance of a new one). Talents only have effects when the world's `talents` rule is on.
A talent gives about +50% in its field, and its skill is learned 1.5x faster.

Professions are never assigned. Each agent's recent work is counted (fading over time) and
whoever spends their days mostly on one kind of work, and is skilled at it, is seen as that.
"""

import random

TALENTS = (
    "keen-eyed",  # sees further
    "tireless",  # tires slower
    "healer",  # heals others more, learns healing faster
    "tinkerer",  # experiments work more often
    "storyteller",  # teaches better
    "water-sense",  # finds water
    "strong-armed",  # carries more, fights better
)
# Green hand (plants grow faster) waits for farming.
DESCRIPTIONS = {
    "keen-eyed": "you see further than others",
    "tireless": "you tire more slowly than others",
    "healer": "you have healing hands",
    "tinkerer": "you are good at working things out with your hands",
    "storyteller": "people listen when you explain things",
    "water-sense": "you can sense where water is",
    "strong-armed": "you are very strong",
}
SKILL_OF = {  # the skill a talent learns faster
    "keen-eyed": "scouting",
    "healer": "healing",
    "tinkerer": "crafting",
    "storyteller": "teaching",
    "water-sense": "water_finding",
    "strong-armed": "combat",
}
# M8f: the work a talent leads to (to see whether talents become callings).
WORK_OF = {
    "keen-eyed": "scout",
    "water-sense": "scout",
    "healer": "healer",
    "tinkerer": "maker",
    "storyteller": "teacher",
    "strong-armed": "provider",
    "tireless": "provider",
}
MUTATION = 0.1

# Work, as the history shows it -> what others would call someone who does it all day.
PROFESSIONS = ("maker", "teacher", "healer", "trader", "scout", "provider", "fighter")
WORK_FADE = 0.99  # a day
PROFESSION_MIN_WORK = 5.0
PROFESSION_SHARE = 0.4
PROFESSION_SKILL = {  # the skill an expert in it has
    "maker": "crafting",
    "teacher": "teaching",
    "healer": "healing",
    "trader": "persuasion",
    "scout": "scouting",
    "provider": "foraging",
    "fighter": "combat",
}
PROFESSION_MIN_SKILL = 0.25


def born_with(seed: int, agent_id: int, parents=()) -> str:
    """One talent, from its own seeded stream (so it never shifts the world's chance)."""
    rng = random.Random(f"talent:{seed}:{agent_id}")
    inherited = [p.talent for p in parents if getattr(p, "talent", None)]
    if inherited and rng.random() >= MUTATION:
        return rng.choice(inherited)
    return rng.choice(TALENTS)


def has(agent, talent: str, rules) -> bool:
    return bool(rules.talents) and agent.talent == talent


def learning(agent, skill: str, rules) -> float:
    """Practice multiplier for a skill."""
    if rules.talents and agent.talent and SKILL_OF.get(agent.talent) == skill:
        return 2.0  # M8f: a talent shows (was 1.5)
    return 1.0


# =================================================================
# Professions
# =================================================================


def count_work(event, agents: dict):
    """Called once per event: what kind of work each person did."""
    from genesis.history import events as ev

    def add(agent_id, kind, amount=1.0):
        a = agents.get(agent_id)
        if a is not None:
            a.work[kind] = round(a.work.get(kind, 0.0) + amount, 4)

    d = event.data
    t = event.type
    if t in (ev.EXPERIMENTED, ev.INVENTED, ev.MADE, ev.BUILT):
        add(event.actors[0], "maker")
    elif t == ev.LEARNED and d.get("learned") and event.targets:
        add(event.targets[0], "teacher")
    elif t == ev.DISCOVERED and d.get("how") != "told":
        add(event.actors[0], "scout")
    elif t == ev.GROUP_STORE and d.get("action") == "put in":
        add(event.actors[0], "provider", 0.5)
    elif t == ev.FIGHT:
        add(event.actors[0], "fighter")
    elif t == ev.MESSAGE:
        act = d.get("act")
        if act == "heal":  # M8f: a gift is kindness, not work (no longer counts as providing)
            add(event.actors[0], "healer", 2.0)
        elif act == "reply" and d.get("payload", {}).get("answer") == "accept_trade":
            add(event.actors[0], "trader")
            add(event.targets[0], "trader")


def fade_work(agent):
    for k in list(agent.work):
        agent.work[k] = round(agent.work[k] * WORK_FADE, 4)
        if agent.work[k] < 0.05:
            del agent.work[k]


def profession(agent) -> str | None:
    """What others would call them, from what they actually spend their days doing."""
    return profession_of(agent.work, agent.abilities.values)


def profession_of(work: dict, skills: dict) -> str | None:
    if not work:
        return None
    total = sum(work.values())
    kind = max(PROFESSIONS, key=lambda k: work.get(k, 0.0))
    amount = work.get(kind, 0.0)
    if amount < PROFESSION_MIN_WORK or amount / total < PROFESSION_SHARE:
        return None
    if skills.get(PROFESSION_SKILL[kind], 0.0) < PROFESSION_MIN_SKILL:
        return None
    return kind
