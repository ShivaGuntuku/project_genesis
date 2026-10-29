"""M6: reproduction and families."""

from fakes import FakeModel, llm_sim
from genesis.agents import lifecycle
from genesis.agents.genome import GENES
from genesis.api import views
from genesis.history import events as ev
from genesis.llm.mind import LLMMind, ReplayMind
from genesis.llm.settings import LLMConfig
from genesis.mind.decision import ActionType
from genesis.persistence.db import WorldStore
from genesis.simulation.engine import Simulation
from genesis.society import family, interaction
from helpers import World, make_agent, make_ctx

DAY = 24


def couple(world=None, food=(4, 4), **genes):
    """Two adults who like and trust each other, side by side."""
    world = world or World()
    a = make_agent(agent_id=1, name="Mira", genes={"fertility": 1.0, **genes})
    b = make_agent(x=11, agent_id=2, name="Kael", genes={"fertility": 1.0, **genes})
    for x, y in ((a, b), (b, a)):
        rel = x.relationship(y.id, y.name)
        rel.affinity, rel.trust = 0.8, 0.8
    a.inventory["food"], b.inventory["food"] = food
    ctx = make_ctx(world, [a, b])
    return world, a, b, ctx


def long_run(seed=3, days=120) -> Simulation:
    sim = Simulation.create(seed=seed, founders=6)
    sim.run(days * DAY)
    return sim


# =================================================================
# The protocol
# =================================================================


def test_a_child_is_conceived_only_after_both_agree_and_costs_food():
    world, a, b, ctx = couple()
    ctx.rng.seed(1)
    assert family.may_propose(ctx, a, b)

    answer = None
    for tick in range(0, 20 * DAY, DAY):  # ask until the answer is yes and a child comes
        ctx.tick = tick
        a.asked.clear()
        answer = family.propose(ctx, a, b)
        if ctx.pregnancies:
            break
    assert ctx.pregnancies, "a fertile, fond couple should conceive"

    by_id = {e.id: e for e in ctx.log.recent}
    conceived = next(e for e in ctx.log.recent if e.type == ev.CONCEIVED)
    agreement = by_id[conceived.cause_id]
    assert agreement.id == answer and agreement.data["payload"]["answer"] == "agree"
    assert by_id[agreement.data["reply_to"]].data["act"] == "propose"

    assert a.inventory["food"] + b.inventory["food"] == 8 - world.rules.child_food_cost
    assert a.partner == b.id and b.partner == a.id and b.pregnant
    assert a.next_mate_tick > ctx.tick and family.why_not(ctx, a, b) is not None


def test_no_agreement_no_child():
    world, a, b, ctx = couple()
    b.relationship(a.id, a.name).affinity = -0.9  # b can't stand a
    b.relationship(a.id, a.name).trust = 0.0
    for tick in range(0, 10 * DAY, DAY):
        ctx.tick = tick
        a.asked.clear()
        family.propose(ctx, a, b)
    answers = [e.data["payload"].get("answer") for e in ctx.log.recent if e.type == ev.MESSAGE]
    assert "decline" in answers and not ctx.pregnancies


def test_who_may_not_have_children():
    world, a, b, ctx = couple()
    assert family.why_not(ctx, a, b) is None

    a.inventory["food"] = b.inventory["food"] = 1
    assert "food" in family.why_not(ctx, a, b)
    a.inventory["food"] = 8

    child = make_agent(x=12, agent_id=3, name="Teo", age=5 * DAY)
    child.stage = lifecycle.CHILD
    ctx.agents[3] = child
    assert "child" in family.why_not(ctx, a, child)

    sibling = make_agent(x=12, agent_id=4, name="Ione")
    a.parents = sibling.parents = (90, 91)
    ctx.agents[4] = sibling
    assert "kin" in family.why_not(ctx, a, sibling)
    a.parents = ()

    other = make_agent(x=12, agent_id=5, name="Dax")
    ctx.agents[5] = other
    a.partner = b.id
    assert "partner" in family.why_not(ctx, a, other)  # monogamy
    a.partner = None

    b.thirst = 90
    assert "parched" in family.why_not(ctx, a, b)
    b.thirst = 0

    full = world.rules.max_population - 2
    ctx.pregnancies.extend({"carrier": 0} for _ in range(full))
    assert "full" in family.why_not(ctx, a, b)


