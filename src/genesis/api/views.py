"""What the live viewer receives: snapshots, per-tick frames, agent details, cause chains.

Everything here only READS the simulation. See docs/14-live-viewer.md.
"""

from genesis.agents.abilities import describe_skill
from genesis.history import events as ev
from genesis.history.describe import clock, describe
from genesis.mind.decision import ActionType
from genesis.mind.memory import ExplorationGoal
from genesis.society import family
from genesis.world.world import Position

TERRAIN_CODES = {"plains": "p", "forest": "f", "mountain": "m", "water": "w", "peak": "k"}
ROUTINE = {ev.NEED_BECAME_PRESSING, ev.NEED_SATISFIED}
ROUTINE_ACTS = {"chat", "greet"}
FAMILY_ORDER = ["partner", "parent", "child", "sibling", "grandparent", "grandchild"]
LINK_LABELS = {"friend", "close friend", "rival", "enemy", "partner", "family"}

CHAT_LINES = ("Nice day.", "Seen the lake today?", "Hmm.", "How are you?", "Long walk, this.")
REPLY_LINES = {
    "don't know": "No idea, sorry.",
    "have none": "I have none.",
    "no": "No.",
    "refuse": "Never!",
    "won't say": "I won't tell you.",
    "agree": "Yes. Let's.",
    "join": "I'm with you.",
    "decline_join": "No, thank you.",
    "obey": "As you say.",
    "refuse_order": "No.",
    "decline": "Not now.",
    "accept_trade": "Done.",
    "refuse_trade": "No deal.",
}


# =================================================================
# Snapshot (sent once when a browser connects)
# =================================================================


def snapshot(sim, paused: bool, speed: float) -> dict:
    world = sim.world
    rows = []
    for y in range(world.height):
        rows.append(
            "".join(TERRAIN_CODES[world.terrain[Position(x, y)].value] for x in range(world.width))
        )
    names = {a.id: a.name for a in sim.population.agents}
    return {
        "type": "snapshot",
        "world": {
            "name": world.name,
            "width": world.width,
            "height": world.height,
            "tiles": rows,
            "food": [
                {"id": f.id, "x": f.position.x, "y": f.position.y, "max": f.max_quantity}
                for f in world.food_sources
            ],
            "start_hour": sim.config.start_hour,
        },
        "agents": [agent_info(a, sim) for a in sim.population.agents],
        "frame": frame(sim, [], names),
        "events": [event_item(e, names, sim) for e in sim.log.recent[-40:]],
        "paused": paused,
        "speed": speed,
    }


def agent_info(agent, sim) -> dict:
    """Fixed facts about an agent: who they are and how they look."""
    g = agent.genome
    return {
        "id": agent.id,
        "name": agent.name,
        "generation": agent.generation,
        "lineage": lineage(agent, sim),
        "looks": {"skin": g.skin, "hair": g.hair, "eyes": g.eyes, "strength": g.strength},
    }


def lineage(agent, sim) -> int:
    """The founder this agent descends from (first parent's line). Sets clothes colour."""
    seen = set()
    while agent.parents and agent.id not in seen:
        seen.add(agent.id)
        parent = sim.population.get_by_id(agent.parents[0])
        if parent is None:
            break
        agent = parent
    return agent.id


# =================================================================
# Frame (sent after every tick)
# =================================================================


def frame(sim, new_events, names) -> dict:
    rules = sim.world.rules
    speakers = {e.actors[0] for e in new_events if e.type == ev.MESSAGE}
    return {
        "type": "tick",
        "tick": sim.tick_count,
        "clock": clock(sim.tick_count, sim.world.calendar),
        "hour": sim.hour,
        "alive": len(sim.population.get_alive()),
        "agents": [
            agent_state(a, rules, a.id in speakers, sim.tick_count) for a in sim.population.agents
        ],
        "food": [f.quantity for f in sim.world.food_sources],
        "dryLakes": sorted(sim.world.dry_lakes),  # M7 droughts (lakes numbered like World)
        "links": links(sim),
        "events": [event_item(e, names, sim) for e in new_events],
        # Born since the last frame: the browser adds their sprites (M6).
        "newAgents": [
            agent_info(sim.population.get_by_id(e.actors[0]), sim)
            for e in new_events
            if e.type == ev.BIRTH
        ],
    }


