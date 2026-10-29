"""M5: memory streams, retrieval, reflection and forgetting."""

import time

import numpy as np

from fakes import FakeModel, llm_sim
from genesis.history import events as ev
from genesis.llm import reflection
from genesis.llm.embeddings import Embedder
from genesis.llm.mind import PromptCollector, ReplayMind
from genesis.mind import stream as memory
from genesis.mind.stream import Memory, MemoryStream
from genesis.persistence.db import WorldStore
from genesis.simulation.engine import Simulation
from genesis.society import interaction
from helpers import World, harsh_sim, make_agent, make_ctx

DAY = 24


def stream_of(*specs) -> MemoryStream:
    """specs: (id, tick, importance, text, about)"""
    s = MemoryStream()
    for i, tick, importance, text, about in specs:
        s.add(Memory(i, tick, memory.CONVERSATION, text, importance, about))
    return s


# =================================================================
# Forming memories
# =================================================================


def test_memories_are_formed_from_each_ones_point_of_view():
    world = World()
    asker = make_agent(agent_id=1, name="Teo", hunger=70)
    liar = make_agent(x=11, agent_id=2, name="Rhea", genes={"honesty": 0.0})
    liar.memory.remember_location("food", world.food_sources[0].position)
    ctx = make_ctx(world, [asker, liar])
    interaction.share_info(ctx, liar, asker, "food", lying=True)
    lie = next(e for e in ctx.log.recent if e.type == ev.MESSAGE)

    names = {1: "Teo", 2: "Rhea"}
    formed = dict(memory.memories_from_event(lie, ctx.agents, names))
    assert formed[2]["text"].endswith("(I lied)")  # the liar knows
    assert "lied" not in formed[1]["text"] and "LIE" not in formed[1]["text"]
    assert formed[1]["kind"] == memory.TOLD and formed[1]["source_agent_id"] == 2
    assert formed[1]["about"] == (2,) and formed[2]["about"] == (1,)


def test_importance_follows_what_matters():
    world = World()
    bully = make_agent(agent_id=1, name="Dax", thirst=80, genes={"aggression": 1.0})
    victim = make_agent(x=11, agent_id=2, name="Ione")
    ctx = make_ctx(world, [bully, victim])
    names = {1: "Dax", 2: "Ione"}

    interaction.say(ctx, bully, victim, interaction.Act.GREET)  # routine, no words
    greet = ctx.log.recent[-1]
    assert memory.memories_from_event(greet, ctx.agents, names) == []

    interaction.threaten(ctx, bully, victim, "water")
    threat = next(e for e in ctx.log.recent if e.data.get("act") == "threaten")
    formed = dict(memory.memories_from_event(threat, ctx.agents, names))
    assert formed[2]["importance"] == 8 and formed[1]["importance"] == 5


def test_everyone_who_knew_the_dead_remembers_the_death():
    sim = harsh_sim(seed=3, founders=8)
    for _ in range(60 * DAY):
        sim.tick()
        dead = [a for a in sim.population.agents if not a.alive]
        if dead:
            break
    assert dead, "someone should die within 60 days with this seed"
    gone = dead[0]
    mourners = [
        a
        for a in sim.population.get_alive()
        if gone.id in a.relationships and a.relationships[gone.id].met
    ]
    for a in mourners:
        assert any(gone.id in m.about and "died" in m.text for m in a.stream.items)


# =================================================================
# Retrieval
# =================================================================


def test_retrieval_balances_recency_importance_relevance_and_the_person_faced():
    now = 30 * DAY
    s = stream_of(
        (1, now - 1, 2, "Kael chatted about the weather", (3,)),  # recent but trivial
        (2, 10 * DAY, 8, "Dax stole my water at the lake", (4,)),  # old, important, about Dax
        (3, 25 * DAY, 4, "Mira told me the berries are ripe", (5,)),
    )
    facing_dax = memory.retrieve(s, now, "Dax asks you for water", about_id=4, k=1)
    assert [m.id for m in facing_dax] == [2]  # 20 days old, but it comes to mind

    berries = memory.retrieve(s, now, "where are the ripe berries", k=1)
    assert [m.id for m in berries] == [3]
    assert [m.id for m in memory.retrieve(s, now, "hello", k=3)] == [2, 3, 1]  # oldest first


