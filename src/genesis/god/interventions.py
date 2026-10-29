"""God Mode (M9): the creator's interventions. See docs/08 (M9) and docs/12.

Every intervention is an event with origin = "god" (so the history and every cause chain
show it), and is remembered in the world file (meta "god"), so a world loaded again is the
same world. The agents never learn who did it; they only live through it.

    add_food X Y [--regen N]       a new berry patch          remove_food ID
    lake X Y [--size N]            a new pool of water        dry X Y [--size N]   (water -> land)
    disaster KIND [VALLEY] [--days N]   drought, winter, flood, sickness, predators, wildfire
                                         ("weather" = winter, "plague" = sickness), starting now
    migrants N [VALLEY]            newcomers arrive            strike AGENT     dies (cause: god)
    whisper AGENT "TEXT"           a voice from nowhere: a memory they cannot explain

Commands are queued (`god_queue` in the world file): a running world applies them at its next
daily save; otherwise `genesis god` applies them at once.
"""

import json
import random
import time

from genesis.history import events as ev
from genesis.world.disasters import DAYS, KINDS, Disaster
from genesis.world.world import Food, Position, TerrainType

ALIASES = {"weather": "winter", "plague": "sickness", "beasts": "predators", "fire": "wildfire"}
GOD_DISASTER_IDS = 100_000  # god disasters are numbered after the seeded ones


class GodError(ValueError):
    pass


def _state(sim) -> dict:
    g = getattr(sim.world, "god", None)
    if g is None:
        g = sim.world.god = {"terrain": [], "food": [], "removed_food": [], "disasters": []}
    return g


def _emit(sim, act_name: str, actors=(), position=None, **data) -> int:
    return sim.log.emit(
        sim.tick_count,
        ev.INTERVENTION,
        actors=tuple(actors),
        position=position,
        data={"act": act_name, **data},
        origin=ev.GOD,
    )


def _ctx(sim):
    from genesis.simulation.context import TickContext

    return TickContext(
        world=sim.world,
        tick=sim.tick_count,
        log=sim.log,
        rng=sim.rng,
        agents={a.id: a for a in sim.population.agents},
        mind=None,
        pregnancies=sim.pregnancies,
        groups=sim.groups,
    )


def _square(world, x, y, size):
    half = size // 2
    return [
        Position(px, py)
        for px in range(x - half, x - half + size)
        for py in range(y - half, y - half + size)
        if world.is_valid_position(Position(px, py))
    ]


# =================================================================
# The interventions
# =================================================================


def add_food(sim, x: int, y: int, regen: int = 3, quantity: int = 100) -> int:
    w = sim.world
    p = Position(x, y)
    if not w.is_walkable(p):
        raise GodError(f"({x},{y}) is not land anyone can reach")
    if any(f.position == p for f in w.food_sources):
        raise GodError(f"there is already a patch at ({x},{y})")
    fid = max((f.id for f in w.food_sources), default=0) + 1
    w.food_sources.append(Food(fid, p, quantity, 100, regen, 0))
    w.food_region[fid] = w.region_at.get(p, w.region_names[0])
    _state(sim)["food"].append({"id": fid, "x": x, "y": y, "regen": regen})
    return _emit(sim, "add_food", position=p, food=fid, regen=regen)


def remove_food(sim, food_id: int) -> int:
    w = sim.world
    food = next((f for f in w.food_sources if f.id == food_id), None)
    if food is None:
        raise GodError(f"no berry patch {food_id}")
    w.food_sources.remove(food)
    _state(sim)["removed_food"].append(food_id)
    return _emit(sim, "remove_food", position=food.position, food=food_id)


def _set_land(sim, x, y, size, terrain: TerrainType, act: str) -> int:
    w = sim.world
    tiles = _square(w, x, y, size)
    if terrain == TerrainType.WATER:
        busy = {a.position for a in sim.population.get_alive()} | {
            f.position for f in w.food_sources
        }
        if any(t in busy for t in tiles):
            raise GodError("someone or a berry patch is there: choose another place")
    for t in tiles:
        w.terrain[t] = terrain
    w.rebuild_terrain()
    _state(sim)["terrain"].append({"x": x, "y": y, "size": size, "terrain": terrain.value})
    return _emit(sim, act, position=Position(x, y), size=size)