def agent_state(a, rules, spoke: bool, tick: int = 0) -> dict:
    return {
        "id": a.id,
        "x": a.position.x,
        "y": a.position.y,
        "facing": a.facing,
        "action": a.current_action.value if a.current_action else None,
        "stage": a.stage,
        "alive": a.alive,
        "health": round(a.health),
        "hunger": round(a.hunger),
        "thirst": round(a.thirst),
        "energy": round(a.energy),
        "icon": icon(a, rules, spoke, tick),
        "group": a.group_id,
    }


def icon(a, rules, spoke: bool, tick: int = 0) -> str | None:
    if not a.alive:
        return None
    if a.sleeping:
        return "sleep"
    fought = [t for k, t in a.asked.items() if k.endswith(":fight")]
    if fought and tick - max(fought) <= 4:  # just fought: still fuming
        return "angry"
    if spoke:
        return "talk"
    if a.thirst >= rules.seek_threshold:
        return "thirsty"
    if a.hunger >= rules.seek_threshold:
        return "hungry"
    if a.current_action == ActionType.EXPLORE and a.memory.get_exploration_goal() in (
        ExplorationGoal.FIND_WATER,
        ExplorationGoal.FIND_FOOD,
    ):
        return "search"
    return None


def links(sim) -> list:
    """Directional labelled relationships among the living: [from, to, label]."""
    alive = {a.id for a in sim.population.get_alive()}
    out = []
    for a in sim.population.agents:
        if a.id not in alive:
            continue
        for target_id in sorted(a.relationships):
            if target_id not in alive:
                continue
            # Family ties show first (gold); feelings otherwise.
            if a.partner == target_id:
                label = "partner"
            elif target_id in a.parents or target_id in a.children:
                label = "family"
            else:
                label = a.relationships[target_id].label()
            if label in LINK_LABELS:
                out.append([a.id, target_id, label])
    return out


def event_item(e, names, sim) -> dict:
    act = e.data.get("act") if e.type == ev.MESSAGE else None
    return {
        "id": e.id,
        "type": e.type,
        "clock": clock(e.tick, sim.world.calendar),
        "text": describe(e, names),
        "actors": list(e.actors),
        "targets": list(e.targets),
        "x": e.x,
        "y": e.y,
        "act": act,
        "lie": e.type == ev.MESSAGE and e.data.get("truthful") is False,
        "bubble": (e.data.get("line") or bubble(e, names)) if act else None,
        "thought": e.data.get("thought") if act else None,
        "voiced": bool(act and e.data.get("line")),
        "float": float_text(e),
        "routine": e.type in ROUTINE or act in ROUTINE_ACTS,
        "hasCause": e.cause_id is not None,
    }


def bubble(e, names) -> str:
    """Speech bubble for a rule-based act (when the agent spoke in its own words, the
    bubble shows those words instead; see event_item)."""
    act, p = e.data["act"], e.data.get("payload", {})
    listener = names.get(e.targets[0], "") if e.targets else ""
    match act:
        case "greet":
            return f"Hello, {listener}!"
        case "chat":
            return CHAT_LINES[e.id % len(CHAT_LINES)]
        case "share_info":
            return f"{p.get('resource', '').capitalize()} at ({p.get('x')},{p.get('y')})!"
        case "request":
            if "info" in p:
                return f"Where is {p['info']}?"
            return f"Can you spare some {p.get('item')}?"
        case "give":
            return "Fine, take it..." if p.get("under_threat") else "Here, take this."
        case "gossip":
            return f"{p.get('about_name', 'They')} {p.get('claim')}."
        case "praise":
            return "You're wonderful!"
        case "insult":
            return "Get away from me!"
        case "threaten":
            return f"Give me your {p.get('demand')}!"
        case "propose":
            return "Shall we raise a child together?"
        case "found" | "invite":
            return "Stand with us!"
        case "order":
            return "Do as I say."
        case "challenge":
            return "I should lead us!"
        case "attack":
            return "Enough!"
        case "teach":
            return "Watch how I make this."
        case "trade":
            return "A trade?"
        case "repay":
            return "As I promised."
        case "heal":
            return "Let me see that wound."
        case "reply":
            return REPLY_LINES.get(p.get("answer"), "...")
    return "..."