def test_an_agent_only_ever_recalls_its_own_memories():
    sim = llm_sim(seed=4, founders=6)
    sim.run(4 * DAY)
    owners: dict[int, int] = {}
    for a in sim.population.agents:
        for m in a.stream.items:
            assert owners.setdefault(m.id, a.id) == a.id  # no memory is shared
    for a in sim.population.get_alive():
        own = {m.id for m in a.stream.items}
        for other in sim.population.get_alive():
            recalled = sim.mind.recall(a, _ctx(sim), "water food lake", other.id)
            assert {m.id for m in recalled} <= own


def _ctx(sim):
    from genesis.simulation.context import TickContext

    return TickContext(world=sim.world, tick=sim.tick_count, log=sim.log, rng=sim.rng)


def test_retrieval_is_fast_at_ten_thousand_memories():
    """docs/08 M5 check: under 50 ms with 10k memories and 768-dim vectors."""
    rng = np.random.default_rng(0)
    s = MemoryStream()
    vectors = {}
    for i in range(10_000):
        s.add(Memory(i, i, memory.CONVERSATION, f"memory {i}", int(rng.integers(1, 10)), (i % 7,)))
        v = rng.normal(size=768).astype(np.float32)
        vectors[i] = v / np.linalg.norm(v)

    class Holder:
        memory_vectors = vectors
        pending_vectors: list = []

    class QueryOnly:  # every memory is already embedded; only the query is
        def embed(self, model, texts):
            return [rng.normal(size=768).tolist() for _ in texts]

        def available(self):
            return True

    embedder = Embedder(QueryOnly(), "nomic-embed-text", Holder())
    memory.retrieve(s, 10_000, "warm up", 3, relevance=embedder.relevance)
    started = time.perf_counter()
    for _ in range(10):
        top = memory.retrieve(s, 10_000, "who took my water", 3, relevance=embedder.relevance)
    per_call = (time.perf_counter() - started) / 10
    assert len(top) == 6 and per_call < 0.05, f"{per_call * 1000:.1f} ms"


def test_embeddings_are_used_and_cached_when_the_model_is_there():
    model = FakeModel()
    sim = llm_sim(seed=4, founders=5, model=model)
    sim.run(2 * DAY)
    assert sim.mind.embedder is not None and sim.memory_vectors
    assert model.embedded > 0 and set(sim.pending_vectors) <= set(sim.memory_vectors)
    first = model.embedded
    sim.mind.recall(sim.population.agents[0], _ctx(sim), "water")
    assert model.embedded - first <= 1 + len(sim.population.agents[0].stream)  # cached


def test_without_an_embedding_model_shared_words_still_work():
    sim = llm_sim(seed=4, founders=5, model=FakeModel(can_embed=False))
    sim.run(2 * DAY)
    assert not sim.memory_vectors  # nothing embedded, nothing broke
    assert any("What comes to mind" in c["prompt"] for c in sim.pending_llm_calls)


# =================================================================
# Reflection
# =================================================================


def test_nightly_reflection_adds_beliefs_story_and_small_feelings():
    model = FakeModel(feel="colder")
    sim = llm_sim(seed=4, founders=5, model=model)
    sim.run(2 * DAY)

    reflections = [c for c in sim.pending_llm_calls if c["kind"] == "reflect"]
    assert reflections and all(c["outcome"] == "ok" for c in reflections)
    start = sim.config.start_hour
    assert all((start + c["tick"] + 1) % DAY == 0 for c in reflections)  # at midnight
    assert all("Today you remember:" in c["prompt"] for c in reflections)

    thinker = next(a for a in sim.population.agents if a.id == reflections[0]["agent_id"])
    assert thinker.story.startswith("I live in the valley")
    beliefs = thinker.stream.beliefs()
    assert beliefs and all(b.importance == 7 for b in beliefs)
    event = next(e for e in sim.log.recent if e.type == ev.REFLECTED)
    assert beliefs[0].source_event_id is not None

    # Feelings move at most one step per night.
    step = sim.world.rules.reflection_feeling_step
    changes = [
        h for h in sim.pending_relationship_history if h[6] == event.id and h[3] == "affinity"
    ]
    assert changes and all(abs(new - old) <= step + 1e-9 for *_, old, new, _ in changes)