def lake(sim, x: int, y: int, size: int = 3) -> int:
    return _set_land(sim, x, y, size, TerrainType.WATER, "lake")


def dry(sim, x: int, y: int, size: int = 3) -> int:
    return _set_land(sim, x, y, size, TerrainType.PLAINS, "dry")


def disaster(sim, kind: str, region: str = "all", days: int | None = None) -> int:
    from genesis.world import disasters

    kind = ALIASES.get(kind, kind)
    w = sim.world
    if kind not in KINDS:
        raise GodError(f"disasters: {', '.join(KINDS)} (or weather, plague, beasts, fire)")
    if region != "all" and region not in w.region_names:
        raise GodError(f"valleys: all, {', '.join(w.region_names)}")
    days = days or DAYS[kind][0]
    start = w.calendar.day_index(sim.tick_count) + 1  # from the next day
    did = GOD_DISASTER_IDS + len(_state(sim)["disasters"]) + 1
    d = Disaster(did, kind, region, start, days)
    w.disasters = tuple(sorted((*w.disasters, d), key=lambda x: (x.day, x.id)))
    _state(sim)["disasters"].append(d.to_dict())
    event = _emit(sim, "disaster", **d.to_dict())
    _state(sim).setdefault("causes", {})[str(did)] = event
    w.god_causes = {int(k): v for k, v in _state(sim)["causes"].items()}
    _ = disasters  # the engine starts it on its first day, like any other (caused by this)
    return event


