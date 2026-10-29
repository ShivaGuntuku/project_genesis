"""M4: the local LLM mind. A fake model stands in for Ollama (no GPU needed for tests)."""

import json
import sqlite3

import httpx
import pytest

from fakes import FakeModel, llm_sim
from genesis.api import views
from genesis.history import events as ev
from genesis.llm import gateway as gw
from genesis.llm import prompts, schemas
from genesis.llm.gateway import OllamaGateway
from genesis.llm.mind import LLMMind, PromptCollector, ReplayDiverged, ReplayMind, attach
from genesis.llm.settings import LLMConfig
from genesis.persistence.db import SCHEMA_VERSION, WorldStore
from genesis.simulation.engine import Simulation
from genesis.society import interaction
from helpers import World, make_agent, make_ctx


def lines(log):
    return [e for e in log.recent if e.type == ev.MESSAGE and e.data.get("line")]


# =================================================================
# The mind in the world
# =================================================================


def test_a_mind_that_never_answers_changes_nothing():
    """Asking costs no randomness: if every point falls back, history is exactly M3's."""
    rules = Simulation.create(seed=5, founders=5)
    rules.run(5 * 24)

    silent = Simulation.create(seed=5, founders=5)
    collector = PromptCollector()
    silent.mind = collector
    silent.run(5 * 24)

    assert collector.prompts, "there should have been decision points"
    assert silent.state() == rules.state()
    assert not lines(silent.log)


def test_conversation_in_own_words_ends_with_a_real_give():
    """M4 demo: greeting, reply, and a gift of water, all chosen and voiced by the model."""
    world = World()
    giver = make_agent(agent_id=1, name="Mira", genes={"generosity": 0.9})
    taker = make_agent(x=11, agent_id=2, name="Kael", thirst=70)
    giver.inventory["water"] = 2
    ctx = make_ctx(world, [giver, taker])
    model = FakeModel(prefer=("give:water", "chat"))
    ctx.mind = LLMMind(LLMConfig(enabled=True, conversation_turns=3), model)

    interaction.converse(ctx, giver, taker)

    said = lines(ctx.log)
    assert [e.data["act"] for e in said] == ["greet", "chat", "give"]
    assert [e.actors[0] for e in said] == [1, 2, 1]  # turns alternate
    gift = said[-1]
    assert gift.data["payload"]["resource"] == "water" and gift.data["reply_to"] == said[1].id
    assert taker.inventory["water"] == 1 and giver.inventory["water"] == 1  # it really moved
    assert taker.relationships[giver.id].gratitude > 0
    assert all(c["outcome"] == "ok" and c["event_id"] for c in ctx.llm_calls)
    assert "Kael" in model.calls[0]["prompt"] and "Mira" in model.calls[0]["prompt"]


def test_hard_choices_use_the_mind_model_and_can_refuse():
    world = World()
    asker = make_agent(agent_id=1, name="Dax", thirst=80)
    helper = make_agent(x=11, agent_id=2, name="Ione", genes={"generosity": 1.0})
    helper.inventory["water"] = 2
    ctx = make_ctx(world, [asker, helper])
    model = FakeModel(prefer=("refuse",))
    ctx.mind = LLMMind(LLMConfig(enabled=True, chat_model="small", mind_model="big"), model)

    interaction.request_item(ctx, asker, helper, "water")

    assert [c["model"] for c in model.calls] == ["small", "big"]  # ask, then the decision
    refusal = lines(ctx.log)[-1]
    assert refusal.data["payload"]["answer"] == "no" and helper.inventory["water"] == 2
    assert asker.relationships[helper.id].grievance > 0  # refused in need: it hurts


def test_lies_are_the_agents_choice_and_still_marked():
    world = World()
    asker = make_agent(agent_id=1, name="Teo", hunger=70)
    liar = make_agent(x=11, agent_id=2, name="Rhea", genes={"honesty": 0.0})
    ctx = make_ctx(world, [asker, liar])
    ctx.mind = LLMMind(LLMConfig(enabled=True), FakeModel(prefer=("mislead",)))

    interaction.request_info(ctx, asker, liar, "food")

    told = [e for e in lines(ctx.log) if e.data["act"] == "share_info"]
    assert told and told[0].data["truthful"] is False  # the observer still sees the truth
    assert ctx.llm_calls[-1]["choice"] == "mislead"


def test_honest_agents_are_not_offered_lies():
    world = World()
    asker = make_agent(agent_id=1, name="Teo", hunger=70)
    saint = make_agent(x=11, agent_id=2, name="Rhea", genes={"honesty": 1.0})
    ctx = make_ctx(world, [asker, saint])
    model = FakeModel()
    ctx.mind = LLMMind(LLMConfig(enabled=True), model)
    options = interaction._options(ctx, saint, asker)
    assert not any(o.key.startswith("mislead") for o in options)


