"""Invention, making, teaching and forgetting (M8b). See docs/15 (15.2).

    problem      agents remember what keeps going wrong (running dry far from water, ...)
    gather       materials from the land (an hour each)
    experiment   a curious agent tries a recipe: materials + actions. The LLM proposes the
                 idea in persona; without it, the rules try a combination. The hidden physics
                 (materials.py) decides what the result can do. Skill, intelligence and luck
                 decide whether this attempt works.
    invention    a working idea that the inventor didn't know before becomes a *concept*,
                 named with a valley word (LLM, checked) or made of syllables
    make         anyone who knows a concept can make the thing from its materials
    teach        knowledge spreads in conversation; it is lost when its last holder dies

Everything is opt-in (`rules.inventions`); story worlds never get here.
"""

import json
from dataclasses import asdict, dataclass

from genesis.culture import materials as phys
from genesis.culture import tech
from genesis.culture.words import is_valley_word, leak_words, leaks
from genesis.history import events as ev
from genesis.llm.mind import Question
from genesis.world.world import Position

SYLLABLES = "ka ri to mi sa lo ne da vi ru el an or ta ze bo li en ia mo su ha ni ve".split()
SEE_DISTANCE = 3  # tiles: close enough to see what someone carries or makes
ITEM_USES = {
    "holds water": 300,
    "holds food": 300,
    "cuts": 120,
    "reaches": 120,
    "binds": 200,
    "adorns": 1000,
    "shelter": 0,
    # S2.2 (inventions = 2): wear in uses or days (see wear())
    "pierces": 120,
    "digs": 150,
    "grinds": 200,
    "carries loads": 300,
    "floats": 150,
    "keeps warm": 200,
    "makes fire": 100,
    "cooks": 200,
    "lights": 150,
    "stores food": 300,
    "heals": 60,
    "marks": 150,
}
NEED_WORDS = {
    "carry water": "You keep running out of water when you are far from the lake.",
    "carry food": "You keep going hungry when you are far from the berries.",
    "gather food": "Gathering enough berries is slow work.",
    "win fights": "You have been beaten in a fight.",
    "rest": "You often can't rest well; you wake tired.",
    "be admired": "You want others to notice you and look up to you.",
    **tech.NEED_WORDS,
}
MATERIAL = "material"  # S2.2: a concept that is a new kind of stuff, not a tool


def physics(rules):
    """The physics of making things: Stage 1's (materials.py) or S2.2's (tech.py)."""
    return tech if rules.inventions >= 2 else phys


def v2(rules) -> bool:
    return rules.inventions >= 2


def effect_of(function: str, rules=None) -> str:
    if function == MATERIAL:
        return "is a new kind of stuff to make other things from"
    table = physics(rules).FUNCTIONS if rules is not None else tech.FUNCTIONS
    return table.get(function, tech.FUNCTIONS.get(function, {})).get("effect", function)


def made_props(concepts) -> dict:
    """Properties of the made materials, by their key in recipes and packs ("#<id>")."""
    return {tech.made_key(c.id): c.props for c in concepts.values() if getattr(c, "props", None)}


def restore_props(concepts):
    """After loading: a made material's properties follow from its recipe (and the made
    materials in it, older ones first), exactly as when it was invented."""
    for c in sorted(concepts.values(), key=lambda c: c.id):
        if c.function == MATERIAL:
            made = {k: v for k, v in made_props(concepts).items() if int(k[1:]) < c.id}
            c.props = tech.properties(phys.Recipe.of(*c.recipe), made)[0]


def material_name(key: str, concepts) -> str:
    """What people call a material: its own word, or the concept's name for a made one."""
    if tech.is_made(key):
        c = concepts.get(int(key[1:]))
        return c.name if c is not None else key
    return key


def is_material(good: str, rules) -> bool:
    return good in physics(rules).MATERIALS or tech.is_made(good)


def trouble(agent, rules, problem: str, amount: float):
    """S2.2: life adds to a trouble (only in worlds with the new technology)."""
    if v2(rules) and amount > 0:
        agent.problems[problem] = round(agent.problems.get(problem, 0.0) + amount, 4)


@dataclass
class Concept:
    id: int
    name: str
    function: str  # the function it was invented for
    functions: list  # everything it can do (the physics decides)
    recipe: list  # [materials, actions]
    inventor: int
    tick: int
    description: str = ""
    same_as: int | None = None  # an older concept that does the same (parallel invention)
    lost_tick: int | None = None
    props: dict | None = None  # S2.2: a made material's properties (function "material")
    parent: int | None = None  # S2.2: the older idea this one improves on or grew out of

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Concept":
        return cls(**d)


def _emit(ctx, type, actors=(), targets=(), **kwargs) -> int:
    event_id = ctx.log.emit(ctx.tick, type, actors=actors, targets=targets, **kwargs)
    for agent_id in (*actors, *targets):
        agent = ctx.agents.get(agent_id)
        if agent is not None:
            agent.memory.remember_event(event_id)
    return event_id


# =================================================================
# Problems: what keeps going wrong (rules, every tick)
# =================================================================


def notice_problems(ctx, agent):
    r = ctx.world.rules
    p = agent.problems
    for resource, need, problem in (
        ("water", "thirst", "carry water"),
        ("food", "hunger", "carry food"),
    ):
        if getattr(agent, need) < r.seek_threshold or agent.inventory.get(resource, 0) > 0:
            continue
        known = agent.memory.get_known_locations(resource)
        if not known or min(agent.position.distance(k.position) for k in known) > 4:
            p[problem] = p.get(problem, 0.0) + 1.0
    if agent.hunger >= r.seek_threshold:
        p["gather food"] = p.get("gather food", 0.0) + 0.3
    if agent.energy <= r.sleep_at_energy:
        p["rest"] = p.get("rest", 0.0) + 0.5
    if r.goals and agent.goal is not None and agent.goal["need"] == "be respected":
        p["be admired"] = p.get("be admired", 0.0) + 0.3  # M8f: something to be admired for
    if v2(r):
        _notice_v2(ctx, agent)
    _notice_solutions(ctx, agent)


