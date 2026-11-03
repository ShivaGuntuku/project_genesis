"""Open-ended technology (S2.2): the hidden physics of making things, version 2.

Worlds with `rules.inventions = 2` use this module; `inventions = 1` keeps materials.py, so
older worlds go on exactly as before. Agents never see these numbers.

- 16 properties and 14 raw materials, found on the land (forest, mountain, shore, grass).
- Made things become materials: a concept of kind "material" (cord, fired clay, charcoal...)
  has the properties its recipe gives, and goes into other recipes as "#<concept id>".
  Chains of any depth follow.
- 12 ways of working things. Heating and burning need fire (something that "makes fire");
  splitting needs something that "cuts".
- 19 uses, decided by the properties alone. Each has an effect in the world and a trouble
  that makes people look for it. No raw material does anything useful unworked.
"""

from genesis.culture.materials import Recipe

PROPERTIES = (
    "hard",
    "sharp",
    "flexible",
    "long",
    "hollow",
    "watertight",
    "heavy",
    "shiny",
    "burns",
    "heatproof",
    "strong",
    "warm",
    "floats",
    "sticky",
    "absorbs",
    "edge",
)
# Loose parts are a pile: every property is their average. Bound into one thing, it is as
# long (hard, sharp, ...) as its best part for these.
STRONGEST = ("hard", "sharp", "shiny", "burns", "strong", "edge", "long")


def _m(**v) -> dict[str, float]:
    return {p: float(v.get(p, 0.0)) for p in PROPERTIES}


MATERIALS: dict[str, dict[str, float]] = {
    "wood": _m(hard=0.5, sharp=0.1, flexible=0.3, long=0.8, hollow=0.1, watertight=0.2,
               heavy=0.4, burns=0.7, strong=0.6, warm=0.1, floats=0.6, absorbs=0.2),
    "stone": _m(hard=0.9, sharp=0.2, long=0.2, watertight=0.9, heavy=0.9, shiny=0.1,
                heatproof=0.9, strong=0.8, edge=0.1),
    "flint": _m(hard=0.9, sharp=0.5, long=0.1, watertight=0.8, heavy=0.5, shiny=0.3,
                heatproof=0.8, strong=0.5, edge=0.4),
    "reed": _m(hard=0.1, flexible=0.9, long=0.9, hollow=0.3, watertight=0.3, heavy=0.1,
               burns=0.5, strong=0.3, warm=0.2, floats=0.8, absorbs=0.3),
    "clay": _m(hard=0.2, flexible=0.7, long=0.1, hollow=0.2, watertight=0.7, heavy=0.6,
               heatproof=0.5, strong=0.2, warm=0.1, sticky=0.5, absorbs=0.4),
    "fibre": _m(flexible=1.0, long=0.8, watertight=0.1, burns=0.6, strong=0.4, warm=0.5,
                floats=0.3, absorbs=0.5),
    "shell": _m(hard=0.6, sharp=0.3, hollow=0.5, watertight=0.8, heavy=0.1, shiny=0.6,
                heatproof=0.3, strong=0.2, floats=0.2, edge=0.3),
    "bright stone": _m(hard=0.8, sharp=0.1, watertight=0.9, heavy=0.3, shiny=0.65,
                       heatproof=0.7, strong=0.5, edge=0.1),
    "bark": _m(hard=0.3, flexible=0.6, long=0.5, hollow=0.3, watertight=0.5, heavy=0.2,
               burns=0.6, strong=0.4, warm=0.6, floats=0.5, absorbs=0.3),
    "resin": _m(hard=0.1, flexible=0.4, watertight=0.9, heavy=0.2, shiny=0.4, burns=0.9,
                strong=0.1, warm=0.1, floats=0.3, sticky=0.9),
    "sand": _m(hard=0.7, sharp=0.2, flexible=0.3, watertight=0.1, heavy=0.6, shiny=0.2,
               heatproof=0.8, absorbs=0.4, edge=0.2),
    "ochre": _m(hard=0.3, flexible=0.2, watertight=0.3, heavy=0.5, shiny=0.3, heatproof=0.5,
                sticky=0.3, absorbs=0.6),
    "obsidian": _m(hard=0.8, sharp=0.6, long=0.1, watertight=0.9, heavy=0.4, shiny=0.6,
                   heatproof=0.6, strong=0.3, edge=0.5),
    "grass": _m(flexible=0.9, long=0.5, hollow=0.1, watertight=0.1, burns=0.9, strong=0.2,
                warm=0.6, floats=0.4, absorbs=0.6),
}  # fmt: skip

