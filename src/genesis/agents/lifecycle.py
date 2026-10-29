"""Ageing: child → adult → elder → death. See docs/03-agent-model.md (Lifecycle).

Ages are in ticks; `tpd` is the calendar's ticks per day (24 in story mode, 4 in watches).
"""

from genesis.agents.genome import Genome
from genesis.rules import Rules

TICKS_PER_DAY = 24

CHILD, ADULT, ELDER = "child", "adult", "elder"


def lifespan_ticks(genome: Genome, rules: Rules, tpd: int = TICKS_PER_DAY) -> int:
    """Natural lifespan: base + longevity × range (days)."""
    days = rules.lifespan_base_days + genome.longevity * rules.lifespan_longevity_days
    return round(days * tpd)


def adult_age_ticks(rules: Rules, tpd: int = TICKS_PER_DAY) -> int:
    return rules.adult_age_days * tpd


def life_stage(age: int, genome: Genome, rules: Rules, tpd: int = TICKS_PER_DAY) -> str:
    if age < adult_age_ticks(rules, tpd):
        return CHILD
    if age >= rules.elder_at_fraction * lifespan_ticks(genome, rules, tpd):
        return ELDER
    return ADULT


def old_age_death_chance(age: int, genome: Genome, rules: Rules, tpd: int = TICKS_PER_DAY) -> float:
    """Chance of dying of old age in one tick. Zero before the elder stage.

    At the natural lifespan the daily chance is `old_age_death_per_day`; it doubles every
    `old_age_doubling_days` after that (and halves for each step before it).
    """
    if life_stage(age, genome, rules, tpd) != ELDER:
        return 0.0

    lifespan = lifespan_ticks(genome, rules, tpd)
    if age >= rules.max_age_fraction * lifespan:
        return 1.0

    days_past = (age - lifespan) / tpd
    per_day = rules.old_age_death_per_day * 2 ** (days_past / rules.old_age_doubling_days)
    return min(1.0, per_day / tpd)