def _notice_v2(ctx, agent):
    """S2.2 troubles from the hours lived: cold mornings, dark nights."""
    r, cal, bonus = ctx.world.rules, ctx.world.calendar, agent.bonus
    hour = cal.hour(ctx.tick)
    if cal.seasons and cal.season_index(ctx.tick) == 3 and 6 <= hour < 12:  # a cold dawn
        if not bonus.get("warm") and not sheltered(agent, ctx.world.structures):
            trouble(agent, r, "cold", 0.3)
            if not bonus.get("fire"):
                trouble(agent, r, "no fire", 0.2)
    if (hour >= 18 or hour < 6) and not bonus.get("lights") and not bonus.get("fire"):
        trouble(agent, r, "dark", 0.05)  # awake in the dark (a fire gives light too)
        trouble(agent, r, "no fire", 0.05)


def _notice_solutions(ctx, agent):
    """Seeing a neighbour with a thing that solves their own trouble: they would rather learn
    it than tinker from scratch (one idea spreads instead of being invented ten times)."""
    problem = main_problem(agent, ctx.world.rules)
    if problem is None:
        return
    target = tech.SOLVES[problem]
    concepts = ctx.world.concepts
    if known_for(agent, concepts, target):
        return
    for other in ctx.agents.values():
        if (
            other is agent
            or not other.alive
            or agent.position.distance(other.position) > SEE_DISTANCE
        ):
            continue
        if known_for(other, concepts, target):
            agent.asked[f"seen:{target}"] = ctx.tick
            return


def lost_a_fight(agent):
    agent.problems["win fights"] = agent.problems.get("win fights", 0.0) + 3.0


def after_meal(ctx, agent):
    """S2.2: the tools used for a meal wear; then the meal is judged."""
    b = agent.bonus
    for key, function in (("cuts", "cuts"), ("grinds", "grinds")):
        if b.get(key):
            wear(ctx, agent, function)
    if b.get("cooks") and b.get("fire"):
        wear(ctx, agent, "cooks")
        wear(ctx, agent, "makes fire")
    ate(ctx, agent)


def ate(ctx, agent):
    """S2.2: after a meal. Meals that don't fill make people think of better food."""
    r = ctx.world.rules
    if v2(r) and agent.hunger >= r.seek_threshold:
        trouble(agent, r, "meals don't fill", 0.5)
        if agent.bonus.get("fire"):
            trouble(agent, r, "raw food", 0.5)


def fade_problems(agent):
    """Once a day: old troubles fade unless they keep happening."""
    for key in list(agent.problems):
        agent.problems[key] = round(agent.problems[key] * 0.97, 4)
        if agent.problems[key] < 0.05:
            del agent.problems[key]


def main_problem(agent, rules) -> str | None:
    """The problem that bothers them most, if it bothers them enough."""
    best = None
    for problem, score in sorted(agent.problems.items()):
        if score >= rules.problem_threshold and (best is None or score > best[1]):
            best = (problem, score)
    return best[0] if best else None


# =================================================================
# What the agent knows and has
# =================================================================


def known_for(agent, concepts, function) -> list:
    """What they know how to make that serves a use (not stuff to make things from, S2.2)."""
    return [
        concepts[c]
        for c in sorted(agent.knowledge)
        if c in concepts and function in concepts[c].functions and concepts[c].function != MATERIAL
    ]


def has_item_for(agent, concepts, function) -> bool:
    return any(
        function in concepts[i["concept"]].functions
        for i in agent.items
        if i["concept"] in concepts
    )


def item_bonus(agent, concepts, function) -> int:
    """How many carried things serve a function (at most two count)."""
    n = sum(
        1
        for i in agent.items
        if i["concept"] in concepts and function in concepts[i["concept"]].functions
    )
    return min(n, 2)


def use_item(agent, concepts, function):
    """Wear down one item that serves the function; returns the item if it broke."""
    for i in agent.items:
        if i["concept"] in concepts and function in concepts[i["concept"]].functions:
            i["uses"] -= 1
            if i["uses"] <= 0:
                agent.items.remove(i)
                refresh_bonus(agent, concepts)
                return i
            return None
    return None


def wear(ctx, agent, function):
    """S2.2: a thing is used, so it wears; when it breaks, the trouble it answered comes back
    (so they make another)."""
    if not v2(ctx.world.rules):
        return
    concepts = ctx.world.concepts
    broke = use_item(agent, concepts, function)
    if broke is None:
        return
    c = concepts[broke["concept"]]
    problem = next((p for p, f in tech.SOLVES.items() if f == function), None)
    if problem is not None:
        trouble(agent, ctx.world.rules, problem, ctx.world.rules.problem_threshold)
    _emit(
        ctx,
        ev.ITEM_BROKE,
        actors=(agent.id,),
        position=agent.position,
        data={"concept": c.id, "name": c.name, "function": function},
    )


PROBLEM_OF = {use: problem for problem, use in tech.SOLVES.items()}  # S2.2

# S2.2 uses that work while carried (no other moment of use): one use a day.
WORN_DAILY = ("holds water", "holds food", "carries loads", "binds", "adorns", "lights")


