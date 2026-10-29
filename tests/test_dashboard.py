"""The observer dashboard (doc 12, first version): read-only API over a world file."""

import pytest
from fastapi.testclient import TestClient

from fakes import llm_sim
from genesis.dashboard.language import LanguageStats, words_of
from genesis.dashboard.server import create_app
from genesis.persistence.db import WorldStore

DAY = 24


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    path = tmp_path_factory.mktemp("dash") / "w.sqlite"
    sim = llm_sim(seed=19, founders=6)
    store = WorldStore.create(path, sim)
    for _ in range(30 * DAY):
        sim.tick()
        store.save(sim)
    store.close()
    return path, sim


@pytest.fixture(scope="module")
def client(world):
    return TestClient(create_app(world[0]))


def test_overview_counts_people_and_messages(client, world):
    _, sim = world
    d = client.get("/api/overview").json()
    assert d["tick"] == sim.tick_count and d["people"]["ever"] == len(sim.population.agents)
    assert d["people"]["alive"] == len(sim.population.get_alive())
    assert d["messages"]["total"] > 0 and d["messages"]["voiced"] > 0
    assert d["llm"]["total"] > 0 and d["feed"] and d["population"]


def test_conversations_are_whole_threads_with_effects(client):
    d = client.get("/api/conversations").json()
    assert d["total"] > d["pageSize"] and len(d["threads"]) == d["pageSize"]
    for t in d["threads"]:
        pair = {t["a"]["id"], t["b"]["id"]}
        assert all({m["from"], m["to"]} == pair for m in t["messages"])
    assert any(m["effects"] for t in d["threads"] for m in t["messages"])
    assert any(m["line"] and m["thought"] for t in d["threads"] for m in t["messages"])

    second = client.get("/api/conversations?page=1").json()["threads"]
    assert not {t["key"] for t in second} & {t["key"] for t in d["threads"]}


def test_conversation_filters(client, world):
    _, sim = world
    a = sim.population.agents[0].id
    mine = client.get(f"/api/conversations?agent={a}").json()
    assert mine["threads"] and all(a in (t["a"]["id"], t["b"]["id"]) for t in mine["threads"])

    lies = client.get("/api/conversations?lies=true").json()
    for t in lies["threads"]:
        assert any(not m["truthful"] for m in t["messages"])

    rules = client.get("/api/conversations?source=rules").json()
    assert all(any(m["source"] == "rules" for m in t["messages"]) for t in rules["threads"])

    early = client.get("/api/conversations?day_from=1&day_to=1").json()
    assert all(t["clock"].startswith("Day 1 ") for t in early["threads"])


def test_pair_history_and_agents(client, world):
    _, sim = world
    a, b = sim.population.agents[0], sim.population.agents[1]
    pair = client.get(f"/api/pair?a={a.id}&b={b.id}").json()
    assert pair["a"]["name"] == a.name and any(pair["curves"].values())
    assert client.get("/api/pair?a=1&b=999").status_code == 404

    rows = client.get("/api/agents").json()
    assert len(rows) == len(sim.population.agents)
    child = next((x for x in sim.population.agents if x.parents), None)
    assert child is not None, "expected a birth in 30 days"
    detail = client.get(f"/api/agents/{child.id}").json()
    assert len(detail["parentGenes"]) == 2 and detail["genes"]
    assert {f["relation"] for f in detail["family"]} >= {"parent"}
    thinker = next(x for x in sim.population.agents if x.story)
    assert client.get(f"/api/agents/{thinker.id}").json()["stories"]
    assert client.get(f"/api/agents/{thinker.id}/memories?kind=belief").json()["rows"]
    assert client.get(f"/api/agents/{thinker.id}/timeline").json()
    assert client.get("/api/agents/999").status_code == 404


def test_language_statistics(client):
    d = client.get("/api/language").json()
    series = d["series"]
    assert series and d["words"]["vocabulary"] > 0
    vocab = [s["vocabulary"] for s in series]
    assert vocab == sorted(vocab)  # it only grows
    assert sum(s["newWords"] for s in series) == d["words"]["vocabulary"]
    assert all(0 <= s["voicedShare"] <= 1 for s in series)


def test_language_updates_incrementally(world):
    path, _ = world
    store = WorldStore(path, readonly=True)
    whole = LanguageStats(8, None)
    whole.update(store.conn)

    parts = LanguageStats(8, None)
    last = store.conn.execute("SELECT MAX(event_id) FROM messages").fetchone()[0]
    store.conn.execute("PRAGMA query_only = 1")
    half = last // 2
    rows = store.conn.execute(
        "SELECT event_id, tick, from_id, to_id, act, payload_json, truthful, line "
        "FROM messages WHERE event_id <= ? ORDER BY event_id",
        (half,),
    ).fetchall()
    for r in rows:
        parts._add(r)
        parts.last_event_id = r["event_id"]
    parts.update(store.conn)  # the rest
    store.close()
    assert parts.series() == whole.series() and parts.counts == whole.counts


def test_valley_words_skip_english_and_names():
    stats = LanguageStats(8, {"the", "water", "lake", "is", "isn't", "cold"})
    row = {
        "event_id": 1,
        "tick": 0,
        "from_id": 1,
        "to_id": 2,
        "act": "chat",
        "payload_json": "{}",
        "truthful": 1,
        "line": "The lake water isn't cold, Mira. Zorvu says glimmerfall!",
    }
    stats._add(row)
    valley = {v["word"] for v in stats.words({"Mira", "Zorvu"})["valley"]}
    # "says" isn't in this tiny word list; names, contractions and listed words never count.
    assert valley == {"glimmerfall", "says"}
    assert words_of("Don't stop") == ["don't", "stop"]


def test_llm_monitor_and_why(client):
    d = client.get("/api/llm").json()
    assert d["kinds"] and all(k["n"] >= k["ok"] for k in d["kinds"])
    calls = client.get("/api/llm/calls?kind=reflect").json()
    assert calls["rows"] and all(c["kind"] == "reflect" for c in calls["rows"])
    full = client.get(f"/api/llm/calls/{calls['rows'][0]['id']}").json()
    assert full["prompt"] and full["response"]

    births = client.get("/api/events?type=birth").json()["rows"]
    why = client.get(f"/api/why/{births[0]['id']}").json()
    types = [e["type"] for e in why["chain"]]
    assert types[-1] == "birth" and "conceived" in types and "message" in types


def test_dashboard_never_writes(world):
    path, _ = world
    before = path.stat().st_mtime_ns
    client = TestClient(create_app(path))
    for url in ("/api/overview", "/api/conversations", "/api/language", "/api/agents", "/api/llm"):
        assert client.get(url).status_code == 200
    assert path.stat().st_mtime_ns == before