def float_text(e) -> str | None:
    d = e.data
    match e.type:
        case ev.SKILL_IMPROVED:
            return f"+ {d.get('description')}"
        case ev.DISCOVERED:
            return f"found {d.get('resource')}!"
        case ev.LIFE_STAGE:
            return "now an elder" if d.get("stage") == "elder" else "came of age"
        case ev.LIE_DISCOVERED:
            return "Liar!"
        case ev.RELATION_CHANGED:
            to = d.get("to")
            if to in ("friend", "close friend"):
                return f"♥ {to}"
            if to in ("rival", "enemy"):
                return f"⚡ {to}"
            return None
        case ev.DIED:
            return f"† {d.get('cause')}"
        case ev.BIRTH:
            return f"👶 {d.get('name')} is born!"
        case ev.FIGHT:
            return "⚔ fight!"
        case ev.FOUNDED_GROUP:
            return f"⚑ {d.get('name')}"
        case ev.LEFT_GROUP:
            return "left the group" if d.get("reason") != "died" else None
        case ev.LEADER_CHALLENGED:
            return "♛ new leader" if d.get("won") else "challenge failed"
        case ev.CONCEIVED:
            return "♥ expecting"
        case ev.GRIEVED:
            return "grieving…"
        case ev.REFLECTED:
            return "💭 new belief" if d.get("beliefs") else None
        case ev.INVENTED:
            return f"💡 {d.get('name')}!"
        case ev.BUILT:
            return f"⌂ {d.get('name')}"
        case ev.LEARNED:
            return f"learned {d.get('name')}" if d.get("learned") else None
        case ev.CONCEPT_LOST:
            return None  # no actor: shown in the event feed
        case ev.PROMISE_BROKEN:
            return "broke a promise"
        case ev.FELL_SICK:
            return "🤒 sick"
        case ev.BEAST_ATTACK:
            return "beasts driven off" if d.get("driven_off") else "🐾 attacked!"
        case ev.MIGRATED:
            return f"→ {d.get('to')}"
        case ev.GOAL_SET:
            return "🎯 new goal"
        case ev.GOAL_REACHED:
            return "★ goal reached!"
    return None


# =================================================================
# Inspect panel and "why?"
# =================================================================