def daily_wear(ctx, agent):
    """Once a day (S2.2): carried things wear; food spoils unless stored well; and what they
    have makes them want what it leads to (the curious more)."""
    r = ctx.world.rules
    if not v2(r) or not agent.alive:
        return
    have = tools_of(agent, ctx.world.concepts)
    push = 0.08 + 0.1 * agent.genome.curiosity
    wants = sorted({nxt for use in have for nxt in tech.LEADS_TO.get(use, ()) if nxt not in have})
    for use in wants:
        trouble(agent, r, PROBLEM_OF[use], push)
    for function in WORN_DAILY:
        if has_item_for(agent, ctx.world.concepts, function):
            wear(ctx, agent, function)
    cold = ctx.world.calendar.season_index(ctx.tick) == 3
    if cold and agent.bonus.get("warm"):
        wear(ctx, agent, "keeps warm")
    food = agent.inventory.get("food", 0)
    if r.food_spoils_per_day > 0 and food > r.food_per_meal:
        if agent.bonus.get("stores"):
            wear(ctx, agent, "stores food")
        else:
            lost = int(food * r.food_spoils_per_day)
            if lost:
                agent.inventory["food"] = food - lost
                trouble(agent, r, "food spoils", 0.12)


def refresh_bonus(agent, concepts):
    """What their things add (kept on the agent so capacity checks stay cheap)."""
    agent.bonus = {
        "water": 2 * item_bonus(agent, concepts, "holds water"),
        "food": 10 * item_bonus(agent, concepts, "holds food"),
        "cuts": item_bonus(agent, concepts, "cuts"),
        "reaches": item_bonus(agent, concepts, "reaches"),
    }
    if any("quality" in i for i in agent.items):  # S2.2 things (only inventions = 2 makes them)
        for key, function in BONUS_V2.items():
            n = item_bonus(agent, concepts, function)
            if n:
                agent.bonus[key] = n
        room = 4 * min(1, agent.bonus.get("loads", 0)) + 2 * min(1, agent.bonus.get("binds", 0))
        if room:
            agent.bonus["materials"] = room


BONUS_V2 = {  # bonus key -> use (S2.2)
    "pierces": "pierces",
    "digs": "digs",
    "grinds": "grinds",
    "loads": "carries loads",
    "binds": "binds",
    "floats": "floats",
    "warm": "keeps warm",
    "fire": "makes fire",
    "cooks": "cooks",
    "lights": "lights",
    "stores": "stores food",
    "heals": "heals",
    "marks": "marks",
}


def materials_room(agent, rules) -> int:
    """How many materials they can carry (S2.2: bags and cords carry more)."""
    return rules.materials_capacity + agent.bonus.get("materials", 0)


def tools_of(agent, concepts) -> set[str]:
    """The uses they have in hand, for working things (fire, a cutting edge)."""
    return {
        f for i in agent.items if i["concept"] in concepts for f in concepts[i["concept"]].functions
    }


def add_item(agent, item, concepts, rules, trim: bool = True):
    """S2.2: take a thing; with `items_capacity`, a full kit leaves its least useful thing
    behind (the one whose uses the others cover most), and they think of something that
    does two jobs at once. trim=False: not yet (in the middle of a trade)."""
    agent.items.append(item)
    if trim:
        trim_items(agent, concepts, rules, keep=item)
    else:
        refresh_bonus(agent, concepts)


def trim_items(agent, concepts, rules, keep=None):
    """A kit over `items_capacity` leaves its least useful things behind."""
    cap = rules.items_capacity if v2(rules) else 0
    while cap and len(agent.items) > cap:

        def own(i):
            others = {
                f
                for j in agent.items
                if j is not i and j["concept"] in concepts
                for f in concepts[j["concept"]].functions
            }
            mine = set(concepts[i["concept"]].functions) if i["concept"] in concepts else set()
            return (len(mine - others), i.get("quality", 0), i["uses"])

        dropped = min((i for i in agent.items if i is not keep), key=own)
        agent.items.remove(dropped)
        if dropped["concept"] in concepts:
            agent.asked["combine"] = concepts[dropped["concept"]].function
    refresh_bonus(agent, concepts)


def new_item(ctx, agent, concept) -> dict:
    """A made thing. S2.2: its quality (skill and how well the materials fit) sets how long it
    lasts."""
    uses = ITEM_USES.get(concept.function, 100)
    if not v2(ctx.world.rules):
        return {"concept": concept.id, "uses": uses}
    recipe = phys.Recipe.of(*concept.recipe)
    fitness = tech.fit(recipe, concept.function, made_props(ctx.world.concepts))
    quality = round(0.4 + 0.4 * agent.abilities.get("crafting") + 0.2 * fitness, 3)
    return {
        "concept": concept.id,
        "uses": max(1, round(uses * (0.5 + quality))),
        "quality": quality,
    }


def material_kinds(agent) -> list[str]:
    return sorted(m for m, n in agent.materials.items() if n > 0)


def carrying_materials(agent) -> int:
    return sum(agent.materials.values())


# =================================================================
# Gathering
# =================================================================


def terrain_of(world, position) -> str:
    if world.shore_lakes.get(position):
        return "shore"
    return world.terrain[position].value


def _material_grids(world) -> dict:
    """material -> bool array [x, y] of walkable tiles that give it (M8.5 speed). Rebuilt when
    a wildfire changes the land."""
    import numpy as np

    cached = getattr(world, "_material_grids", None)
    if cached is not None and cached[0] == (world.burned, world.land_version):
        return cached[1]
    P = physics(world.rules)
    grids = {m: np.zeros((world.width, world.height), dtype=bool) for m in P.MATERIALS}
    for p in world.walkable:
        for m, _ in P.SOURCES.get(terrain_of(world, p), []):
            grids[m][p.x, p.y] = True
    world._material_grids = ((world.burned, world.land_version), grids)
    return grids


