"""Learned skills (0.0 to 1.0). Not inherited; they grow with practice.

Growth per practice = rate × amount × (1 - level) × (0.5 + intelligence):
fast at first, slower near mastery, faster for quick learners.
"""

SKILLS = (
    "foraging",  # more nourishment per unit of food eaten
    "water_finding",  # sees further while searching for water
    "combat",  # used from M7
    "persuasion",  # used from M3
    "teaching",  # used from M3/M6
    "leadership",  # used from M7
    "crafting",  # making things (M8b)
    "healing",  # tending others' wounds (M8d)
    "scouting",  # knowing the land: finding the way, seeing what is where (M8f)
)

# Level names, crossed upward. Each crossing is an event in the history.
LEVELS = ((0.25, "capable"), (0.5, "skilled"), (0.75, "expert"))

SKILL_NOUNS = {
    "foraging": "forager",
    "water_finding": "water-finder",
    "combat": "fighter",
    "persuasion": "persuader",
    "teaching": "teacher",
    "leadership": "leader",
    "crafting": "crafter",
    "healing": "healer",
    "scouting": "scout",
}


def level_name(value: float) -> str:
    name = "untrained" if value < 0.05 else "novice"
    for threshold, label in LEVELS:
        if value >= threshold:
            name = label
    return name


def describe_skill(skill: str, value: float) -> str:
    return f"{level_name(value)} {SKILL_NOUNS[skill]}"


class Abilities:
    def __init__(self, values: dict | None = None):
        self.values = {s: 0.0 for s in SKILLS}
        if values:
            unknown = set(values) - set(SKILLS)
            if unknown:
                raise ValueError(f"Unknown skill(s): {unknown}")
            self.values.update(values)

    def get(self, skill: str) -> float:
        return self.values[skill]

    def practice(self, skill: str, amount: float, intelligence: float, rate: float) -> str | None:
        """Improve a skill. Returns a level name if a new level was just reached."""
        before = self.values[skill]
        gain = rate * amount * (1.0 - before) * (0.5 + intelligence)
        after = min(1.0, before + gain)
        self.values[skill] = after

        for threshold, label in LEVELS:
            if before < threshold <= after:
                return label
        return None

    def trained(self) -> dict:
        return {s: v for s, v in self.values.items() if v >= 0.05}

    def to_dict(self) -> dict:
        return dict(self.values)
