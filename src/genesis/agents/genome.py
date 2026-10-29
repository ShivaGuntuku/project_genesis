"""Genes: the inherited, lifelong nature of an agent. See docs/03-agent-model.md.

Every gene is a number from 0.0 to 1.0. Founders get random genes; children inherit
a mix of both parents' genes plus small random mutations.
"""

from dataclasses import dataclass, fields
from random import Random

MIND_GENES = (
    "curiosity",  # likes exploring
    "sociability",  # seeks company
    "aggression",  # quick to threaten or fight (used from M7)
    "risk_tolerance",  # accepts danger
    "generosity",  # shares and helps (used from M3)
    "ambition",  # wants status and resources
    "honesty",  # tells the truth (used from M3)
    "intelligence",  # learns skills faster
)
BODY_GENES = (
    "strength",  # fights, carrying (used from M3/M7)
    "endurance",  # slower energy drain
    "metabolism",  # high = gets hungry and thirsty faster
    "perception",  # vision radius
    "fertility",  # chance a mating produces a child (used from M6)
    "longevity",  # natural lifespan
    "resilience",  # heals faster, takes less damage
)
# Looks only (no effect on behaviour), inherited like the rest so families look alike.
APPEARANCE_GENES = (
    "skin",  # skin tone
    "hair",  # hair colour
    "eyes",  # eye colour
)
GENES = MIND_GENES + BODY_GENES + APPEARANCE_GENES
# Daily rhythm (M8a): when and how long they sleep. Drawn from their own seeded source (see
# Population), so adding them changed nothing else about existing worlds.
RHYTHM_GENES = (
    "sleep_need",  # 0 = 6 hours of sleep ... 1 = 10 hours
    "chronotype",  # 0 = early bird (bed at 20:00) ... 1 = night owl (bed at 02:00)
)
ALL_GENES = GENES + RHYTHM_GENES

# Words for very high (>= 0.75) and very low (<= 0.25) genes.
DESCRIPTORS = {
    "curiosity": ("endlessly curious", "incurious"),
    "sociability": ("very sociable", "a loner"),
    "aggression": ("aggressive", "gentle"),
    "risk_tolerance": ("a risk-taker", "cautious"),
    "generosity": ("generous", "selfish"),
    "ambition": ("ambitious", "unambitious"),
    "honesty": ("honest", "deceitful"),
    "intelligence": ("quick to learn", "slow to learn"),
    "strength": ("physically powerful", "physically weak"),
    "endurance": ("tireless", "tires quickly"),
    "metabolism": ("always hungry", "needs little food"),
    "perception": ("sharp-eyed", "short-sighted"),
    "fertility": ("very fertile", "rarely fertile"),
    "longevity": ("long-lived stock", "short-lived stock"),
    "resilience": ("hardy", "frail"),
}
# For these genes a HIGH value is a weakness (and a low value a strength).
HIGH_IS_WEAK = {"metabolism"}
# Character genes: extreme values are traits, not strengths or weaknesses.
NEUTRAL_TRAITS = {"curiosity", "sociability", "aggression", "risk_tolerance", "ambition"}
HIGH, LOW = 0.75, 0.25


@dataclass(frozen=True)
class Genome:
    curiosity: float
    sociability: float
    aggression: float
    risk_tolerance: float
    generosity: float
    ambition: float
    honesty: float
    intelligence: float
    strength: float
    endurance: float
    metabolism: float
    perception: float
    fertility: float
    longevity: float
    resilience: float
    skin: float
    hair: float
    eyes: float
    sleep_need: float = 0.5
    chronotype: float = 0.5

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"Gene {f.name} must be between 0.0 and 1.0, got {value}")

    # --------------------------------
    # Creation
    # --------------------------------

    @classmethod
    def random(cls, rng: Random) -> "Genome":
        """Founder genes from the world's seeded RNG (never the global one)."""
        return cls(**{g: round(rng.random(), 4) for g in GENES})

    @classmethod
    def neutral(cls) -> "Genome":
        return cls(**{g: 0.5 for g in GENES})

    @classmethod
    def inherit(
        cls,
        a: "Genome",
        b: "Genome",
        rng: Random,
        mutation_sigma: float = 0.05,
        blend_chance: float = 0.3,
    ) -> "Genome":
        """A child's genes: per gene, take one parent's value (or their average), then mutate."""
        child = {}
        for gene in GENES:
            va, vb = getattr(a, gene), getattr(b, gene)
            if rng.random() < blend_chance:
                value = (va + vb) / 2
            else:
                value = va if rng.random() < 0.5 else vb
            value += rng.gauss(0, mutation_sigma)
            child[gene] = round(min(1.0, max(0.0, value)), 4)
        return cls(**child)

    @classmethod
    def from_dict(cls, data: dict) -> "Genome":
        return cls(**{g: data[g] for g in GENES}, **{g: data.get(g, 0.5) for g in RHYTHM_GENES})

    def to_dict(self) -> dict:
        return {g: getattr(self, g) for g in ALL_GENES}

    def with_genes(self, **changes) -> "Genome":
        return Genome(**{**self.to_dict(), **changes})

    def with_rhythm(self, rng: Random, parents: tuple["Genome", ...] = ()) -> "Genome":
        """Rhythm genes: random for founders; inherited (one parent's or the average, plus a
        small mutation) for children."""
        values = {}
        for gene in RHYTHM_GENES:
            if len(parents) == 2:
                a, b = (getattr(p, gene) for p in parents)
                value = (a + b) / 2 if rng.random() < 0.3 else (a if rng.random() < 0.5 else b)
                value += rng.gauss(0, 0.05)
            else:
                value = rng.random()
            values[gene] = round(min(1.0, max(0.0, value)), 4)
        return self.with_genes(**values)

    # --------------------------------
    # Words
    # --------------------------------

    def traits(self) -> list[str]:
        """Character traits from extreme mind genes (neither good nor bad)."""
        out = []
        for gene in sorted(NEUTRAL_TRAITS, key=GENES.index):
            value = getattr(self, gene)
            if value >= HIGH:
                out.append(DESCRIPTORS[gene][0])
            elif value <= LOW:
                out.append(DESCRIPTORS[gene][1])
        return out

    def strengths(self) -> list[str]:
        return self._judged(strong=True)

    def weaknesses(self) -> list[str]:
        return self._judged(strong=False)

    def _judged(self, strong: bool) -> list[str]:
        out = []
        for gene in GENES:
            if gene in NEUTRAL_TRAITS or gene in APPEARANCE_GENES:
                continue
            value = getattr(self, gene)
            high_word, low_word = DESCRIPTORS[gene]
            if value >= HIGH and (gene in HIGH_IS_WEAK) != strong:
                out.append(high_word)
            elif value <= LOW and (gene in HIGH_IS_WEAK) == strong:
                out.append(low_word)
        return out

    def describe(self) -> dict:
        return {g: round(getattr(self, g), 2) for g in ALL_GENES}