def gather_spot(agent, world, wanted: str | None):
    """The nearest seen tile where a wanted material (or any material not in hand) is found
    (ties: smallest x, then y)."""
    import numpy as np

    grids = _material_grids(world)
    if wanted is not None:
        kinds = [wanted] if wanted in grids else []
    else:
        in_hand = set(material_kinds(agent))
        kinds = [m for m in grids if m not in in_hand]
    if not kinds:
        return None
    useful = np.zeros((world.width, world.height), dtype=bool)
    for m in kinds:
        useful |= grids[m]
    useful &= agent.memory.seen_grid(world.width, world.height)
    xs, ys = np.nonzero(useful)  # in (x, y) order
    if not len(xs):
        return None
    i = int(np.argmin(np.abs(xs - agent.position.x) + np.abs(ys - agent.position.y)))
    return Position(int(xs[i]), int(ys[i]))


def useful_material(agent, world, target: str):
    """S2.2: (material, where) worth fetching to try for a use: the raw materials with most
    of what it needs (and something to bind with, if it must be bound) that they don't hold
    yet and have seen where to find. None if there is no such place."""
    spec = tech.FUNCTIONS[target]
    needs = spec["needs"]
    in_hand = set(material_kinds(agent))
    ranked = sorted(tech.MATERIALS, key=lambda m: (-sum(tech.MATERIALS[m][p] for p in needs), m))
    wanted = [*ranked[:2], "fibre", "resin", *ranked[2:4]]  # and something to tie with
    if spec.get("bound") or "long" in needs:
        wanted = ["fibre", "resin", *ranked[:4]]
    from genesis.mind import planner

    for m in wanted:
        if m not in in_hand:
            spot = gather_spot(agent, world, m)
            if spot is not None and _safe_trip(agent, world, spot, planner):
                return m, spot
    return None


TRIP = 20  # S2.2: the farthest one goes for a material (and never beyond the leash)


def _safe_trip(agent, world, spot, planner) -> bool:
    return agent.position.distance(spot) <= TRIP and planner.within_leash(agent, world, spot)


def improvement(agent, world, tick):
    """S2.2: a curious person with nothing pressing sometimes works on something better: one
    thing for two jobs when their kit is full, or a better version of a thing they have.
    Returns the trouble to tinker for, or None. Decided without chance (a hash of the person
    and the day), about one day in three for the most curious."""
    import zlib

    r = world.rules
    if not v2(r) or agent.stage == "child" or not agent.items:
        return None
    day = world.calendar.day_index(tick)
    if zlib.crc32(f"improve:{agent.id}:{day}".encode()) % 1000 >= 350 * agent.genome.curiosity:
        return None
    combine = agent.asked.get("combine")
    if combine in PROBLEM_OF:
        return PROBLEM_OF[combine]
    made = made_props(world.concepts)
    worst = None
    for i in agent.items:
        c = world.concepts.get(i["concept"])
        if c is None or c.function not in PROBLEM_OF:
            continue
        f = tech.fit(phys.Recipe.of(*c.recipe), c.function, made)
        if f < 0.9 and (worst is None or f < worst[0]):
            worst = (f, c.function)
    return PROBLEM_OF[worst[1]] if worst else None


def useful_stuff(agent, concepts, target: str):
    """S2.2: a made material they know that suits a use better than any raw material (cord
    for a bag, fired clay for a pot), with the next step towards having it in hand. None if
    there is none, or it is already in hand."""
    needs = tech.FUNCTIONS[target]["needs"]
    best_raw = max(sum(m[p] for p in needs) for m in tech.MATERIALS.values())
    stuff = [
        c
        for c in (concepts[i] for i in sorted(agent.knowledge) if i in concepts)
        if c.function == MATERIAL and c.props and c.lost_tick is None
    ]
    stuff.sort(key=lambda c: (-sum(c.props[p] for p in needs), c.id))
    for c in stuff:
        if sum(c.props[p] for p in needs) < 0.8 * best_raw:
            break
        if agent.materials.get(tech.made_key(c.id), 0) > 0:
            return None  # in hand already
        step = next_step(agent, c, concepts)
        if step is not None:
            return step
    return None


def gather(ctx, agent) -> str | None:
    """An hour of gathering here: one unit of something this place gives (by chance)."""
    r = ctx.world.rules
    wanted = agent.project.get("material") if agent.project else None
    if carrying_materials(agent) >= materials_room(agent, r):
        trouble(agent, r, "heavy loads", 0.5)
        if not (v2(r) and wanted and _drop_one(agent, wanted)):
            return None
    terrain = terrain_of(ctx.world, agent.position)
    sources = physics(r).SOURCES.get(terrain, [])
    ordered = sorted(sources, key=lambda s: (s[0] != wanted, s[0]))
    if v2(r) and wanted and any(m == wanted for m, _ in sources):
        ordered = [s for s in ordered if s[0] == wanted]  # S2.2: an hour spent looking for it
    for material, chance in ordered:
        if ctx.rng.random() < chance:
            return _gathered(ctx, agent, material)
    if v2(r):  # S2.2: a digging tool or something that floats gives a second try
        tool = "digs" if agent.bonus.get("digs") else None
        if terrain == "shore" and agent.bonus.get("floats"):
            tool = "floats"
        if tool is not None:
            reach = tech.GROUND if tool == "digs" else tech.WATERSIDE
            for material, chance in ordered:
                if material in reach and ctx.rng.random() < chance:
                    wear(ctx, agent, tool)
                    return _gathered(ctx, agent, material)
        if terrain == "mountain":
            trouble(agent, r, "hard ground", 0.3)
        elif terrain == "shore":
            trouble(agent, r, "reeds out of reach", 0.3)
    return None