def test_invalid_answers_are_rejected_logged_and_the_rules_decide():
    sim = llm_sim(model=FakeModel(broken=True))
    sim.run(3 * 24)  # runs fine

    calls = sim.pending_llm_calls
    choosing = [c for c in calls if c["kind"] not in ("greet", "ask")]  # nothing to choose there
    assert choosing and all(c["outcome"] == "invalid" for c in choosing)
    assert any("not one of" in c["error"] for c in calls)
    assert sim.mind.stats["failed"] > 0
    voiced = lines(sim.log)
    assert all(e.data["act"] in ("greet", "request") for e in voiced)


def test_with_ollama_stopped_the_world_runs_on_rules():
    sim = llm_sim()
    attach(sim, gateway=OllamaGateway("http://127.0.0.1:9", timeout=1, retry_after=3600))
    sim.run(3 * 24)

    assert sim.population.get_alive()
    assert [c["outcome"] for c in sim.pending_llm_calls] == ["offline"]  # tried once, then waits
    assert sim.mind.stats["skipped_offline"] > 0
    assert not lines(sim.log)


def test_budget_limits_calls_per_tick_and_keeps_room_for_hard_choices():
    sim = llm_sim(founders=8, max_calls_per_tick=2, max_calls_per_day=1000)
    sim.run(3 * 24)
    per_tick: dict[int, list] = {}
    for c in sim.pending_llm_calls:
        if c["kind"] != "reflect":  # nightly reflection is outside the per-tick budget
            per_tick.setdefault(c["tick"], []).append(c["kind"])
    assert per_tick and max(len(k) for k in per_tick.values()) <= 2
    for kinds in per_tick.values():  # chatter alone never takes the last slot
        assert sum(k in ("greet", "turn", "reply", "ask") for k in kinds) <= 1
    assert sim.mind.stats["skipped_budget"] > 0


# =================================================================
# Recording and replay
# =================================================================


def test_replay_from_recorded_answers_is_identical(tmp_path):
    path = tmp_path / "story.sqlite"
    sim = llm_sim(seed=11, founders=6)
    store = WorldStore.create(path, sim)
    for _ in range(4 * 24):
        sim.tick()
        store.save(sim)
    final = sim.state()
    store.close()

    store = WorldStore(path)
    saved = store.load()
    records = store.llm_records()
    stored_lines = store.conn.execute("SELECT COUNT(*) FROM messages WHERE source = 'llm'")
    assert stored_lines.fetchone()[0] > 0
    store.close()
    assert saved.state() == final and records

    replay = Simulation(saved.config)
    replayer = ReplayMind(records, saved.config.llm)
    replay.mind = replayer
    replay.run(4 * 24)
    assert replay.state() == final
    assert replayer.used == sum(r["outcome"] == "ok" for r in records.values())


def test_replay_notices_a_different_history():
    sim = llm_sim(seed=11, founders=6)
    sim.run(2 * 24)
    records = {(c["tick"], c["point"]): dict(c) for c in sim.pending_llm_calls}
    first = min(records)
    records[first]["agent_id"] = 999

    replay = Simulation(sim.config)
    replay.mind = ReplayMind(records, sim.config.llm)
    with pytest.raises(ReplayDiverged):
        replay.run(2 * 24)


def test_old_world_files_are_upgraded(tmp_path):
    path = tmp_path / "old.sqlite"
    WorldStore.create(path, Simulation.create(seed=1)).close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE llm_calls")
    conn.execute("DROP TABLE memories")
    conn.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    store = WorldStore(path)
    store.load()
    assert store.meta()["schema_version"] == str(SCHEMA_VERSION) and store.llm_calls() == []
    assert store.memories(1) == []
    store.close()


# =================================================================
# Prompts, answers and the gateway
# =================================================================


def test_prompts_carry_the_persona_and_stay_small():
    collector = PromptCollector()
    sim = Simulation.create(seed=3, founders=6)
    sim.mind = collector
    sim.run(4 * 24)
    kinds = {p["kind"] for p in collector.prompts}
    assert {"greet", "turn"} <= kinds

    for p in collector.prompts:
        text = p["prompt"]
        assert text.startswith("You are ")
        assert any(w in text for w in ("honest", "liar", "lie when"))  # always stated
        body = next((line for line in text.splitlines() if line.startswith("Your body")), "")
        assert "generous" not in body and "honest" not in body
        assert len(text) < 4000  # ~1,000 tokens: well inside a 2,048-token context
        assert "simulation" not in text.lower()
        if p["keys"]:
            assert all(f"- {k}:" in text for k in p["keys"])


