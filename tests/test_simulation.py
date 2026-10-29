import pytest

from genesis.agents.genome import Genome
from genesis.agents.population import Population
from genesis.simulation.engine import Simulation
from helpers import Position, rng


def test_same_seed_gives_identical_history():
    a, b = Simulation.create(seed=42), Simulation.create(seed=42)
    a.run(240)
    b.run(240)
    assert a.state() == b.state()


def test_different_seed_gives_different_history():
    a, b = Simulation.create(seed=1), Simulation.create(seed=2)
    a.run(48)
    b.run(48)
    assert a.state() != b.state()


def test_ids_and_names_are_unique():
    sim = Simulation.create(founders=5)
    ids = [a.id for a in sim.population.agents]
    names = [a.name for a in sim.population.agents]
    assert len(set(ids)) == len(ids)
    assert len(set(names)) == len(names)


def test_duplicate_name_rejected():
    p = Population(rng())
    p.create_agent("Same", Position(1, 1))
    with pytest.raises(ValueError):
        p.create_agent("Same", Position(2, 2))


def test_everyone_observes_positions_from_start_of_tick():
    """Two-phase tick: what an agent sees must not depend on who moved first."""
    sim = Simulation.create(seed=3, founders=4)
    before = {a.id: a.position for a in sim.population.agents}
    sim.tick()
    for agent in sim.population.agents:
        for seen in agent.last_observation.visible_agents:
            assert seen["position"] == before[seen["id"]]


def test_clock():
    sim = Simulation.create()
    assert (sim.day, sim.hour) == (1, 8)
    sim.run(16)
    assert (sim.day, sim.hour) == (2, 0)


@pytest.mark.parametrize("num_agents", [1, 3, 5])
def test_average_agents_survive_ten_days(num_agents):
    """M0 acceptance: with average genes, small populations survive 10 days."""
    for seed in range(10):
        sim = Simulation.create(seed=seed, founders=num_agents)
        for agent in sim.population.agents:
            agent.genome = Genome.neutral()
        sim.run(240)
        assert all(a.alive for a in sim.population.agents), f"seed {seed}"


def test_most_random_agents_survive_ten_days():
    """With random genes, some unlucky agents (e.g. very high metabolism) may die early."""
    alive = total = 0
    for seed in range(20):
        sim = Simulation.create(seed=seed, founders=3)
        sim.run(240)
        alive += len(sim.population.get_alive())
        total += sim.population.count()
    assert alive / total >= 0.9


def test_agents_meet_each_other():
    sim = Simulation.create(seed=42, founders=3)
    sim.run(240)
    assert all(a.memory.social_memory for a in sim.population.agents)