def _drop_one(agent, keep: str) -> bool:
    """S2.2: a full pack and a recipe waiting: leave one of the most plentiful other things."""
    others = sorted((-n, m) for m, n in agent.materials.items() if m != keep and n > 0)
    if not others:
        return False
    m = others[0][1]
    agent.materials[m] -= 1
    if agent.materials[m] <= 0:
        del agent.materials[m]
    return True


def _gathered(ctx, agent, material: str) -> str:
    agent.materials[material] = agent.materials.get(material, 0) + 1
    if material == "wood" and ctx.world.land is not None:  # S2.1: trees come down
        ctx.world.land.cut_wood(agent.position)
    return material


# =================================================================
# Experimenting and inventing
# =================================================================


def experiment(ctx, agent, problem: str):
    """Try a recipe for a problem. Returns the event id (invented, or a failed try)."""
    r = ctx.world.rules
    P = physics(r)
    concepts = ctx.world.concepts
    target = P.SOLVES[problem]
    in_hand = material_kinds(agent)
    made = made_props(concepts) if v2(r) else None
    if v2(r):  # S2.2: only the ways of working their tools allow (fire, a cutting edge)
        tools = tools_of(agent, concepts)
        actions = sorted(
            a for a, spec in tech.ACTIONS.items() if spec.get("tool", "") in {"", *tools}
        )
    else:
        actions = sorted(phys.ACTIONS)
    idea = _llm_idea(ctx, agent, problem, in_hand, actions) if ctx.mind is not None else None
    if idea is not None:
        mats, acts, description = idea
    elif v2(r):
        mats, acts = _directed(ctx, agent, target, in_hand, actions, made)
        description = ""
    else:
        k = min(len(in_hand), ctx.rng.choice((1, 2, 2, 3)))
        mats = ctx.rng.sample(in_hand, k) if k else []
        acts = [ctx.rng.choice(actions) for _ in range(ctx.rng.choice((1, 2)))]
        description = ""
    recipe = phys.Recipe.of(mats, acts)
    works = tech.functions(recipe, made) if v2(r) else phys.functions(recipe)
    skill = agent.abilities.get("crafting")
    chance = r.invent_base_chance + 0.4 * skill + 0.2 * agent.genome.intelligence
    chance += 0.15 * bool(r.talents and agent.talent == "tinkerer")  # M8d
    succeeded = target in works and ctx.rng.random() < chance
    stuff = (  # S2.2: not what they wanted, but a new kind of stuff (cord, fired clay, ...)
        v2(r)
        and not succeeded
        and tech.novel(recipe, made)
        and not any(  # nobody made this stuff yet (else it is learned, not found again)
            c.recipe == recipe.to_list() and c.function == MATERIAL for c in concepts.values()
        )
        and ctx.rng.random() < r.material_find_chance * chance
    )

    for m in recipe.materials:  # trying uses up one of each material
        agent.materials[m] = agent.materials.get(m, 0) - 1
        if agent.materials[m] <= 0:
            del agent.materials[m]
    if v2(r):
        for tool in tech.tools_needed(recipe):
            wear(ctx, agent, tool)
    agent._practice("crafting", 1.0 if succeeded or stuff else 0.3, ctx.world, ctx.tick, ctx.log)
    agent.asked["tinkered"] = ctx.tick

    if not succeeded and not stuff:
        if len(recipe.materials) >= 2 and not tech.properties(recipe, made)[1]:
            trouble(agent, r, "things fall apart", 0.5)
        return _emit(
            ctx,
            ev.EXPERIMENTED,
            actors=(agent.id,),
            position=agent.position,
            data={
                "problem": problem,
                "recipe": recipe.to_list(),
                "works": works,
                "idea": description,
                "success": False,
            },
        )

    function = target if succeeded else MATERIAL
    if v2(r) and succeeded:  # S2.3: the same thing again is recognised, not named anew
        older, _ = _kin(concepts, target, works, recipe)
        if older is not None:
            return _recognised(ctx, agent, older, problem)
    concept = _new_concept(ctx, agent, function, works, recipe, description)
    if function == MATERIAL:
        concept.props = tech.properties(recipe, made)[0]
    agent.knowledge.add(concept.id)
    if function == MATERIAL:
        key = tech.made_key(concept.id)
        agent.materials[key] = agent.materials.get(key, 0) + 1
    elif target != "shelter":
        add_item(agent, new_item(ctx, agent, concept), concepts, r)
    if succeeded:
        agent.problems[problem] = 0.0
        agent.asked.pop("combine", None)
    event_id = _emit(
        ctx,
        ev.INVENTED,
        actors=(agent.id,),
        position=agent.position,
        data={
            "concept": concept.id,
            "name": concept.name,
            "function": function,
            "functions": works,
            "recipe": recipe.to_list(),
            "idea": description,
            "same_as": concept.same_as,
            "leaks": leaks(description),
            # S2.2/S2.3: the idea it grew out of, the facts that led to it
            **({"parent": concept.parent, "lore": _lore_used(agent, recipe, r)} if v2(r) else {}),
        },
    )
    if function == "shelter":
        build(ctx, agent, concept, event_id)
    if r.culture and concept.same_as is None and function != MATERIAL:  # S2.4: a tale
        from genesis.culture import ideas

        ideas.on_first_making(ctx, agent, concept, event_id)
    return event_id


def _recognised(ctx, agent, older, problem):
    """They worked out a thing the valley already has: they know it now (learned by doing),
    and keep the one they made. No new name."""
    concepts = ctx.world.concepts
    new = older.id not in agent.knowledge
    agent.knowledge.add(older.id)
    if older.function != "shelter":
        add_item(agent, new_item(ctx, agent, older), concepts, ctx.world.rules)
    agent.problems[problem] = 0.0
    agent.asked.pop("combine", None)
    if older.lost_tick is not None:  # worked out again after it was lost
        older.lost_tick = None
    return _emit(
        ctx,
        ev.LEARNED if new else ev.MADE,
        actors=(agent.id,),
        position=agent.position,
        data={"concept": older.id, "name": older.name, "function": older.function}
        | ({"learned": True, "how": "worked out"} if new else {}),
    )


