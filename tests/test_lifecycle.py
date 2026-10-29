import random

from genesis.agents import lifecycle
from genesis.agents.abilities import Abilities, level_name
from genesis.agents.genome import Genome
from genesis.history import events as ev
from genesis.history.events import EventLog
from genesis.rules import Rules
from genesis.simulation.engine import Simulation
from helpers import World, make_agent, step

R = Rules()
DAY = 24


# ---------------------------------------------------------------- genes change the body


def hunger_after(genes, ticks=10):
    world = World()
    agent = make_agent(x=1, y=1, hunger=0, thirst=0, genes=genes)
    for t in range(ticks):
        agent.update_needs(world, t, EventLog(), random.Random(0))
    return agent.hunger, agent.thirst


def test_high_metabolism_gets_hungry_and_thirsty_faster():
    """M2 check: all else equal, high metabolism means faster hunger."""
    fast_h, fast_t = hunger_after({"metabolism": 1.0})
    avg_h, avg_t = hunger_after({})
    slow_h, slow_t = hunger_after({"metabolism": 0.0})
    assert slow_h < avg_h < fast_h
    assert slow_t < avg_t < fast_t
    assert avg_h == R.hunger_per_tick * 10  # gene 0.5 = the rule's base rate


def test_perception_sets_vision_radius():
    world = World()
    assert make_agent(genes={"perception": 0.0}).vision_radius(world) == 2
    assert make_agent(genes={"perception": 0.5}).vision_radius(world) == 3
    assert make_agent(genes={"perception": 1.0}).vision_radius(world) == 4


def test_endurance_slows_energy_drain():
    world = World()
    tough = make_agent(genes={"endurance": 1.0})
    weak = make_agent(genes={"endurance": 0.0})
    for a in (tough, weak):
        a.update_needs(world, 0, EventLog(), random.Random(0))
    assert tough.energy > weak.energy


# ---------------------------------------------------------------- life stages and death


def test_life_stages_follow_age():
    g = Genome.neutral()  # lifespan 80 days, elder from 56
    assert lifecycle.life_stage(5 * DAY, g, R) == lifecycle.CHILD
    assert lifecycle.life_stage(30 * DAY, g, R) == lifecycle.ADULT
    assert lifecycle.life_stage(60 * DAY, g, R) == lifecycle.ELDER


def test_longevity_sets_lifespan():
    short = Genome.neutral().with_genes(longevity=0.0)
    long = Genome.neutral().with_genes(longevity=1.0)
    assert lifecycle.lifespan_ticks(short, R) == 60 * DAY
    assert lifecycle.lifespan_ticks(long, R) == 100 * DAY


def test_old_age_death_chance_rises_with_age():
    g = Genome.neutral()
    chances = [lifecycle.old_age_death_chance(d * DAY, g, R) for d in (30, 60, 80, 90, 100)]
    assert chances[0] == 0.0  # adults don't die of old age
    assert chances[1] < chances[2] < chances[3] < chances[4]
    assert lifecycle.old_age_death_chance(121 * DAY, g, R) == 1.0  # beyond 150% of lifespan


def test_agent_becomes_elder_and_dies_of_old_age_with_cause():
    world = World()
    agent = make_agent(x=11, y=15, age=55 * DAY, hunger=0, thirst=0)  # by the lake
    log = None
    for t in range(80 * DAY):
        agent.hunger = agent.thirst = 0  # keep needs out of the picture
        log = step(agent, world, tick=t, log=log)
        if not agent.alive:
            break

    assert agent.cause_of_death == "old age"
    by_id = {e.id: e for e in log.recent}
    died = next(e for e in log.recent if e.type == ev.DIED)
    stage = by_id[died.cause_id]
    assert stage.type == ev.LIFE_STAGE and stage.data["stage"] == "elder"


def test_founders_are_adults_of_varied_ages():
    sim = Simulation.create(seed=3, founders=8)
    ages = [a.age(0) // DAY for a in sim.population.agents]
    assert all(R.founder_min_age_days <= age <= R.founder_max_age_days for age in ages)
    assert len(set(ages)) > 1


def test_founders_all_die_of_old_age_within_150_days():
    """M2 demo: a small world runs until everyone has died of old age (or need)."""
    sim = Simulation.create(seed=1, founders=3)
    sim.run(150 * DAY)
    founders = [a for a in sim.population.agents if a.generation == 0]
    assert not any(a.alive for a in founders)  # their children may live on (M6)
    assert sum(a.cause_of_death == "old age" for a in founders) >= 2


# ---------------------------------------------------------------- skills


def test_skills_grow_with_practice_and_slow_near_mastery():
    a = Abilities()
    gains = []
    for _ in range(100):
        before = a.get("foraging")
        a.practice("foraging", 1, intelligence=0.5, rate=0.02)
        gains.append(a.get("foraging") - before)
    assert gains[0] > gains[-1] > 0
    assert 0.5 < a.get("foraging") < 1.0


def test_intelligence_speeds_learning():
    quick, slow = Abilities(), Abilities()
    for _ in range(20):
        quick.practice("foraging", 1, intelligence=1.0, rate=0.02)
        slow.practice("foraging", 1, intelligence=0.0, rate=0.02)
    assert quick.get("foraging") > 2 * slow.get("foraging")


def test_reaching_a_level_is_reported_once():
    a = Abilities()
    reached = [a.practice("foraging", 1, 0.5, 0.02) for _ in range(200)]
    assert [x for x in reached if x] == ["capable", "skilled", "expert"]
    assert level_name(0.0) == "untrained" and level_name(0.6) == "skilled"


def test_eating_trains_foraging_and_skill_makes_food_go_further():
    world = World()
    food = world.food_sources[0]
    novice = make_agent(x=food.position.x, y=food.position.y, hunger=80, thirst=0)
    expert = make_agent(x=food.position.x, y=food.position.y, hunger=80, thirst=0, agent_id=2)
    expert.abilities.values["foraging"] = 1.0

    for a in (novice, expert):
        food.quantity = 100
        a._eat(world, 0, EventLog())
    assert expert.hunger < novice.hunger
    assert novice.abilities.get("foraging") > 0


def test_daily_metrics_are_recorded():
    sim = Simulation.create(seed=2)
    sim.run(3 * DAY)
    names = {name for _, name, _ in sim.pending_metrics}
    assert {
        "population.alive",
        "deaths.old age",
        "gene.metabolism.mean",
        "skill.foraging.mean",
        "age.mean_days",
    } <= names
    ticks = sorted({t for t, _, _ in sim.pending_metrics})
    assert [sim.config.start_hour + t for t in ticks] == [24, 48, 72][: len(ticks)]