def agent_detail(sim, agent_id: int, store) -> dict | None:
    agent = sim.population.get_by_id(agent_id)
    if agent is None:
        return None
    world, r = sim.world, sim.world.rules
    g = agent.genome
    names = {a.id: a.name for a in sim.population.agents}
    start = sim.world.calendar
    at = agent.died_tick if agent.died_tick is not None else sim.tick_count

    relationships = []
    for other_id in sorted(agent.relationships, key=lambda i: -agent.relationships[i].affinity):
        rel = agent.relationships[other_id]
        other = sim.population.get_by_id(other_id)
        back = other.relationships.get(agent.id) if other else None
        relationships.append(
            {
                "id": other_id,
                "name": rel.target_name,
                "alive": bool(other and other.alive),
                "label": rel.label(),
                "theirLabel": back.label() if back else None,
                "affinity": round(rel.affinity, 2),
                "trust": round(rel.trust, 2),
                "gratitude": round(rel.gratitude, 2),
                "grievance": round(rel.grievance, 2),
                "fear": round(rel.fear, 2),
                "met": rel.met,
            }
        )

    events = [event_item(e, names, sim) for e in store.events(agent_id=agent.id, limit=25)]

    return {
        **agent_info(agent, sim),
        "alive": agent.alive,
        "stage": agent.stage,
        "causeOfDeath": agent.cause_of_death,
        "died": clock(agent.died_tick, start) if agent.died_tick is not None else None,
        "ageDays": world.calendar.age_days(agent.age(at)),
        "lifespanDays": round(agent.lifespan(world) / world.calendar.ticks_per_day),
        "position": [agent.position.x, agent.position.y],
        "action": agent.current_action.value if agent.current_action else None,
        "needs": {
            "health": round(agent.health),
            "hunger": round(agent.hunger),
            "thirst": round(agent.thirst),
            "energy": round(agent.energy),
        },
        "carrying": dict(agent.inventory),
        "character": g.traits(),
        "strengths": g.strengths(),
        "weaknesses": g.weaknesses(),
        "skills": [describe_skill(k, v) for k, v in agent.abilities.trained().items()],
        "genes": g.describe(),
        "vision": agent.vision_radius(world),
        "knowsFood": len(agent.memory.get_known_locations("food")),
        "knowsWater": bool(agent.memory.get_known_locations("water")),
        "relationships": relationships,
        "events": events,
        "family": [
            {
                "id": other_id,
                "name": names.get(other_id, "?"),
                "relation": rel,
                "alive": bool(sim.population.get_by_id(other_id).alive),
            }
            for other_id, rel in sorted(
                family.family(agent, {a.id: a for a in sim.population.agents}).items(),
                key=lambda item: (FAMILY_ORDER.index(item[1]), item[0]),
            )
        ],
        "expecting": agent.pregnant,
        "group": _group_of(sim, agent),
        "story": agent.story,
        "beliefs": [
            {"text": m["text"], "clock": clock(m["tick"], start)}
            for m in reversed(store.memories(agent.id, kind="belief", limit=5))
        ],
        "memories": [
            {"text": m.text, "clock": clock(m.tick, start), "importance": m.importance}
            for m in sorted(agent.stream.items, key=lambda m: (-m.importance, -m.tick))[:6]
        ],
        "seekThreshold": r.seek_threshold,
        **_goal_view(sim, agent),
        "talent": agent.talent if r.talents else None,
        "profession": _profession(agent) if r.talents else None,
    }


def _profession(agent):
    from genesis.agents import talents

    return talents.profession(agent)


def _goal_view(sim, agent) -> dict:
    """M8c: what drives them, and their goal (nothing when goals are off)."""
    if not sim.world.rules.goals:
        return {"goal": None, "drives": None}
    from genesis.mind import goals
    from genesis.simulation.context import TickContext

    ctx = TickContext(
        world=sim.world,
        tick=sim.tick_count,
        log=sim.log,
        rng=None,
        agents={a.id: a for a in sim.population.agents},
        groups=sim.groups,
    )
    goal = agent.goal
    return {
        "goal": {
            "need": goal["need"],
            "text": goal["text"],
            "since": clock(goal["tick"], sim.world.calendar),
            "ownWords": goal.get("how") == "own words",
        }
        if goal
        else None,
        "drives": goals.drives(agent, ctx),
    }


def why(store, sim, event_id: int) -> dict | None:
    chain = store.cause_chain(event_id)
    if not chain:
        return None
    names = {a.id: a.name for a in sim.population.agents}
    return {
        "event": event_id,
        "chain": [event_item(e, names, sim) for e in reversed(chain)],  # root cause first
        "consequences": [event_item(e, names, sim) for e in store.consequences(event_id)],
    }


def _group_of(sim, agent) -> dict | None:
    g = sim.groups.get(agent.group_id) if agent.group_id is not None else None
    if g is None:
        return None
    leader = sim.population.get_by_id(g.leader)
    return {
        "name": g.name,
        "leader": leader.name if leader else None,
        "leaderId": g.leader,
        "members": len(g.members),
        "order": agent.order["kind"] if agent.order else None,
    }