def _lore_used(agent, recipe, rules) -> list:
    if not rules.research:
        return []
    from genesis.culture import research

    return research.used(agent, recipe)


def _directed(ctx, agent, target, in_hand, actions, made):
    """S2.2, without the LLM: a try aimed at the use they want. Materials and ways of working
    that give the properties it needs are preferred, with chance in the choice (people try
    sensible things, not every thing)."""
    rng = ctx.rng
    spec = tech.FUNCTIONS[target]
    needs = dict(spec["needs"])
    combine = agent.asked.get("combine") if agent is not None else None
    if combine in tech.FUNCTIONS and combine != target:  # a full kit: one thing for two jobs
        for p, v in tech.FUNCTIONS[combine]["needs"].items():
            needs[p] = max(needs.get(p, 0.0), v)

    def props(m):
        return tech.MATERIALS.get(m) or (made or {}).get(m) or {}

    k = min(len(in_hand), rng.choice((1, 2, 2, 3)))
    noise = {m: rng.random() for m in in_hand}

    def appeal(m):  # what it gives the use, and new stuff is worth a try
        return sum(props(m).get(p, 0) for p in needs) + noise[m] + 0.3 * tech.is_made(m)

    mats = sorted(in_hand, key=lambda m: (-appeal(m), m))
    mats = mats[:k]
    chosen = []
    for _ in range(rng.choice((1, 2, 2, 3))):
        jitter = {a: rng.random() for a in actions}

        def score(a, jitter=jitter):
            act = tech.ACTIONS[a]
            gain = sum(act["changes"].get(p, 0.0) for p in needs)
            if agent is not None and agent.lore:  # S2.3: what they found out guides the try
                from genesis.culture import research

                gain += 2 * sum(research.guided(agent, m, a, needs) for m in mats)
            if act.get("binds") and len(mats) >= 2:
                gain += 0.5  # several things in hand: tie them into one
            return gain + 0.6 * jitter[a]

        chosen.append(max(actions, key=lambda a: (score(a), a)))
    # parts are tied together first, then worked
    return mats, sorted(chosen, key=lambda a: not tech.ACTIONS[a].get("binds"))


def _new_concept(ctx, agent, target, works, recipe, description) -> "Concept":
    concepts = ctx.world.concepts
    parent = None
    if v2(ctx.world.rules) and target != MATERIAL:
        older, parent = _kin(concepts, target, works, recipe)
    else:
        older = next(
            (
                c
                for c in sorted(concepts.values(), key=lambda c: c.id)
                if c.function == target
                and (target != MATERIAL or c.recipe == recipe.to_list())  # S2.2: same stuff
            ),
            None,
        )
    taken = {c.name.lower() for c in concepts.values()}
    taken |= {a.name.lower() for a in ctx.agents.values()}
    if v2(ctx.world.rules):  # S2.2: made materials sit among raw ones; never the same word
        taken |= set(tech.MATERIALS)
    taken |= {g.name.lower() for g in ctx.groups.values()}
    name = _llm_name(ctx, agent, target, recipe, description, taken) if ctx.mind else None
    if name is not None and v2(ctx.world.rules) and not _fresh_stem(name, concepts):
        name = None  # S2.3: no stem more than twice; the rules name it instead
    if name is None:
        sounds = _valley_sounds(ctx, agent) if v2(ctx.world.rules) else SYLLABLES
        tries = 0
        while True:
            name = "".join(ctx.rng.choice(sounds) for _ in range(ctx.rng.choice((2, 3))))
            tries += 1
            fresh = not v2(ctx.world.rules) or tries > 50 or _fresh_stem(name, concepts)
            if name not in taken and is_valley_word(name, taken) and fresh:
                break
    cid = max(concepts, default=0) + 1
    concept = Concept(
        id=cid,
        name=name.capitalize(),
        function=target,
        functions=works,
        recipe=recipe.to_list(),
        inventor=agent.id,
        tick=ctx.tick,
        description=description,
        same_as=older.id if older else None,
        parent=parent,
    )
    concepts[cid] = concept
    return concept


def _fresh_stem(name: str, concepts) -> bool:
    """S2.3: a word's first three letters are used by at most one other idea's name."""
    stem = name.lower()[:3]
    return sum(1 for c in concepts.values() if c.name.lower()[:3] == stem) < 2


def _valley_sounds(ctx, agent) -> list[str]:
    """S2.3: each valley names things with its own sounds (14 of the syllables, chosen from
    the world's seed and the valley's name, without using the world's chance)."""
    import random

    valley = ctx.world.region_at.get(agent.position, "")
    seed = zlib_crc(f"{ctx.world.seed}:{valley}")
    return sorted(random.Random(seed).sample(SYLLABLES, 14))


def zlib_crc(text: str) -> int:
    import zlib

    return zlib.crc32(text.encode())


BETTER = 0.15  # S2.2: this much better a fit for its use is a new idea (an improvement)


