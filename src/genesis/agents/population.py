import random

from genesis.agents.agent import Agent
from genesis.agents.genome import Genome
from genesis.world.world import Position


class Population:
    """Registry of every agent that ever lived. Ids and names are unique."""

    def __init__(self, rng: random.Random, next_id: int = 1, seed: int = 0):
        self.rng = rng
        self.seed = seed  # for each agent's own rhythm source
        self.agents: list[Agent] = []
        self._next_id = next_id

    def create_agent(
        self,
        name: str,
        position: Position,
        born_tick: int = 0,
        genome: Genome | None = None,
        generation: int = 0,
        parents: tuple[int, ...] = (),
    ) -> Agent:
        if self.get_agent(name) is not None:
            raise ValueError(f"Agent name already used: {name}")

        genome = genome or Genome.random(self.rng)
        rhythm = random.Random(f"rhythm:{self.seed}:{self._next_id}")
        parent_genomes = tuple(
            p.genome for p in (self.get_by_id(i) for i in parents) if p is not None
        )
        agent = Agent(
            id=self._next_id,
            name=name,
            position=position,
            genome=genome.with_rhythm(rhythm, parent_genomes),
            born_tick=born_tick,
            generation=generation,
            parents=parents,
        )
        from genesis.agents import talents  # M8d: one inborn talent, from its own stream

        parent_agents = [p for p in (self.get_by_id(i) for i in parents) if p is not None]
        agent.talent = talents.born_with(self.seed, agent.id, parent_agents)
        self._next_id += 1
        self.agents.append(agent)
        return agent

    def add_existing(self, agent: Agent):
        """Used when loading a saved world."""
        self.agents.append(agent)
        self._next_id = max(self._next_id, agent.id + 1)

    def get_alive(self):
        return [agent for agent in self.agents if agent.alive]

    def get_agent(self, name: str):
        for agent in self.agents:
            if agent.name == name:
                return agent

        return None

    def get_by_id(self, agent_id: int):
        for agent in self.agents:
            if agent.id == agent_id:
                return agent

        return None

    def count(self):
        return len(self.agents)