def test_birth_after_gestation_with_inherited_genes_and_family_bonds():
    sim = Simulation.create(seed=4, founders=2)
    a, b = sim.population.agents
    for x, y in ((a, b), (b, a)):
        rel = x.relationship(y.id, y.name)
        rel.affinity, rel.trust = 0.8, 0.8
    a.inventory["food"] = b.inventory["food"] = 6
    a.genome = a.genome.with_genes(fertility=1.0)
    b.genome = b.genome.with_genes(fertility=1.0)
    b.memory.remember_location("water", sim.world.food_sources[0].position, seen=True)

    ctx = _ctx(sim)
    agreement = interaction.say(ctx, b, a, interaction.Act.REPLY, {"answer": "agree"})
    sim.rng.seed(0)
    assert family.agree(ctx, a, b, agreement) is not None
    due = sim.pregnancies[0]["due"]
    assert due == sim.tick_count + sim.world.rules.gestation_days * DAY

    while sim.tick_count <= due:
        sim.tick()
    child = sim.population.agents[-1]
    assert child.parents == (a.id, b.id) and child.generation == 1
    assert child.stage == lifecycle.CHILD and child.born_tick == due
    assert child.id in a.children and child.id in b.children and not b.pregnant
    assert child.relationships[a.id].affinity >= 0.7 and a.relationships[child.id].trust >= 0.7
    assert child.memory.get_known_locations("water")  # taught by the parent who bore them

    birth = next(e for e in sim.log.recent if e.type == ev.BIRTH)
    conceived = next(e for e in sim.log.recent if e.id == birth.cause_id)
    assert conceived.type == ev.CONCEIVED and conceived.cause_id == agreement
    assert any(m.importance == 10 for m in a.stream.items if child.id in m.about)


def _ctx(sim):
    from genesis.simulation.context import TickContext

    return TickContext(
        world=sim.world,
        tick=sim.tick_count,
        log=sim.log,
        rng=sim.rng,
        agents={x.id: x for x in sim.population.agents},
        pregnancies=sim.pregnancies,
    )


# =================================================================
# Over generations (docs/08 M6 checks)
# =================================================================


def test_generations_follow_the_rules():
    """Births only after agreement; never close kin; genes within mutation range."""
    sim = long_run()
    by_id = {a.id: a for a in sim.population.agents}
    events = {e.id: e for e in sim.log.pending}  # everything since creation (never saved)
    children = [a for a in sim.population.agents if a.parents]
    assert len(children) >= 5 and max(a.generation for a in children) >= 2  # 1-3 by seed

    births = [e for e in events.values() if e.type == ev.BIRTH]
    assert len(births) == len(children)
    for birth in births:
        conceived = events[birth.cause_id]
        agreement = events[conceived.cause_id]
        proposal = events[agreement.data["reply_to"]]
        assert conceived.type == ev.CONCEIVED
        assert agreement.data["payload"]["answer"] == "agree"
        assert proposal.data["act"] == "propose"
        assert set(conceived.actors) == set(birth.targets)

    sigma = sim.world.rules.mutation_sigma
    for child in children:
        pa, pb = (by_id[p] for p in child.parents)
        assert not family.close_kin(pa, pb)
        for gene in GENES:
            low = min(getattr(pa.genome, gene), getattr(pb.genome, gene)) - 5 * sigma
            high = max(getattr(pa.genome, gene), getattr(pb.genome, gene)) + 5 * sigma
            assert max(0.0, low) <= getattr(child.genome, gene) <= min(1.0, high), gene
        assert child.generation == max(pa.generation, pb.generation) + 1

    names = [a.name for a in sim.population.agents]
    assert len(names) == len(set(names))


def test_families_save_and_resume_identically(tmp_path):
    reference = long_run(seed=3, days=60)

    sim = Simulation.create(seed=3, founders=6)
    store = WorldStore.create(tmp_path / "w.sqlite", sim)
    for _ in range(30 * DAY):
        sim.tick()
        store.save(sim)
    store.close()
    store = WorldStore(tmp_path / "w.sqlite")
    resumed = store.load()
    store.close()
    resumed.run(30 * DAY)
    assert resumed.state() == reference.state()
    assert any(a.parents for a in resumed.population.agents)


# =================================================================
# Care and grief
# =================================================================


def test_parents_bring_water_to_a_thirsty_child():
    world = World()
    parent = make_agent(agent_id=1, name="Mira")
    child = make_agent(x=13, agent_id=2, name="Teo", age=3 * DAY, thirst=60)
    child.stage = lifecycle.CHILD
    child.parents, parent.children = (1, 9), [2]
    parent.inventory["water"] = 2
    from helpers import step

    log = step(parent, world, [parent, child])
    assert parent.current_action == ActionType.CARE
    for tick in range(1, 6):
        step(parent, world, [parent, child], tick=tick, log=log)
        if child.inventory["water"] or child.thirst < 50:
            break
    gifts = [e for e in log.recent if e.type == ev.MESSAGE and e.data["act"] == "give"]
    assert gifts and gifts[0].actors == (1,) and gifts[0].targets == (2,)