def _kin(concepts, target, works, recipe):
    """S2.2: (the older idea this is the same as, or None; the idea it grew out of, or None).
    The same thing again: an older idea that does everything this does, about as well. A new
    idea otherwise: it does a new mix of uses, or does its use clearly better. Its parent is
    the older idea for the same use that shares most uses (the best fit first)."""
    made = made_props(concepts)
    fit = tech.fit(recipe, target, made)
    kin = [
        c
        for c in sorted(concepts.values(), key=lambda c: c.id)
        if c.function != MATERIAL and target in c.functions
    ]
    for c in kin:
        old_fit = tech.fit(phys.Recipe.of(*c.recipe), target, made)
        if set(works) <= set(c.functions) and fit < old_fit + BETTER:
            return c, None
    if not kin:
        return None, None
    best = max(
        kin,
        key=lambda c: (
            len(set(works) & set(c.functions)),
            tech.fit(phys.Recipe.of(*c.recipe), target, made),
            -c.id,
        ),
    )
    return None, best.id


def _lore_lines(ctx, agent, in_hand) -> list[str]:
    """S2.3: what they have found out about what they hold, for the prompt."""
    if not ctx.world.rules.research or not agent.lore:
        return []
    from genesis.culture import research

    lines = research.told_lines(agent, set(in_hand), ctx.world.concepts)
    return ["What you have found out: " + "; ".join(lines) + "."] if lines else []


def _llm_idea(ctx, agent, problem, in_hand, actions):
    """The agent's own idea in persona: materials from what they hold, actions they can do.
    Made materials are shown by their own names (S2.2)."""
    from genesis.llm import persona

    mind = ctx.mind
    if mind is None or not getattr(mind, "active", False) or not in_hand:
        return None
    key_of = {material_name(k, ctx.world.concepts): k for k in in_hand}
    names = list(key_of)

    def prompt():
        return "\n".join(
            [
                persona.fact_block(agent, ctx),
                "",
                NEED_WORDS[problem],
                *_lore_lines(ctx, agent, in_hand),
                f"You have: {', '.join(names)}. You can work things in these ways: "
                f"{', '.join(actions)}.",
                "Think of something you could make from what you have that might help. Nothing "
                "like it exists anywhere yet, so it has no name: describe it only by what it is "
                "made of, how you work it, and what it does. Never call it by the name of any "
                "thing you know of.",
                'Answer in JSON: "thought", "idea" (under 25 words), "materials" (1 to 3 of what '
                'you have), "actions" (1 to 3 ways of working them, in order).',
            ]
        )

    schema = {
        "type": "object",
        "properties": {
            "thought": {"type": "string"},
            "idea": {"type": "string"},
            "materials": {"type": "array", "items": {"type": "string", "enum": names}},
            "actions": {"type": "array", "items": {"type": "string", "enum": actions}},
        },
        "required": ["thought", "idea", "materials", "actions"],
    }

    def parse(text, record):
        try:
            data = json.loads(text)
            mats = [key_of[m] for m in data.get("materials", []) if m in key_of][:3]
            acts = [a for a in data.get("actions", []) if a in actions][:3]
            idea = " ".join(str(data.get("idea", "")).split())[:200]
        except (ValueError, TypeError, AttributeError):
            mats, acts, idea = [], [], ""
        if not mats or not acts:
            if record is not None:
                record["outcome"], record["error"] = "invalid", "no usable materials or actions"
            return None
        leaked = leak_words(idea)
        if leaked:  # M8f: the recipe is theirs; a name from our world is not
            idea = ""
            if record is not None:
                record["error"] = "leak dropped: " + ", ".join(sorted(set(leaked)))
        if record is not None:
            record.update(
                choice=f"{'+'.join(mats)} / {'+'.join(acts)}",
                say=idea,
                thought=str(data.get("thought", ""))[:160],
            )
        return list(dict.fromkeys(mats)), acts, idea

    question = Question(
        kind="invent",
        agent=agent,
        other=None,
        prompt=prompt,
        schema=schema,
        parse=parse,
        max_tokens=150,
        budgeted=False,
    )
    return mind.ask(ctx, ctx.next_point(), question)


def _llm_name(ctx, agent, function, recipe, description, taken):
    from genesis.llm import persona

    mind = ctx.mind
    if mind is None or not getattr(mind, "active", False):
        return None

    def prompt():
        return "\n".join(
            [
                persona.fact_block(agent, ctx),
                "",
                f"You made something new that works! It {effect_of(function, ctx.world.rules)}. "
                f"You made it from "
                f"{', '.join(material_name(m, ctx.world.concepts) for m in recipe.materials)}"
                + (f" ({description})" if description else "")
                + ".",
                "Give it a name: one new word of your own (3 to 12 letters), not a word anyone "
                "here already uses.",
                'Answer in JSON: "thought", "name".',
            ]
        )

    def parse(text, record):
        try:
            name = str(json.loads(text).get("name", "")).strip()
        except (ValueError, TypeError, AttributeError):
            name = ""
        if not is_valley_word(name, taken):
            if record is not None:
                record["outcome"] = "invalid"
                record["error"] = f"{name!r} is not a new word of the valley"
            return None
        if record is not None:
            record.update(choice=name)
        return name

    question = Question(
        kind="name_invention",
        agent=agent,
        other=None,
        prompt=prompt,
        schema={
            "type": "object",
            "properties": {"thought": {"type": "string"}, "name": {"type": "string"}},
            "required": ["thought", "name"],
        },
        parse=parse,
        max_tokens=60,
        budgeted=False,
    )
    return mind.ask(ctx, ctx.next_point(), question)


# =================================================================
# Making, building, teaching, losing
# =================================================================


def can_make(agent, concept, concepts=None) -> bool:
    mats = concept.recipe[0]
    if not all(agent.materials.get(m, 0) >= 1 for m in mats):
        return False
    need = tech.tools_needed(phys.Recipe.of(*concept.recipe)) if concepts is not None else []
    return not need or set(need) <= tools_of(agent, concepts)


def missing_material(agent, concept) -> str | None:
    for m in concept.recipe[0]:
        if agent.materials.get(m, 0) < 1:
            return m
    return None