def test_reflection_answers_are_checked():
    names = ["Kael"]
    good = reflection.parse(
        '{"thought": "t", "beliefs": ["A", "a", "B", "C"], "story": "s", '
        '"feelings": [{"name": "Kael", "change": "warmer"}, {"name": "Zed", "change": "colder"},'
        '{"name": "Kael", "change": "colder"}]}',
        names,
        None,
    )
    assert good.beliefs == ["A", "B"]  # duplicates dropped, at most 2
    assert good.feelings == [("Kael", "warmer")]  # unknown people and repeats ignored
    assert reflection.parse("nonsense", names, None) is None
    assert reflection.parse('{"beliefs": "not a list"}', names, None) is None
    assert reflection.parse('{"beliefs": [], "story": "", "feelings": []}', names, None) is None
    schema = reflection.schema(names)
    assert schema["properties"]["feelings"]["items"]["properties"]["name"]["enum"] == names


def test_beliefs_and_story_reach_later_prompts():
    sim = llm_sim(seed=4, founders=5)
    sim.run(3 * DAY)
    day3 = [c for c in sim.pending_llm_calls if c["tick"] >= 2 * DAY and c["kind"] != "reflect"]
    assert any("What you believe:" in c["prompt"] for c in day3)
    assert any("In your own words: I live in the valley" in c["prompt"] for c in day3)


def test_no_reflection_without_a_mind_and_no_randomness_used():
    rules = Simulation.create(seed=5, founders=5)
    rules.run(4 * DAY)
    silent = Simulation.create(seed=5, founders=5)
    silent.mind = PromptCollector()
    silent.run(4 * DAY)
    assert silent.state() == rules.state()  # memories identical too
    assert not any(e.type == ev.REFLECTED for e in silent.log.recent)


# =================================================================
# Forgetting and persistence
# =================================================================


def test_small_old_memories_fold_into_daily_summaries():
    rules = World().rules
    now = 20 * DAY
    s = stream_of(
        (1, 2 * DAY, 2, "Kael chatted with Mira", (2,)),
        (2, 2 * DAY + 3, 3, "Kael asked Mira where to find food", (2,)),
        (3, 2 * DAY + 5, 8, "Dax threatened Kael, demanding water", (4,)),
        (4, 15 * DAY, 2, "Kael chatted with Oren", (5,)),  # small but recent
    )
    s.add(Memory(5, 1 * DAY, memory.BELIEF, "Dax is dangerous", 7))
    forgotten, summaries = memory.forget(s, now, rules)

    assert {m.id for m in forgotten} == {1, 2}
    assert len(summaries) == 1 and summaries[0]["text"].startswith("Day 3, small things:")
    assert summaries[0]["about"] == (2,) and summaries[0]["importance"] == 2
    assert {m.id for m in s.items} == {3, 4, 5}  # important, recent and beliefs stay


def test_memories_survive_save_and_load_and_the_dead_are_not_loaded(tmp_path):
    path = tmp_path / "w.sqlite"
    sim = llm_sim(seed=3, founders=6)
    store = WorldStore.create(path, sim)
    for _ in range(25 * DAY):
        sim.tick()
        store.save(sim)
    store.close()

    store = WorldStore(path)
    loaded = store.load()
    assert loaded.state() == sim.state()
    for a in loaded.population.get_alive():
        original = sim.population.get_by_id(a.id)
        assert [m.id for m in a.stream.items] == [m.id for m in original.stream.items]
        assert a.story == original.story
    for a in loaded.population.agents:
        if not a.alive:
            assert not a.stream.items  # never recalled again
    forgotten = store.conn.execute(
        "SELECT COUNT(*) FROM memories WHERE forgotten_tick IS NOT NULL"
    ).fetchone()[0]
    assert forgotten > 0  # kept for history, but not loaded
    live_ids = {m.id for a in loaded.population.agents for m in a.stream.items}
    assert set(loaded.memory_vectors) <= live_ids  # only vectors of live memories
    store.close()


def test_replay_reproduces_reflections(tmp_path):
    sim = llm_sim(seed=11, founders=6)
    sim.run(5 * DAY)
    records = {(c["tick"], c["point"]): dict(c) for c in sim.pending_llm_calls}
    assert any(r["kind"] == "reflect" for r in records.values())

    replay = Simulation(sim.config)
    replay.mind = ReplayMind(records, sim.config.llm)
    replay.run(5 * DAY)
    assert replay.state() == sim.state()
    assert [a.story for a in replay.population.agents] == [a.story for a in sim.population.agents]