def test_children_keep_close_to_a_parent_and_partners_to_each_other():
    world = World()
    parent = make_agent(agent_id=1, name="Mira")
    child = make_agent(x=13, agent_id=2, name="Teo", age=3 * DAY)  # 3 tiles: in sight
    child.stage = lifecycle.CHILD
    child.parents = (1, 9)
    from helpers import step

    step(child, world, [parent, child])
    assert child.current_action == ActionType.FOLLOW and child.position.x == 12

    mira = make_agent(agent_id=1, name="Mira", energy=10)  # resting: stays put
    kael = make_agent(x=13, agent_id=3, name="Kael")
    kael.partner, mira.partner = 1, 3
    step(kael, World(), [mira, kael], tick=1)
    assert kael.current_action == ActionType.FOLLOW and kael.position.x == 12


def test_a_death_in_the_family_brings_grief_and_widows_the_partner():
    world, a, b, ctx = couple()
    a.partner, b.partner = b.id, a.id
    child = make_agent(x=12, agent_id=3, name="Teo")
    child.parents, a.children, b.children = (1, 2), [3], [3]
    ctx.agents[3] = child
    energy = a.energy

    died = ctx.log.emit(0, ev.DIED, actors=(b.id,), data={"cause": "thirst"})
    b.alive = False
    family.grieve(ctx, next(e for e in ctx.log.recent if e.id == died))

    grief = {e.actors[0]: e.data for e in ctx.log.recent if e.type == ev.GRIEVED}
    assert grief[a.id]["relation"] == "partner" and grief[a.id]["widowed"]
    assert grief[child.id]["relation"] == "parent"
    assert a.partner is None and a.energy == energy - world.rules.grief_energy_loss


# =================================================================
# The LLM decides in persona
# =================================================================


def test_the_llm_accepts_or_declines_and_names_the_child():
    world, a, b, ctx = couple()
    ctx.mind = LLMMind(LLMConfig(enabled=True), FakeModel(prefer=("agree",)))
    ctx.rng.seed(3)
    family.propose(ctx, a, b)
    mate = [c for c in ctx.llm_calls if c["kind"] == "mate"]
    assert mate and mate[0]["choice"] == "agree" and mate[0]["agent_id"] == b.id

    world, a, b, ctx = couple()
    ctx.mind = LLMMind(LLMConfig(enabled=True), FakeModel(prefer=("decline",)))
    family.propose(ctx, a, b)
    assert not ctx.pregnancies and a.partner is None


def test_names_are_new_single_words():
    sim = llm_sim(seed=4, founders=2, model=FakeModel(child_name="Ashwen"))
    a, b = sim.population.agents
    ctx = _ctx(sim)
    ctx.mind = sim.mind
    assert family.choose_name(ctx, sim, a, b) == "Ashwen"

    taken = llm_sim(seed=4, founders=2, model=FakeModel(child_name=a.name))  # already used
    ctx = _ctx(taken)
    ctx.mind = taken.mind
    name = family.choose_name(ctx, taken, *taken.population.agents)
    assert name.lower() not in {x.name.lower() for x in taken.population.agents}
    assert any(c["outcome"] == "invalid" and c["kind"] == "name" for c in ctx.llm_calls)


def test_replay_reproduces_families():
    sim = llm_sim(seed=19, founders=6)
    sim.run(45 * DAY)
    assert any(a.parents for a in sim.population.agents), "expected a birth"
    records = {(c["tick"], c["point"]): dict(c) for c in sim.pending_llm_calls}

    replay = Simulation(sim.config)
    replay.mind = ReplayMind(records, sim.config.llm)
    replay.run(45 * DAY)
    assert replay.state() == sim.state()
    assert [a.name for a in replay.population.agents] == [a.name for a in sim.population.agents]


def test_viewer_gets_newborns_and_family_links():
    sim = long_run(days=40)
    names = {a.id: a.name for a in sim.population.agents}
    births = [e for e in sim.log.pending if e.type == ev.BIRTH]
    frame = views.frame(sim, births[-1:], names)
    assert frame["newAgents"] and frame["newAgents"][0]["id"] == births[-1].actors[0]
    labels = {label for *_, label in frame["links"]}
    assert "family" in labels or "partner" in labels