# terrain -> [(material, chance per gathering hour)]
SOURCES: dict[str, list[tuple[str, float]]] = {
    "forest": [("wood", 1.0), ("bark", 0.5), ("resin", 0.2)],
    "mountain": [
        ("stone", 1.0),
        ("flint", 0.25),
        ("ochre", 0.15),
        ("bright stone", 0.05),
        ("obsidian", 0.05),
    ],
    # pebbles and flint nodules wash out along the shore and turn up in the grass
    "shore": [
        ("reed", 0.6),
        ("clay", 0.6),
        ("sand", 0.4),
        ("stone", 0.15),
        ("flint", 0.05),
        ("shell", 0.05),
    ],
    "plains": [("grass", 0.6), ("fibre", 0.5), ("stone", 0.03)],
}
GROUND = {"stone", "flint", "ochre", "bright stone", "obsidian", "clay", "sand"}  # "digs"
WATERSIDE = {"reed", "shell"}  # "floats": reached from the water

# What working things does. "needs": what the thing must already be; "tool": a use the
# worker must have in hand (fire, a cutting edge); "binds": holds parts together.
ACTIONS: dict[str, dict] = {
    "hollow": {"changes": {"hollow": +0.5, "long": -0.2}, "needs": {"hard": 0.1}},
    "sharpen": {"changes": {"sharp": +0.4, "edge": +0.2}, "needs": {"hard": 0.6, "strong": 0.2}},
    "weave": {
        "changes": {"hollow": +0.4, "flexible": +0.1, "warm": +0.2, "strong": +0.1},
        "needs": {"flexible": 0.7},
    },
    "bind": {"changes": {}, "binds": True, "needs": {}},
    "shape": {"changes": {"hollow": +0.2, "watertight": +0.1}, "needs": {"flexible": 0.5}},
    "smooth": {"changes": {"shiny": +0.2, "sharp": -0.2}, "needs": {}},
    "heat": {
        "changes": {
            "hard": +0.5,
            "watertight": +0.2,
            "heatproof": +0.4,
            "flexible": -0.5,
            "sticky": -0.4,
        },
        "needs": {"flexible": 0.5, "heavy": 0.3},
        "tool": "makes fire",
    },
    "burn": {
        "changes": {"burns": +0.3, "hard": -0.4, "long": -0.4, "heavy": -0.3, "absorbs": +0.3},
        "needs": {"burns": 0.5},
        "tool": "makes fire",
    },
    "twist": {
        "changes": {"strong": +0.4, "long": +0.1, "flexible": -0.1},
        "needs": {"flexible": 0.7},
    },
    "grind": {
        "changes": {"hard": -0.5, "long": -0.6, "sharp": -0.3, "absorbs": +0.3, "sticky": +0.2},
        "needs": {"hard": 0.2},
    },
    "split": {
        "changes": {"long": +0.1, "strong": +0.2, "heavy": -0.2, "flexible": +0.1},
        "needs": {"long": 0.6, "hard": 0.4},
        "tool": "cuts",
    },
    "polish": {
        "changes": {"shiny": +0.3, "edge": +0.2, "sharp": +0.1},
        "needs": {"hard": 0.5, "strong": 0.2},
    },
    "soak": {
        "changes": {"flexible": +0.3, "hard": -0.2, "absorbs": +0.2},
        "needs": {"absorbs": 0.3},
    },
}  # fmt: skip