def next_step(agent, concept, concepts, depth: int = 0):
    """S2.2: what to do next to make a concept: ("make", concept) once everything is in hand,
    ("gather", raw material) for a missing raw one, or the step towards a missing made
    material they know how to make. None if they can't (a missing tool or unknown stuff)."""
    if depth > 4:
        return None
    need = tech.tools_needed(phys.Recipe.of(*concept.recipe))
    if need and not set(need) <= tools_of(agent, concepts):
        return None
    missing = missing_material(agent, concept)
    if missing is None:
        return ("make", concept)
    if not tech.is_made(missing):
        return ("gather", missing)
    sub = concepts.get(int(missing[1:]))
    if sub is None or sub.id not in agent.knowledge or sub.lost_tick is not None:
        return None
    return next_step(agent, sub, concepts, depth + 1)


def make(ctx, agent, concept):
    concepts = ctx.world.concepts
    if not can_make(agent, concept, concepts if v2(ctx.world.rules) else None):
        return None
    for m in concept.recipe[0]:
        agent.materials[m] -= 1
        if agent.materials[m] <= 0:
            del agent.materials[m]
    if v2(ctx.world.rules):
        for tool in tech.tools_needed(phys.Recipe.of(*concept.recipe)):
            wear(ctx, agent, tool)
    agent._practice("crafting", 0.5, ctx.world, ctx.tick, ctx.log)
    event_id = _emit(
        ctx,
        ev.MADE,
        actors=(agent.id,),
        position=agent.position,
        data={"concept": concept.id, "name": concept.name, "function": concept.function},
    )
    if concept.function == "shelter":
        build(ctx, agent, concept, event_id)
    elif concept.function == MATERIAL:  # S2.2: stuff to make other things from
        key = tech.made_key(concept.id)
        agent.materials[key] = agent.materials.get(key, 0) + 1
    else:
        add_item(agent, new_item(ctx, agent, concept), concepts, ctx.world.rules)
    return event_id


def build(ctx, agent, concept, cause):
    structures = ctx.world.structures
    sid = max((s["id"] for s in structures), default=0) + 1
    structures.append(
        {
            "id": sid,
            "concept": concept.id,
            "x": agent.position.x,
            "y": agent.position.y,
            "owner": agent.id,
            "built": ctx.tick,
            "group": agent.group_id,
        }
    )
    _emit(
        ctx,
        ev.BUILT,
        actors=(agent.id,),
        position=agent.position,
        data={"structure": sid, "concept": concept.id, "name": concept.name},
        cause_id=cause,
    )


def sheltered(agent, structures) -> bool:
    """Sleeping at (or next to) their own or their group's shelter."""
    for s in structures:
        mine = s["owner"] == agent.id or (
            agent.group_id is not None and s.get("group") == agent.group_id
        )
        if mine and abs(s["x"] - agent.position.x) + abs(s["y"] - agent.position.y) <= 1:
            return True
    return False


def teachable(teacher, learner, concepts) -> list:
    """What the teacher could show the learner: what solves the learner's worst trouble first."""
    need = {tech.SOLVES[p]: s for p, s in learner.problems.items() if p in tech.SOLVES}
    found = [
        concepts[c]
        for c in sorted(teacher.knowledge)
        if c in concepts and c not in learner.knowledge and concepts[c].lost_tick is None
    ]
    return sorted(found, key=lambda c: -need.get(c.function, 0.0))


def wants_to_learn(learner, concept, rules) -> float:
    """0..1: how much the learner's own trouble makes them want this."""
    for problem, score in learner.problems.items():
        if tech.SOLVES.get(problem) == concept.function:
            return min(1.0, score / rules.problem_threshold)
    return 0.0


def teach(ctx, teacher, learner, concept, reply_to=None, voice=None):
    from genesis.society.interaction import Act, feel, say

    event_id = say(
        ctx,
        teacher,
        learner,
        Act.TEACH,
        {"concept": concept.id, "name": concept.name, "function": concept.function},
        reply_to=reply_to,
        voice=voice,
    )
    chance = 0.4 + 0.4 * teacher.abilities.get("teaching") + 0.2 * learner.genome.intelligence
    r = ctx.world.rules
    chance += 0.15 * bool(r.talents and teacher.talent == "storyteller")  # M8d
    if teacher.bonus.get("marks"):  # S2.2: marks help to show how
        chance += 0.1
        wear(ctx, teacher, "marks")
    learned = ctx.rng.random() < chance
    teacher._practice("teaching", 1.0 if learned else 0.3, ctx.world, ctx.tick, ctx.log)
    if learned:
        learner.knowledge.add(concept.id)
        feel(ctx, learner, teacher, "taught", event_id)
    _emit(
        ctx,
        ev.LEARNED,
        actors=(learner.id,),
        targets=(teacher.id,),
        data={"concept": concept.id, "name": concept.name, "learned": learned},
        cause_id=event_id,
    )
    return event_id


def _recorded(world, concept_id: int) -> bool:
    from genesis.culture import research

    return research.recorded(world, concept_id)


def check_lost(ctx, died_event):
    """Knowledge held by nobody alive is gone."""
    living = [a for a in ctx.agents.values() if a.alive]
    for concept in sorted(ctx.world.concepts.values(), key=lambda c: c.id):
        if concept.lost_tick is not None:
            continue
        if not any(concept.id in a.knowledge for a in living) and not (
            ctx.world.rules.research and _recorded(ctx.world, concept.id)
        ):
            concept.lost_tick = ctx.tick
            _emit(
                ctx,
                ev.CONCEPT_LOST,
                data={"concept": concept.id, "name": concept.name, "function": concept.function},
                cause_id=died_event,
            )
