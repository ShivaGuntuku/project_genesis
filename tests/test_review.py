"""Fixes from the code review (2026-10-29): one writer per world, money in small valleys,
God's changes in replays, an OpenAI-compatible gateway for faster model servers."""

import json
from types import SimpleNamespace

import httpx
import pytest

from genesis.config import WorldConfig
from genesis.god import interventions as god
from genesis.llm import gateway as gw
from genesis.llm.mind import ReplayMind
from genesis.llm.settings import LLMConfig
from genesis.persistence.db import WorldStore
from genesis.rules import Rules
from genesis.simulation import service
from genesis.simulation.engine import Simulation
from genesis.society import economy
from helpers import World, make_agent, make_ctx

TWO = "worlds/two_valleys.toml"


def world_file(tmp_path):
    folder = tmp_path / "worlds"
    folder.mkdir(exist_ok=True)
    path = folder / "w.sqlite"
    WorldStore.create(path, Simulation(WorldConfig.from_file(TWO))).close()
    return path


def test_only_one_process_may_run_a_world(tmp_path):
    path = world_file(tmp_path)
    assert not service.is_running(path)
    with service.run_lock(path, "test"):
        assert service.is_running(path)
        with pytest.raises(service.WorldBusy, match="already being run by test"):
            with service.run_lock(path, "another"):
                pass
        with pytest.raises(service.WorldBusy):
            service.run(path, 8, llm=False, echo=lambda *_: None)
    assert not service.is_running(path)


def test_a_run_whose_process_died_shows_as_stopped(tmp_path):
    path = world_file(tmp_path)
    service._write_status(path, state="running", pid=99999999, target_tick=40)
    status = service.read_status(path)
    assert status["state"] == "stopped" and "genesis resume" in status["note"]


def test_money_thresholds_scale_with_the_valley():
    world = World(
        ticks_per_day=4,
        start_hour=6,
        rules=Rules(trade=1, currency_min_indirect=20, currency_min_users=5),
    )
    people = [make_agent(agent_id=i, name=f"P{i}") for i in range(1, 8)]  # a valley of 7
    world.economy["indirect"] = [[1, "valley", "shell", 1 + i % 3] for i in range(14)]

    class Sim:
        population = SimpleNamespace(get_alive=lambda: people)

    economy.nightly(make_ctx(world, people, tick=10), Sim())
    (money,) = world.economy["currencies"]  # 14 >= 2 x 7 trades, by 3 >= 7 // 3 people
    assert money["good"] == "shell"


def test_replays_make_gods_changes_at_the_same_ticks(tmp_path):
    path = world_file(tmp_path)
    store = WorldStore(path)
    sim = store.load()
    sim.run(4 * 5)
    god.apply(sim, {"act": "migrants", "count": 2, "region": "east valley"})
    god.apply(sim, {"act": "disaster", "kind": "plague", "region": "all", "days": 3})
    sim.run(4 * 2)
    god.apply(sim, {"act": "add_food", "x": 15, "y": 12})
    sim.run(4 * 5)
    store.save(sim)
    store.close()

    store = WorldStore(path)
    saved = store.load()
    records = store.llm_records()
    store.close()
    replay = Simulation(saved.config)
    replay.mind = ReplayMind(records, saved.config.llm)
    replay.god_schedule = god.schedule_of(saved.world.god)
    assert sorted(replay.god_schedule) == [20, 28]
    replay.run(saved.tick_count)
    assert replay.state() == saved.state()


def _transport(seen):
    def handle(request: httpx.Request):
        seen.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "llama3"}]})
        if request.url.path == "/v1/embeddings":
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        body = json.loads(request.content)
        if body["model"] == "missing":
            return httpx.Response(404, text="no such model")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"choice": "a", "say": "hi"}'}}]}
        )

    return httpx.MockTransport(handle)


def test_the_openai_compatible_gateway_speaks_to_vllm_or_llama_cpp():
    seen = []
    g = gw.OpenAIGateway("http://server:8000", transport=_transport(seen))
    assert g.check() == (True, ["llama3"])
    reply = g.chat("llama3", "system", "prompt", {"type": "object"}, seed=7, max_tokens=50)
    assert reply.outcome == gw.OK and json.loads(reply.text)["say"] == "hi"
    body = json.loads(seen[-1].content)
    assert body["response_format"]["type"] == "json_schema" and body["seed"] == 7
    assert body["max_tokens"] == 50 and seen[-1].url.path == "/v1/chat/completions"
    assert g.chat("missing", "s", "p", {}).outcome == gw.ERROR
    assert g.embed("embedder", ["a"]) == [[0.1, 0.2]]


def test_the_provider_setting_picks_the_gateway():
    assert isinstance(gw.make_gateway(LLMConfig(provider="openai")), gw.OpenAIGateway)
    assert type(gw.make_gateway(LLMConfig())) is gw.OllamaGateway
    with pytest.raises(ValueError):
        LLMConfig.from_dict({"provider": "magic"})
