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
}
NEED_WORDS = {
    "carry water": "You keep running out of water when you are far from the lake.",
    "carry food": "You keep going hungry when you are far from the berries.",
    "gather food": "Gathering enough berries is slow work.",
    "win fights": "You have been beaten in a fight.",
    "rest": "You often can't rest well; you wake tired.",
    "be admired": "You want others to notice you and look up to you.",
}


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
    _notice_solutions(ctx, agent)


def _notice_solutions(ctx, agent):
    """Seeing a neighbour with a thing that solves their own trouble: they would rather learn
    it than tinker from scratch (one idea spreads instead of being invented ten times)."""
    problem = main_problem(agent, ctx.world.rules)
    if problem is None:
        return
    target = phys.SOLVES[problem]
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
    return [
        concepts[c]
        for c in sorted(agent.knowledge)
        if c in concepts and function in concepts[c].functions
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
    """Wear down one item that serves the function; it breaks when used up."""
    for i in agent.items:
        if i["concept"] in concepts and function in concepts[i["concept"]].functions:
            i["uses"] -= 1
            if i["uses"] <= 0:
                agent.items.remove(i)
                refresh_bonus(agent, concepts)
            return


def refresh_bonus(agent, concepts):
    """What their things add (kept on the agent so capacity checks stay cheap)."""
    agent.bonus = {
        "water": 2 * item_bonus(agent, concepts, "holds water"),
        "food": 10 * item_bonus(agent, concepts, "holds food"),
        "cuts": item_bonus(agent, concepts, "cuts"),
        "reaches": item_bonus(agent, concepts, "reaches"),
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
    if cached is not None and cached[0] is world.burned:
        return cached[1]
    grids = {m: np.zeros((world.width, world.height), dtype=bool) for m in phys.MATERIALS}
    for p in world.walkable:
        for m, _ in phys.SOURCES.get(terrain_of(world, p), []):
            grids[m][p.x, p.y] = True
    world._material_grids = (world.burned, grids)
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


def gather(ctx, agent) -> str | None:
    """An hour of gathering here: one unit of something this place gives (by chance)."""
    r = ctx.world.rules
    if carrying_materials(agent) >= r.materials_capacity:
        return None
    sources = phys.SOURCES.get(terrain_of(ctx.world, agent.position), [])
    wanted = agent.project.get("material") if agent.project else None
    for material, chance in sorted(sources, key=lambda s: (s[0] != wanted, s[0])):
        if ctx.rng.random() < chance:
            agent.materials[material] = agent.materials.get(material, 0) + 1
            return material
    return None


# =================================================================
# Experimenting and inventing
# =================================================================


def experiment(ctx, agent, problem: str):
    """Try a recipe for a problem. Returns the event id (invented, or a failed try)."""
    r = ctx.world.rules
    target = phys.SOLVES[problem]
    in_hand = material_kinds(agent)
    idea = _llm_idea(ctx, agent, problem, in_hand) if ctx.mind is not None else None
    if idea is not None:
        mats, acts, description = idea
    else:
        k = min(len(in_hand), ctx.rng.choice((1, 2, 2, 3)))
        mats = ctx.rng.sample(in_hand, k) if k else []
        acts = [ctx.rng.choice(sorted(phys.ACTIONS)) for _ in range(ctx.rng.choice((1, 2)))]
        description = ""
    recipe = phys.Recipe.of(mats, acts)
    works = phys.functions(recipe)
    skill = agent.abilities.get("crafting")
    chance = r.invent_base_chance + 0.4 * skill + 0.2 * agent.genome.intelligence
    chance += 0.15 * bool(r.talents and agent.talent == "tinkerer")  # M8d
    succeeded = target in works and ctx.rng.random() < chance

    for m in recipe.materials:  # trying uses up one of each material
        agent.materials[m] = agent.materials.get(m, 0) - 1
        if agent.materials[m] <= 0:
            del agent.materials[m]
    agent._practice("crafting", 1.0 if succeeded else 0.3, ctx.world, ctx.tick, ctx.log)
    agent.asked["tinkered"] = ctx.tick

    if not succeeded:
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

    concept = _new_concept(ctx, agent, target, works, recipe, description)
    agent.knowledge.add(concept.id)
    if target != "shelter":
        agent.items.append({"concept": concept.id, "uses": ITEM_USES.get(target, 100)})
        refresh_bonus(agent, ctx.world.concepts)
    agent.problems[problem] = 0.0
    event_id = _emit(
        ctx,
        ev.INVENTED,
        actors=(agent.id,),
        position=agent.position,
        data={
            "concept": concept.id,
            "name": concept.name,
            "function": target,
            "functions": works,
            "recipe": recipe.to_list(),
            "idea": description,
            "same_as": concept.same_as,
            "leaks": leaks(description),
        },
    )
    if target == "shelter":
        build(ctx, agent, concept, event_id)
    return event_id


def _new_concept(ctx, agent, target, works, recipe, description) -> "Concept":
    concepts = ctx.world.concepts
    older = next(
        (c for c in sorted(concepts.values(), key=lambda c: c.id) if c.function == target), None
    )
    taken = {c.name.lower() for c in concepts.values()}
    taken |= {a.name.lower() for a in ctx.agents.values()}
    taken |= {g.name.lower() for g in ctx.groups.values()}
    name = _llm_name(ctx, agent, target, recipe, description, taken) if ctx.mind else None
    if name is None:
        while True:
            name = "".join(ctx.rng.choice(SYLLABLES) for _ in range(ctx.rng.choice((2, 3))))
            if name not in taken and is_valley_word(name, taken):
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
    )
    concepts[cid] = concept
    return concept


def _llm_idea(ctx, agent, problem, in_hand):
    """The agent's own idea in persona: materials from what they hold, actions they can do."""
    from genesis.llm import persona

    mind = ctx.mind
    if mind is None or not getattr(mind, "active", False) or not in_hand:
        return None
    actions = sorted(phys.ACTIONS)

    def prompt():
        return "\n".join(
            [
                persona.fact_block(agent, ctx),
                "",
                NEED_WORDS[problem],
                f"You have: {', '.join(in_hand)}. You can work things in these ways: "
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
            "materials": {"type": "array", "items": {"type": "string", "enum": in_hand}},
            "actions": {"type": "array", "items": {"type": "string", "enum": actions}},
        },
        "required": ["thought", "idea", "materials", "actions"],
    }

    def parse(text, record):
        try:
            data = json.loads(text)
            mats = [m for m in data.get("materials", []) if m in in_hand][:3]
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
                f"You made something new that works! It {phys.FUNCTIONS[function]['effect']}. "
                f"You made it from {', '.join(recipe.materials)}"
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


def can_make(agent, concept) -> bool:
    mats = concept.recipe[0]
    return all(agent.materials.get(m, 0) >= 1 for m in mats)


def missing_material(agent, concept) -> str | None:
    for m in concept.recipe[0]:
        if agent.materials.get(m, 0) < 1:
            return m
    return None


def make(ctx, agent, concept):
    if not can_make(agent, concept):
        return None
    for m in concept.recipe[0]:
        agent.materials[m] -= 1
        if agent.materials[m] <= 0:
            del agent.materials[m]
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
    else:
        agent.items.append({"concept": concept.id, "uses": ITEM_USES.get(concept.function, 100)})
        refresh_bonus(agent, ctx.world.concepts)
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
    need = {phys.SOLVES[p]: s for p, s in learner.problems.items() if p in phys.SOLVES}
    found = [
        concepts[c]
        for c in sorted(teacher.knowledge)
        if c in concepts and c not in learner.knowledge and concepts[c].lost_tick is None
    ]
    return sorted(found, key=lambda c: -need.get(c.function, 0.0))


def wants_to_learn(learner, concept, rules) -> float:
    """0..1: how much the learner's own trouble makes them want this."""
    for problem, score in learner.problems.items():
        if phys.SOLVES.get(problem) == concept.function:
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


def check_lost(ctx, died_event):
    """Knowledge held by nobody alive is gone."""
    living = [a for a in ctx.agents.values() if a.alive]
    for concept in sorted(ctx.world.concepts.values(), key=lambda c: c.id):
        if concept.lost_tick is not None:
            continue
        if not any(concept.id in a.knowledge for a in living):
            concept.lost_tick = ctx.tick
            _emit(
                ctx,
                ev.CONCEPT_LOST,
                data={"concept": concept.id, "name": concept.name, "function": concept.function},
                cause_id=died_event,
            )
