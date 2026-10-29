import pytest

from genesis.agents.genome import ALL_GENES, GENES, Genome
from helpers import rng


def test_random_genes_are_in_range_and_complete():
    for seed in range(50):
        genome = Genome.random(rng(seed))
        values = genome.to_dict()
        assert set(values) == set(ALL_GENES) and len(GENES) == 18  # 15 + 3 appearance
        assert len(ALL_GENES) == 20  # + 2 rhythm (M8a)
        assert all(0.0 <= v <= 1.0 for v in values.values())


def test_same_seed_same_genome():
    assert Genome.random(rng(7)) == Genome.random(rng(7))
    assert Genome.random(rng(7)) != Genome.random(rng(8))


def test_out_of_range_gene_is_rejected():
    with pytest.raises(ValueError):
        Genome.neutral().with_genes(strength=1.5)


def test_inherited_genes_stay_in_range():
    """Property: across many random parents and seeds, children's genes stay within 0-1."""
    for seed in range(300):
        r = rng(seed)
        a, b = Genome.random(r), Genome.random(r)
        child = Genome.inherit(a, b, r, mutation_sigma=0.3)  # large mutations on purpose
        assert all(0.0 <= v <= 1.0 for v in child.to_dict().values())


def test_inherited_genes_come_from_the_parents():
    """Without mutation, each gene is one parent's value or their average."""
    a, b = Genome.random(rng(1)), Genome.random(rng(2))
    for seed in range(50):
        child = Genome.inherit(a, b, rng(seed), mutation_sigma=0.0)
        for g in GENES:
            va, vb, vc = getattr(a, g), getattr(b, g), getattr(child, g)
            assert vc in (va, vb) or vc == pytest.approx(round((va + vb) / 2, 4))


def test_mutation_is_small_on_average():
    a = b = Genome.neutral()
    diffs = []
    for seed in range(200):
        child = Genome.inherit(a, b, rng(seed), mutation_sigma=0.05)
        diffs += [abs(v - 0.5) for v in child.to_dict().values()]
    assert 0.02 < sum(diffs) / len(diffs) < 0.06  # mean |N(0, 0.05)| ≈ 0.04


def test_strengths_weaknesses_and_traits():
    g = Genome.neutral().with_genes(
        strength=0.9, endurance=0.1, metabolism=0.9, perception=0.1, curiosity=0.95
    )
    assert "physically powerful" in g.strengths()
    assert "tires quickly" in g.weaknesses()
    assert "always hungry" in g.weaknesses()  # high metabolism is a weakness
    assert "short-sighted" in g.weaknesses()
    assert "endlessly curious" in g.traits()
    assert "endlessly curious" not in g.strengths() + g.weaknesses()
    assert Genome.neutral().strengths() == Genome.neutral().weaknesses() == []

    efficient = Genome.neutral().with_genes(metabolism=0.1)
    assert "needs little food" in efficient.strengths()


def test_rhythm_genes_come_from_their_own_source_and_are_inherited():
    from genesis.simulation.engine import Simulation

    a = Simulation.create(seed=4, founders=6)
    b = Simulation.create(seed=4, founders=6)
    rhythms = [(x.genome.sleep_need, x.genome.chronotype) for x in a.population.agents]
    assert rhythms == [(x.genome.sleep_need, x.genome.chronotype) for x in b.population.agents]
    assert len(set(rhythms)) == 6  # everyone different
    old = Genome.from_dict({g: 0.5 for g in GENES})  # a world saved before M8a
    assert old.sleep_need == 0.5 and old.chronotype == 0.5