def migrants(sim, count: int, region: str | None = None) -> int:
    w = sim.world
    if not 1 <= count <= 20:
        raise GodError("1 to 20 migrants at a time")
    starts = {name: Position(x, y) for name, x, y in w.regions}
    if region is None:
        region = w.region_names[0]
    centre = starts.get(region) or Position(w.width // 2, w.height // 2)
    rng = random.Random(f"god:migrants:{sim.tick_count}:{count}")
    r = w.rules
    tpd = w.calendar.ticks_per_day
    names = []
    for _ in range(count):
        for _try in range(200):
            p = Position(centre.x + rng.randint(-4, 4), centre.y + rng.randint(-4, 4))
            if w.is_walkable(p):
                break
        name = _new_name(sim, rng)
        age = rng.randint(r.founder_min_age_days * tpd, r.founder_max_age_days * tpd)
        agent = sim.population.create_agent(name, p, born_tick=sim.tick_count - age)
        agent.home_region = region
        if w.shore:
            agent.memory.remember_location(
                "water", min(w.shore, key=lambda s: (s.distance(p), s.x, s.y)), seen=False
            )
        names.append(agent.id)
    return _emit(sim, "migrants", actors=names, region=region, count=count)


def _new_name(sim, rng) -> str:
    taken = {a.name for a in sim.population.agents}
    syllables = "ka ri to mi sa lo ne da vi ru el an or ta ze bo li en mo su ha ni ve".split()
    while True:
        name = "".join(rng.choice(syllables) for _ in range(2)).capitalize()
        if name not in taken:
            return name


def strike(sim, agent_id: int) -> int:
    agent = sim.population.get_by_id(agent_id)
    if agent is None or not agent.alive:
        raise GodError(f"no one alive with id {agent_id}")
    event = _emit(sim, "strike", actors=(agent_id,), position=agent.position, name=agent.name)
    agent._die(sim.tick_count, sim.log, "god", event)
    sim._grieve(_ctx(sim), event)
    return event


def whisper(sim, agent_id: int, text: str) -> int:
    agent = sim.population.get_by_id(agent_id)
    if agent is None or not agent.alive:
        raise GodError(f"no one alive with id {agent_id}")
    text = " ".join(text.split())[:200]
    if not text:
        raise GodError("say something")
    event = _emit(sim, "whisper", actors=(agent_id,), position=agent.position, text=text)
    sim.add_memory(
        agent,
        sim.tick_count,
        source_event_id=event,
        kind="observation",
        text=f'A voice from nowhere said: "{text}"',
        importance=9,
        about=(),
        source_agent_id=None,
    )
    agent.memory.remember_event(event)
    return event


COMMANDS = {
    "add_food": add_food,
    "remove_food": remove_food,
    "lake": lake,
    "dry": dry,
    "disaster": disaster,
    "migrants": migrants,
    "strike": strike,
    "whisper": whisper,
}


def apply(sim, command: dict) -> int:
    """Apply one command {"act": ..., args...}. Returns the god event id."""
    original = {k: v for k, v in command.items() if k != "id"}
    command = dict(original)
    act = command.pop("act", None)
    fn = COMMANDS.get(act)
    if fn is None:
        raise GodError(f"God Mode commands: {', '.join(COMMANDS)}")
    tick = sim.tick_count
    try:
        event = fn(sim, **command)
    except TypeError as e:
        raise GodError(f"{act}: {e}") from e
    # Kept with the tick it was made at, so a replay from the start can do the same.
    _state(sim).setdefault("log", []).append({"tick": tick, "command": original})
    return event


def schedule_of(saved: dict) -> dict[int, list[dict]]:
    """{tick: [commands]} from a world's God log, for replays."""
    out: dict[int, list[dict]] = {}
    for entry in saved.get("log", []):
        out.setdefault(entry["tick"], []).append(entry["command"])
    return out


# =================================================================
# Kept with the world file, and the command queue
# =================================================================


def restore(sim, saved: dict):
    """On load: the land, patches and disasters God changed (in the order they were made)."""
    if not saved:
        return
    w = sim.world
    w.god = {k: list(v) for k, v in saved.items()}
    for food_id in saved.get("removed_food", []):
        w.food_sources = [f for f in w.food_sources if f.id != food_id]
    for f in saved.get("food", []):
        if not any(x.id == f["id"] for x in w.food_sources):
            w.food_sources.append(Food(f["id"], Position(f["x"], f["y"]), 0, 100, f["regen"], 0))
    for edit in saved.get("terrain", []):
        for t in _square(w, edit["x"], edit["y"], edit["size"]):
            w.terrain[t] = TerrainType(edit["terrain"])
    if saved.get("terrain") or saved.get("food"):
        w.rebuild_terrain()
    w.god_causes = {int(k): v for k, v in saved.get("causes", {}).items()}
    extra = [Disaster(**d) for d in saved.get("disasters", [])]
    if extra:
        w.disasters = tuple(sorted((*w.disasters, *extra), key=lambda x: (x.day, x.id)))


QUEUE = """CREATE TABLE IF NOT EXISTS god_queue (
    id INTEGER PRIMARY KEY, created TEXT NOT NULL, command_json TEXT NOT NULL,
    applied_tick INTEGER, event_id INTEGER, error TEXT
)"""


def enqueue(conn, command: dict) -> int:
    conn.execute(QUEUE)
    cur = conn.execute(
        "INSERT INTO god_queue (created, command_json) VALUES (?, ?)",
        (time.strftime("%Y-%m-%dT%H:%M:%S"), json.dumps(command)),
    )
    conn.commit()
    return cur.lastrowid


def pending(conn) -> list[tuple[int, dict]]:
    conn.execute(QUEUE)
    return [
        (r[0], json.loads(r[1]))
        for r in conn.execute(
            "SELECT id, command_json FROM god_queue WHERE applied_tick IS NULL ORDER BY id"
        )
    ]


def apply_pending(sim, store) -> list[tuple[int, int | None, str | None]]:
    """Apply queued commands now (a running world calls this at each daily save)."""
    done = []
    for qid, command in pending(store.conn):
        try:
            event, error = apply(sim, command), None
        except GodError as e:
            event, error = None, str(e)
        store.conn.execute(
            "UPDATE god_queue SET applied_tick = ?, event_id = ?, error = ? WHERE id = ?",
            (sim.tick_count, event, error, qid),
        )
        done.append((qid, event, error))
    if done:
        store.conn.commit()
    return done