def test_answers_are_checked_and_tidied():
    keys = ["give", "refuse"]
    good = schemas.parse('{"thought": "ok", "choice": "give", "say": "Mira: \\"Take it.\\""}', keys)
    assert good.choice == "give" and good.say == "Take it."

    for bad in (
        "not json",
        '{"choice": "give"}',  # said nothing
        '{"choice": "steal", "say": "Mine!"}',
        '{"choice": "give", "say": "   "}',
    ):
        with pytest.raises(schemas.InvalidAnswer):
            schemas.parse(bad, keys)

    junk = schemas.parse(json.dumps({"say": "><!-- she asked first.--> **Fine**"}), None)
    assert junk.say == "she asked first. Fine"

    long = schemas.parse(json.dumps({"say": "word " * 100}), None, max_chars=40)
    assert len(long.say) <= 40 and long.say.endswith("…")
    assert schemas.schema(keys)["properties"]["choice"]["enum"] == keys
    assert list(schemas.schema(None)["properties"]) == ["thought", "say"]


def test_gateway_talks_ollama_and_handles_failures():
    def handler(request):
        body = json.loads(request.content)
        if body["model"] == "missing":
            return httpx.Response(404, json={"error": "model not found"})
        assert body["format"]["type"] == "object" and body["stream"] is False
        return httpx.Response(200, json={"message": {"content": '{"say": "hi"}'}})

    gateway = OllamaGateway(transport=httpx.MockTransport(handler))
    reply = gateway.chat("llama3", "sys", "hello", schemas.schema(None))
    assert reply.outcome == gw.OK and json.loads(reply.text) == {"say": "hi"}

    missing = gateway.chat("missing", "sys", "hello", schemas.schema(None))
    assert missing.outcome == gw.ERROR and "ollama pull missing" in missing.error

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    down = OllamaGateway(transport=httpx.MockTransport(refuse), retry_after=60)
    assert down.chat("llama3", "s", "p", {}).outcome == gw.OFFLINE
    assert not down.available()
    assert down.chat("llama3", "s", "p", {}).outcome == gw.OFFLINE  # no new attempt


def test_directions_read_naturally():
    from genesis.world.world import Position

    assert prompts.direction(Position(5, 5), Position(5, 5)) == "right here"
    assert prompts.direction(Position(5, 5), Position(11, 5)) == "about 6 steps east"
    assert prompts.direction(Position(5, 5), Position(8, 1)) == "about 7 steps north-east"
    assert prompts.direction(Position(5, 5), Position(5, 9)) == "about 4 steps south"


def test_viewer_bubbles_show_the_agents_own_words():
    sim = llm_sim()
    sim.run(2 * 24)
    names = {a.id: a.name for a in sim.population.agents}
    voiced = lines(sim.log)
    assert voiced
    item = views.event_item(voiced[0], names, sim)
    assert item["bubble"] == voiced[0].data["line"] and item["voiced"] and item["thought"]
    assert f'"{voiced[0].data["line"]}"' in item["text"]


# =================================================================
# Real Ollama (optional): GENESIS_TEST_OLLAMA=1 uv run pytest tests/test_llm.py -k real
# =================================================================


def test_real_ollama_conversation():
    import os

    if not os.environ.get("GENESIS_TEST_OLLAMA"):
        pytest.skip("set GENESIS_TEST_OLLAMA=1 to talk to the local model")
    sim = llm_sim(founders=5, max_calls_per_tick=4)
    status = attach(sim)
    assert "on," in status, status
    sim.run(24)
    calls = sim.pending_llm_calls
    assert calls and sum(c["outcome"] == "ok" for c in calls) >= 0.9 * len(calls)


def test_live_viewer_runs_the_llm_mind(tmp_path):
    from fastapi.testclient import TestClient

    from genesis.api.server import Runner, create_app

    path = tmp_path / "w.sqlite"
    WorldStore.create(path, llm_sim(seed=3, founders=6)).close()
    runner = Runner(path, speed=0, max_ticks=48, gateway=FakeModel())
    with TestClient(create_app(runner)) as client:
        assert runner.finished.wait(60)
        assert "LLM mind: on" in runner.llm_status
        detail = client.get("/api/agent/1").json()
        assert detail["events"] and "voiced" in detail["events"][0]

    store = WorldStore(path)
    assert store.llm_stats()["total"] > 0
    assert store.conn.execute("SELECT COUNT(*) FROM messages WHERE line IS NOT NULL").fetchone()[0]
    store.close()

    runner = Runner(path, speed=0, max_ticks=5, llm=False)  # --no-llm
    with TestClient(create_app(runner)):
        assert runner.finished.wait(30)
    assert "off" in runner.llm_status