# The uses a made thing can have: (required properties, effect). Effects live where the world
# works (agent.py, disasters.py, inventions.py); `effect` is how people describe it.
FUNCTIONS: dict[str, dict] = {
    "holds water": {"needs": {"hollow": 0.6, "watertight": 0.6}, "effect": "+2 drinks carried"},
    "holds food": {"needs": {"hollow": 0.6}, "effect": "+10 food carried"},
    "cuts": {
        "needs": {"sharp": 0.7, "hard": 0.6, "edge": 0.5},
        "effect": "more food per meal gathered; stronger in a fight",
    },
    "reaches": {
        "needs": {"long": 0.7, "hard": 0.6, "strong": 0.6},
        "effect": "much stronger in a fight",
    },
    "binds": {
        "needs": {"flexible": 0.7, "long": 0.6, "strong": 0.6},
        "effect": "ties bundles: carry more materials",
    },
    "shelter": {
        "needs": {"long": 0.5, "heavy": 0.3, "strong": 0.5},
        "bound": True,
        "effect": "a place to sleep that restores more",
    },
    "adorns": {"needs": {"shiny": 0.8}, "effect": "pretty: people like to have it"},
    "pierces": {
        "needs": {"sharp": 0.7, "long": 0.6, "edge": 0.5},
        "effect": "drives off beasts; stronger in a fight",
    },
    "digs": {
        "needs": {"hard": 0.7, "long": 0.6, "strong": 0.7},
        "effect": "gets more from the ground (stone, clay, flint...)",
    },
    "grinds": {
        "needs": {"hard": 0.8, "heavy": 0.8, "shiny": 0.3},
        "effect": "meals nourish more",
    },
    "carries loads": {
        "needs": {"flexible": 0.6, "strong": 0.6, "hollow": 0.5, "floats": 0.3},  # light
        "effect": "carry many more materials",
    },
    "floats": {
        "needs": {"floats": 0.6, "long": 0.6},
        "bound": True,
        "effect": "reach reeds and shells from the water",
    },
    "keeps warm": {
        "needs": {"warm": 0.7, "flexible": 0.5},
        "effect": "the cold hurts less; sleep better in the cold season",
    },
    "makes fire": {
        "needs": {"burns": 0.7, "hard": 0.8, "sharp": 0.5},
        "effect": "fire: warmth, beasts kept off, things heated or burnt",
    },
    "cooks": {
        "needs": {"hollow": 0.6, "heatproof": 0.8, "watertight": 0.6},
        "effect": "with fire, meals nourish much more",
    },
    "lights": {
        "needs": {"burns": 0.8, "long": 0.6, "sticky": 0.4},
        "effect": "see further; beasts keep away at night",
    },
    "stores food": {
        "needs": {"hollow": 0.6, "watertight": 0.7, "hard": 0.7},
        "effect": "food carried doesn't spoil",
    },
    "heals": {"needs": {"absorbs": 0.7, "sticky": 0.4}, "effect": "sickness hurts less"},
    "marks": {
        "needs": {"edge": 0.6, "hard": 0.6, "shiny": 0.4},
        "effect": "marks that help to show and remember",
    },
}

# Which trouble each use answers (troubles come from what people live through). The first six
# are Stage 1's, unchanged, so lookups for older worlds give the same answers.
SOLVES: dict[str, str] = {
    "carry water": "holds water",
    "carry food": "holds food",
    "gather food": "cuts",
    "win fights": "reaches",
    "rest": "shelter",
    "be admired": "adorns",
    "cold": "keeps warm",
    "beasts": "pierces",
    "dark": "lights",
    "food spoils": "stores food",
    "heavy loads": "carries loads",
    "sickness": "heals",
    "counting trades": "marks",
    "hard ground": "digs",
    "reeds out of reach": "floats",
    "meals don't fill": "grinds",
    "no fire": "makes fire",
    "raw food": "cooks",
    "things fall apart": "binds",
}
NEED_WORDS = {
    "cold": "The cold gets into your bones at night.",
    "beasts": "Beasts prowl at night, and you have nothing to keep them off.",
    "dark": "The nights are dark; you can't see what is out there.",
    "food spoils": "Food you carry goes bad before you can eat it.",
    "heavy loads": "You can't carry everything you gather.",
    "sickness": "Sickness lays you low and you can do nothing about it.",
    "counting trades": "You trade a lot and find it hard to keep track.",
    "hard ground": "Getting stone and clay out of the ground is slow work.",
    "reeds out of reach": "The best reeds and shells are out in the water, out of reach.",
    "meals don't fill": "Your meals don't fill you for long.",
    "no fire": "You wish you had a fire: for warmth, for light, against beasts.",
    "raw food": "You have fire, but your food is still eaten raw and hard.",
    "things fall apart": "What you put together keeps falling apart.",
}

