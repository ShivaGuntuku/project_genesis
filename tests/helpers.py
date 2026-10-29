import random

from genesis.agents.agent import Agent
from genesis.agents.genome import Genome
from genesis.config import WorldConfig
from genesis.history.events import EventLog
from genesis.simulation.context import TickContext
from genesis.world.world import Position
from genesis.world.world import World as _World

NEUTRAL = Genome.neutral()
ADULT_AGE = 25 * 24  # ticks


def World(**overrides):
    """The default map (optionally with config overrides)."""
    config = WorldConfig.default()
    if overrides:
        config = config.with_overrides(**overrides)
    return _World(config)


def harsh_sim(seed=3, founders=10, regen=1):
    """A valley with slow-growing berries, where some starve: for tests about hardship."""
    from dataclasses import replace

    from genesis.simulation.engine import Simulation

    config = WorldConfig.default().with_overrides(seed=seed, founders=founders)
    config = config.with_overrides(
        food=tuple(replace(f, regen_per_tick=regen) for f in config.food)
    )
    return Simulation(config)


def harsh_toml(regen=1) -> str:
    """The default world's TOML text with slow-growing berries."""
    return WorldConfig.default().source_toml.replace(
        "regen_per_tick = 3", f"regen_per_tick = {regen}"
    )


def make_agent(x=10, y=10, agent_id=1, name="Test", genes=None, age=ADULT_AGE, **needs):
    """An agent with average genes (override some with genes={...}), aged `age` ticks at tick 0."""
    genome = NEUTRAL.with_genes(**genes) if genes else NEUTRAL
    agent = Agent(id=agent_id, name=name, position=Position(x, y), genome=genome, born_tick=-age)
    for key, value in needs.items():
        setattr(agent, key, value)
    return agent


def make_ctx(world, agents, tick=0, log=None, seed=0):
    return TickContext(
        world=world,
        tick=tick,
        log=log or EventLog(),
        rng=random.Random(seed * 100_003 + tick),
        agents={a.id: a for a in agents},
    )


def step(agent, world, agents=None, tick=0, log=None, seed=0):
    """Run one full tick for these agents (same phase order as Simulation)."""
    agents = agents or [agent]
    ctx = make_ctx(world, agents, tick, log, seed)
    world.update()
    for a in agents:
        a.update_needs(world, tick, ctx.log, ctx.rng)
    for a in agents:
        a.perceive(ctx)
    for a in agents:
        a.decide(world)
    for a in agents:
        a.hours_left, a.talked = world.calendar.hours_per_tick, False  # as the engine does
        a.act(ctx)
    return ctx.log


def perceive(agent, world, agents=None, tick=0):
    agents = agents or [agent]
    agent.perceive(make_ctx(world, agents, tick))


def rng(seed=0):
    return random.Random(seed)


__all__ = [
    "harsh_sim",
    "harsh_toml",
    "make_agent",
    "make_ctx",
    "step",
    "perceive",
    "rng",
    "World",
    "Position",
    "NEUTRAL",
]
