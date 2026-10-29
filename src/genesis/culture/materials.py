"""The valley's materials and the hidden physics of making things (M8b). See docs/15 (15.2).

Agents never see these numbers. They gather materials, try combinations, and find out what
works. Whatever the LLM imagines, a thing can only do what its properties allow, so nothing
from our world works just because it is named after something from our world.
"""

from dataclasses import dataclass

PROPERTIES = ("hard", "sharp", "flexible", "long", "hollow", "watertight", "heavy", "shiny")

# Base properties of each raw material (0..1), and where it is found.
MATERIALS: dict[str, dict[str, float]] = {
    "wood": {
        "hard": 0.5,
        "sharp": 0.1,
        "flexible": 0.3,
        "long": 0.8,
        "hollow": 0.1,
        "watertight": 0.2,
        "heavy": 0.4,
        "shiny": 0.0,
    },
    "stone": {
        "hard": 0.9,
        "sharp": 0.2,
        "flexible": 0.0,
        "long": 0.2,
        "hollow": 0.0,
        "watertight": 0.9,
        "heavy": 0.9,
        "shiny": 0.1,
    },
    "flint": {
        "hard": 0.9,
        "sharp": 0.6,
        "flexible": 0.0,
        "long": 0.1,
        "hollow": 0.0,
        "watertight": 0.8,
        "heavy": 0.5,
        "shiny": 0.3,
    },
    "reed": {
        "hard": 0.1,
        "sharp": 0.0,
        "flexible": 0.9,
        "long": 0.9,
        "hollow": 0.3,
        "watertight": 0.3,
        "heavy": 0.1,
        "shiny": 0.0,
    },
    "clay": {
        "hard": 0.2,
        "sharp": 0.0,
        "flexible": 0.7,
        "long": 0.1,
        "hollow": 0.2,
        "watertight": 0.7,
        "heavy": 0.6,
        "shiny": 0.0,
    },
    "fibre": {
        "hard": 0.0,
        "sharp": 0.0,
        "flexible": 1.0,
        "long": 0.8,
        "hollow": 0.0,
        "watertight": 0.1,
        "heavy": 0.0,
        "shiny": 0.0,
    },
    "shell": {
        "hard": 0.6,
        "sharp": 0.3,
        "flexible": 0.0,
        "long": 0.0,
        "hollow": 0.5,
        "watertight": 0.8,
        "heavy": 0.1,
        "shiny": 0.9,
    },
    "bright stone": {
        "hard": 0.8,
        "sharp": 0.1,
        "flexible": 0.0,
        "long": 0.0,
        "hollow": 0.0,
        "watertight": 0.9,
        "heavy": 0.3,
        "shiny": 0.9,
    },
}

# terrain -> [(material, chance per gathering hour)]
SOURCES: dict[str, list[tuple[str, float]]] = {
    "forest": [("wood", 1.0)],
    "mountain": [("stone", 1.0), ("flint", 0.25), ("bright stone", 0.05)],
    "shore": [("reed", 0.6), ("clay", 0.6), ("shell", 0.05)],
    "plains": [("fibre", 0.5)],
}

# What working a material does to it. Each action needs something to work on.
ACTIONS: dict[str, dict] = {
    "hollow": {"changes": {"hollow": +0.5, "long": -0.2}, "needs": {"hard": 0.1}},
    "sharpen": {"changes": {"sharp": +0.4}, "needs": {"hard": 0.6}},
    "weave": {"changes": {"hollow": +0.4, "flexible": +0.1}, "needs": {"flexible": 0.7}},
    "tie": {"changes": {}, "binds": True, "needs": {}},
    "shape": {"changes": {"hollow": +0.2, "watertight": +0.1}, "needs": {"flexible": 0.5}},
    "smooth": {"changes": {"shiny": +0.2, "sharp": -0.2}, "needs": {}},
}

# The functions a made thing can have: (required properties, effect).
FUNCTIONS: dict[str, dict] = {
    "holds water": {"needs": {"hollow": 0.6, "watertight": 0.6}, "effect": "+2 drinks carried"},
    "holds food": {"needs": {"hollow": 0.6}, "effect": "+10 food carried"},
    "cuts": {
        "needs": {"sharp": 0.6, "hard": 0.6},
        "effect": "more food per meal gathered; stronger in a fight",
    },
    "reaches": {"needs": {"long": 0.6, "hard": 0.5}, "effect": "much stronger in a fight"},
    "binds": {"needs": {"flexible": 0.7, "long": 0.6}, "effect": "lets things be tied together"},
    "shelter": {
        "needs": {"long": 0.5, "heavy": 0.3},
        "bound": True,
        "effect": "a place to sleep that restores more",
    },
    "adorns": {"needs": {"shiny": 0.7}, "effect": "pretty: people like to have it"},
}

# Which problem each function answers (problems come from what agents live through).
SOLVES: dict[str, str] = {
    "carry water": "holds water",
    "carry food": "holds food",
    "gather food": "cuts",
    "win fights": "reaches",
    "rest": "shelter",
    "be admired": "adorns",  # M8f
}


@dataclass(frozen=True)
class Recipe:
    materials: tuple[str, ...]  # sorted
    actions: tuple[str, ...]  # in order

    @classmethod
    def of(cls, materials, actions) -> "Recipe":
        return cls(tuple(sorted(materials)), tuple(actions))

    def to_list(self) -> list:
        return [list(self.materials), list(self.actions)]


def properties(recipe: Recipe) -> tuple[dict[str, float], bool]:
    """(the made thing's properties, whether it is bound together). Pure function."""
    if not recipe.materials:
        return {p: 0.0 for p in PROPERTIES}, False
    parts = [MATERIALS[m] for m in recipe.materials if m in MATERIALS]
    props = {
        p: max(part[p] for part in parts)
        if p in ("long", "hard", "sharp", "shiny")
        else sum(part[p] for part in parts) / len(parts)
        for p in PROPERTIES
    }
    bound = False
    for action in recipe.actions:
        spec = ACTIONS.get(action)
        if spec is None:
            continue
        if any(props[k] < v for k, v in spec["needs"].items()):
            continue  # nothing here can be worked that way
        if spec.get("binds"):
            binder = any(
                MATERIALS[m]["flexible"] >= 0.7 and MATERIALS[m]["long"] >= 0.6
                for m in recipe.materials
            )
            bound = bound or (binder and len(recipe.materials) >= 2)
            continue
        for k, dv in spec["changes"].items():
            props[k] = min(1.0, max(0.0, props[k] + dv))
    return props, bound


def functions(recipe: Recipe) -> list[str]:
    """What the made thing can do, decided by the physics alone."""
    props, bound = properties(recipe)
    out = []
    for name, spec in FUNCTIONS.items():
        if spec.get("bound") and not bound:
            continue
        if all(props[k] >= v for k, v in spec["needs"].items()):
            out.append(name)
    return out