# What having a use makes people want next (S2.2): one thing opens the way to others, as fire
# led to cooking, pots and fired clay. Every use leads somewhere, so making never runs dry.
LEADS_TO: dict[str, tuple[str, ...]] = {
    "holds water": ("cooks", "stores food"),
    "holds food": ("stores food", "carries loads"),
    "cuts": ("pierces", "marks", "binds"),
    "reaches": ("pierces", "digs"),
    "binds": ("carries loads", "floats", "shelter"),
    "shelter": ("stores food", "keeps warm"),
    "adorns": ("marks", "lights"),
    "pierces": ("floats", "makes fire"),
    "digs": ("stores food", "grinds"),
    "grinds": ("heals", "adorns"),
    "carries loads": ("floats", "digs"),
    "floats": ("pierces", "binds"),
    "keeps warm": ("makes fire", "shelter"),
    "makes fire": ("cooks", "lights", "keeps warm"),
    "cooks": ("heals", "stores food"),
    "lights": ("marks", "adorns"),
    "stores food": ("marks", "cooks"),
    "heals": ("grinds", "marks"),
    "marks": ("adorns", "heals"),
}

MADE = "#"  # a made material in a recipe or a pack: "#<concept id>"


def is_made(material: str) -> bool:
    return material.startswith(MADE)


def made_key(concept_id: int) -> str:
    return f"{MADE}{concept_id}"


def properties(
    recipe: Recipe, made: dict[str, dict[str, float]] | None = None
) -> tuple[dict[str, float], bool]:
    """(the made thing's properties, whether it is bound together). Pure function. `made`
    gives the properties of made materials ("#<id>") the recipe uses."""
    found = [MATERIALS[m] if m in MATERIALS else (made or {}).get(m) for m in recipe.materials]
    parts: list[dict[str, float]] = [p for p in found if p is not None]
    if not parts:
        return {p: 0.0 for p in PROPERTIES}, False
    props = {p: sum(part[p] for part in parts) / len(parts) for p in PROPERTIES}
    bound = False
    for action in recipe.actions:
        spec = ACTIONS.get(action)
        if spec is None or any(props[k] < v for k, v in spec["needs"].items()):
            continue  # nothing here can be worked that way
        if spec.get("binds"):
            binder = any(
                part["sticky"] >= 0.6 or (part["flexible"] >= 0.7 and part["long"] >= 0.6)
                for part in parts
            )
            if binder and len(parts) >= 2 and not bound:
                bound = True
                for p in STRONGEST:
                    props[p] = max(props[p], max(part[p] for part in parts))
            continue
        for k, dv in spec["changes"].items():
            props[k] = round(min(1.0, max(0.0, props[k] + dv)), 6)
    return props, bound


def functions(recipe: Recipe, made: dict[str, dict[str, float]] | None = None) -> list[str]:
    """What the made thing can do, decided by the physics alone."""
    props, bound = properties(recipe, made)
    out = []
    for name, spec in FUNCTIONS.items():
        if spec.get("bound") and not bound:
            continue
        if all(props[k] >= v for k, v in spec["needs"].items()):
            out.append(name)
    return out


def tools_needed(recipe: Recipe) -> list[str]:
    """Uses the maker must have in hand for this recipe (fire for heating, ...)."""
    return sorted({ACTIONS[a]["tool"] for a in recipe.actions if ACTIONS.get(a, {}).get("tool")})


def novel(recipe: Recipe, made: dict[str, dict[str, float]] | None = None) -> bool:
    """A new kind of stuff: working it raised some property well above every part (cord from
    fibre, fired clay, charcoal). Such a thing can be kept as a material for other recipes."""
    if not recipe.actions:
        return False
    found = [MATERIALS.get(m) or (made or {}).get(m) for m in recipe.materials]
    parts: list[dict[str, float]] = [p for p in found if p is not None]
    if not parts:
        return False
    props, _ = properties(recipe, made)
    return any(props[p] >= 0.7 and props[p] >= max(x[p] for x in parts) + 0.3 for p in PROPERTIES)


def fit(recipe: Recipe, function: str, made=None) -> float:
    """0..1: how far above the needs of a use the thing is (better materials, better thing)."""
    props, _ = properties(recipe, made)
    needs = FUNCTIONS[function]["needs"]
    if not needs:
        return 0.5
    margin = sum(min(1.0, (props[k] - v) / max(0.01, 1 - v)) for k, v in needs.items())
    return max(0.0, min(1.0, margin / len(needs)))
