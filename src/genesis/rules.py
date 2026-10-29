"""Tuning numbers for one world (1 tick = 1 hour; needs run 0-100).

Defaults live here. A world's TOML file can override any of them in its [rules] table.
"""

from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class Rules:
    # Body: per-tick changes (for an average gene of 0.5)
    hunger_per_tick: float = 2
    thirst_per_tick: float = 3
    energy_per_tick: float = 3
    starving_damage: float = 2
    dehydration_damage: float = 4
    heal_per_tick: float = 1  # when hunger and thirst are both below heal_below
    heal_below: float = 60

    # How strongly genes change the body (0.25 = ±25% between gene 0 and gene 1)
    metabolism_effect: float = 0.25  # hunger and thirst rates
    endurance_effect: float = 0.25  # energy drain
    resilience_effect: float = 0.25  # healing up, damage down
    perception_vision_range: int = 1  # vision radius ± this many tiles

    # Actions
    food_per_meal: int = 10  # food units eaten per EAT
    hunger_per_food_unit: float = 5  # one full meal = -50 hunger
    thirst_per_drink: float = 60
    sleep_energy: float = 10
    rest_energy: float = 5

    # Decisions
    seek_threshold: float = 50  # start looking for water/food
    top_up_threshold: float = 25  # eat/drink if it's right here anyway
    urgent_threshold: float = 75  # beats everything; wakes a sleeping agent
    trip_safety_limit: float = 90  # don't start a trip that pushes the other need past this
    sleep_at_energy: float = 20
    wake_at_energy: float = 80
    rest_at_health: float = 30

    # Senses
    vision_radius: int = 3

    # Skills
    skill_learning_rate: float = 0.02  # growth per unit of practice for a novice
    foraging_bonus: float = 0.5  # an expert gets up to +50% nourishment per food unit
    water_finding_vision_at: float = 0.5  # +1 vision while searching for water from this level

    # Lifecycle (days; the prototype has 24 ticks per day)
    adult_age_days: int = 20
    lifespan_base_days: float = 60
    lifespan_longevity_days: float = 40  # lifespan = base + longevity gene × this
    elder_at_fraction: float = 0.7  # elder from 70% of lifespan
    max_age_fraction: float = 1.5  # nobody outlives 150% of their lifespan
    old_age_death_per_day: float = 0.02  # daily chance of death at exactly the lifespan...
    old_age_doubling_days: float = 5  # ...doubling every this many days after it
    founder_min_age_days: int = 20
    founder_max_age_days: int = 24
    child_forage_efficiency: float = 0.5
    elder_energy_factor: float = 1.3  # elders tire faster
    elder_vision_penalty: int = 1

    # Carrying (gathered when eating or drinking; can be given away)
    water_capacity: int = 2  # drinks a waterskin holds
    food_capacity_base: int = 10  # food units carried, plus...
    food_capacity_strength: int = 20  # ...strength gene × this
    gather_min_patch_meals: int = 3  # only pick to carry if the patch holds this many meals

    # Social (M3)
    ask_cooldown_ticks: int = 24  # hours: don't ask the same person the same thing more often
    ask_item_max_distance: int = 4  # in urgent need, walk this far to ask a carrier for help
    lie_chance_base: float = 0.2  # chance to lie = (1 - honesty)² × this...
    lie_chance_disliked: float = 0.8  # ...or × this when the listener is disliked
    threaten_min_aggression: float = 0.6
    gossip_effect: float = 0.15  # how much gossip moves the listener's affinity

    # Sleep on a schedule (M8a; millennium worlds). Off: agents sleep when tired (M0–M7).
    scheduled_sleep: int = 0  # 1: everyone sleeps at their own hours (sleep_need, chronotype)
    sleep_min_hours: float = 6  # sleep_need 0
    sleep_max_hours: float = 10  # sleep_need 1
    bedtime_earliest: float = 20  # chronotype 0 goes to bed at 20:00...
    bedtime_spread: float = 6  # ...chronotype 1 at 02:00
    reflect_every_days: int = 1  # nightly reflection (LLM) every this many days

    # Invention (M8b; docs/15). Off: nothing is gathered, tried, made or taught.
    inventions: int = 0
    problem_threshold: float = 5  # how much a problem must bother them before they tinker
    tinker_min_curiosity: float = 0.35
    tinker_cooldown_days: float = 1
    invent_base_chance: float = 0.25  # + 0.4 x crafting + 0.2 x intelligence, if it can work
    materials_capacity: int = 6
    learn_wait_days: float = 30  # after seeing someone with the fix, wait to be taught
    # Drives, goals and projects (M8c); 0 = off (story worlds)
    goals: int = 0
    goal_every_days: float = 30  # someone without a goal considers one this often
    goal_give_up_days: float = 720  # a goal not reached by then is given up
    goal_min_drive: float = 0.4  # a need must pull this hard to become a goal
    # Talents, healing, trade and money (M8d); 0 = off (story worlds)
    talents: int = 0
    trade: int = 0
    tend_below: float = 70  # health under which someone can be tended
    heal_base: float = 5
    heal_skill: float = 15  # extra health restored by a fully skilled healer
    trade_min_want: float = 0.9  # how much a good must be wanted to trade for it
    promise_days: float = 30  # what was taken on a promise must be brought back by then
    promise_trust: float = 0.3  # trust needed to give on a promise
    currency_window_days: float = 90  # a season of indirect exchanges
    currency_min_indirect: int = 8  # at least this many in the season
    currency_share: float = 0.5  # one good in at least this share of them
    currency_min_users: int = 3  # taken to pass on by at least this many people
    currency_after_days: float = 0  # M8f: no money before trading has gone on this long
    foraging_practice: float = 1.0  # M8f: how much each meal teaches (lower: talents show)
    forget_every_days: float = 1  # M8.5: how often small old memories are folded away
    # Disasters, valleys and migration (M8e); 0 = off (story worlds)
    disasters: int = 0
    disaster_min_years: float = 15  # years between disasters, drawn between these
    disaster_max_years: float = 25
    disaster_horizon_years: float = 1100  # how far ahead the schedule is drawn
    fire_damage: float = 25  # caught in the woods when they burn
    cold_damage_per_day: float = 30  # sleeping alone and unsheltered in a harsh winter
    predator_chance: float = 0.08  # per night watch, for someone alone
    armed_drive_off: float = 0.7  # chance a weapon drives the beasts off
    beast_damage_min: float = 15
    beast_damage_max: float = 35
    sickness_spread: float = 0.15  # per tick, to each person within a tile
    sick_min_days: int = 10
    sick_max_days: int = 25
    sick_damage_per_day: float = 4  # and no natural healing while sick
    immune_days: float = 1800  # after recovering
    crowd_per_valley: int = 0  # more living than this in a valley pushes people out (0 = never)
    migrate_days: float = 30  # living this long in another valley is moving there
    migrate_min_risk: float = 0.5  # only the bolder set out for another valley
    explore_leash: int = 0  # 1: never explore further from known water/food than is safe

    # Seasons (M8a; only in worlds with time.seasons = true): berry regrowth factor
    regen_spring: float = 1.2
    regen_wet: float = 1.0
    regen_dry: float = 0.6
    regen_cold: float = 0.3

    # Memory (M5)
    forget_after_days: int = 10  # small memories older than this are folded into a summary
    memory_recency_per_day: float = 0.8867  # how much a memory's recency fades each day
    #   (0.995 per hour; long runs use a slower fade, e.g. 0.999)
    forget_max_importance: int = 3  # "small" = importance at or below this
    reflection_min_importance: int = 4  # a day with nothing this important needs no reflection
    reflection_feeling_step: float = 0.1  # most a night's reflection can move a feeling

    # Inheritance (used from M6)
    mutation_sigma: float = 0.05
    blend_chance: float = 0.3

    # Reproduction and families (M6). See docs/04-society.md (4.3).
    propose_min_affinity: float = 0.3  # the proposer must like the other...
    propose_min_trust: float = 0.3  # ...and trust them a little
    propose_cooldown_days: int = 3  # don't ask the same person again sooner
    mate_min_health: int = 50  # both parents
    child_food_cost: int = 6  # food units the pair gives up when a child is conceived
    elder_fertility_factor: float = 0.5  # elders can still have children, less often
    conceive_cooldown_days: int = 6  # after a conception (each parent): gestation + 1 day
    failed_conceive_cooldown_days: int = 1  # after an agreement that didn't conceive
    conceive_base_chance: float = 0.25  # chance = base + (1 - base) × average fertility
    gestation_days: int = 5
    max_population: int = 40  # living agents plus unborn children
    monogamy: int = 1  # 1: someone with a living partner only mates with that partner
    kin_rule: int = 1  # 1: parents, children and siblings can't mate
    newborn_need: float = 20  # hunger and thirst at birth
    care_distance: int = 6  # parents walk this far to feed a child in need
    child_follow_distance: int = 2  # in free time children stay this close to a parent,
    # and partners to each other
    grief_energy_loss: float = 25  # when a parent, child, sibling or partner dies

    # Groups and leaders (M7). See docs/04-society.md (4.4).
    found_min_ambition: float = 0.5  # to found a group (above-average ambition)...
    found_min_friends: int = 2  # ...with at least this many friends
    invite_min_affinity: float = 0.3  # to ask someone to join
    group_ask_cooldown_days: int = 3
    max_group_size: int = 12
    leave_grievance: float = 0.5  # members leave a leader they resent this much
    challenge_min_ambition: float = 0.7
    order_days: int = 2  # an order not carried out by then lapses
    home_radius: int = 5  # in free time, members drift back within this of home
    rejoin_days: int = 10  # someone who left a group can't be asked back sooner

    # Conflict (M7). See docs/04-society.md (4.5).
    fight_min_aggression: float = 0.2  # the gentlest never start fights
    fight_provocation: float = 1.0  # aggression + grudge (+0.3 if desperate) to fight
    fight_min_health: int = 40
    fight_luck: float = 0.25  # standard deviation of luck in a fight
    fight_damage_base: float = 15  # health lost by the loser...
    fight_damage_margin: float = 40  # ...plus this much more for a clear defeat

    @classmethod
    def from_dict(cls, overrides: dict) -> "Rules":
        types = {f.name: f.type for f in fields(cls)}
        unknown = set(overrides) - set(types)
        if unknown:
            raise ValueError(f"Unknown rule(s): {', '.join(sorted(unknown))}")

        for key, value in overrides.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Rule {key} must be a number, got {value!r}")
            if types[key] in (int, "int") and not isinstance(value, int):
                raise ValueError(f"Rule {key} must be a whole number, got {value!r}")

        return cls(**overrides)

    def to_dict(self) -> dict:
        return asdict(self)
