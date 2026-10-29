"""M7: world events, groups, leaders, orders and conflict."""

import random

import pytest

from fakes import FakeModel
from genesis.config import ConfigError, WorldConfig
from genesis.history import events as ev
from genesis.llm.mind import LLMMind, ReplayMind, attach
from genesis.llm.settings import LLMConfig
from genesis.persistence.db import WorldStore
from genesis.simulation.engine import Simulation
from genesis.society import conflict, groups
from helpers import World, make_agent, make_ctx

DAY = 24
TWO_LAKES = "worlds/two_lakes.toml"


def two_lakes(seed=9, llm=None) -> Simulation:
    config = WorldConfig.from_file(TWO_LAKES).with_overrides(seed=seed)
    if llm is not None:
        config = config.with_overrides(llm=LLMConfig(enabled=True))
    sim = Simulation(config)
    if llm is not None:
        attach(sim, gateway=llm)
    return sim


def friends(*agents, affinity=0.8):
    for a in agents:
        for b in agents:
            if a is not b:
                rel = a.relationship(b.id, b.name)
                rel.affinity, rel.trust, rel.familiarity = affinity, 0.8, 0.5


def people(n, **genes):
    world = World()
    agents = [
        make_agent(
            x=10 + i % 3, y=10 + i // 3, agent_id=i + 1, name=f"P{i + 1}", genes=genes or None
        )
        for i in range(n)
    ]
    return world, agents, make_ctx(world, agents)


# =================================================================
# World events
# =================================================================


def test_droughts_dry_a_lake_and_end_on_schedule(demo_world):
    world = demo_world.world
    assert world.lakes == 2
    west = next(p for p, lakes in world.shore_lakes.items() if lakes == {1})
    east = next(p for p, lakes in world.shore_lakes.items() if lakes == {2})
    assert world.conditions_on(39) == (frozenset(), frozenset())
    assert world.conditions_on(40)[0] == {1} and world.conditions_on(54)[0] == {1}
    assert world.conditions_on(55)[0] == frozenset()
    world.dry_lakes = frozenset({1})
    assert not world.can_drink_at(west) and world.can_drink_at(east)
    world.dry_lakes = frozenset()

    changes = [e for e in demo_world.log.pending if e.type == ev.WORLD_CHANGED]
    assert [e.data["started"] for e in changes] == [True, False]
    assert changes[0].data == {"kind": "drought", "target": 1, "started": True}
    assert changes[1].cause_id == changes[0].id
    assert (demo_world.config.start_hour + changes[0].tick) // DAY + 1 == 40


def test_famine_stops_regrowth_and_bad_events_are_refused():
    config = WorldConfig.default().with_overrides(
        events=(WorldConfig.from_toml(_event_toml("famine", 1)).events[0],)
    )
    sim = Simulation(config)
    patch = sim.world.food_sources[0]
    patch.quantity = 0
    sim.run(5)
    assert patch.quantity == 0 and sim.world.food_sources[1].quantity > 0
    with pytest.raises(ConfigError):
        WorldConfig.from_toml(_event_toml("earthquake", 1))
    with pytest.raises(ConfigError):
        WorldConfig.from_toml(_event_toml("famine", 99))


def _event_toml(kind, target):
    text = WorldConfig.default().source_toml
    return text + f'\n[[events]]\nkind = "{kind}"\nday = 1\ndays = 3\ntarget = {target}\n'


def test_agents_forget_a_dried_up_shore_when_they_see_it():
    sim = two_lakes()
    agent = sim.population.agents[0]
    shore = next(p for p, lakes in sim.world.shore_lakes.items() if lakes == {1})
    agent.memory.remember_location("water", shore)
    agent.position = shore
    sim.world.dry_lakes = frozenset({1})
    from genesis.simulation.context import TickContext

    agent.perceive(
        TickContext(
            world=sim.world,
            tick=0,
            log=sim.log,
            rng=sim.rng,
            agents={a.id: a for a in sim.population.agents},
        )
    )
    assert not agent.memory.knows_location("water", shore)


# =================================================================
# Groups
# =================================================================


def test_a_group_is_founded_only_by_agreement():
    world, (a, b, c, d), ctx = people(4, ambition=0.9)
    friends(a, b, c, d)
    assert groups.may_found(ctx, a, b)
    ctx.rng.seed(1)
    answer = groups.ask_to_join(ctx, a, b)
    g = groups.group_of(ctx, a)
    assert g is not None and g.members == [a.id, b.id] and g.leader == a.id
    founded = next(e for e in ctx.log.recent if e.type == ev.FOUNDED_GROUP)
    assert founded.cause_id == answer and founded.data["name"] == g.name

    assert groups.may_invite(ctx, b, c)  # any member may invite
    groups.ask_to_join(ctx, b, c)
    assert c.group_id == g.id
    joined = next(e for e in ctx.log.recent if e.type == ev.JOINED_GROUP)
    assert joined.actors == (c.id,) and joined.targets == (b.id,)

    loner = make_agent(x=13, agent_id=9, name="Loner", genes={"ambition": 0.1})
    ctx.agents[9] = loner
    friends(loner, d)
    assert not groups.may_found(ctx, loner, d)  # not ambitious enough


def test_members_who_resent_the_leader_leave_and_dont_bounce_back():
    world, (a, b, c), ctx = people(3, ambition=0.9)
    friends(a, b, c)
    ctx.rng.seed(1)
    groups.ask_to_join(ctx, a, b)
    g = groups.group_of(ctx, a)
    b.relationship(a.id, a.name).grievance = 0.8
    groups.nightly(ctx)
    assert b.group_id is None and b.id not in g.members
    left = next(e for e in ctx.log.recent if e.type == ev.LEFT_GROUP)
    assert "grudge" in left.data["reason"]
    assert not groups.may_invite(ctx, a, b)  # not back for rejoin_days


def test_switching_sides_and_succession():
    world, (a, b, c, d), ctx = people(4, ambition=0.9)
    friends(a, b, c, d)
    first = groups.found(ctx, a, b, None)  # a leads b
    second = groups.found(ctx, c, d, None)  # c leads d

    rel = b.relationship(a.id, a.name)  # b has had enough of a, and likes c
    rel.affinity, rel.respect, rel.grievance = -0.4, 0.0, 0.45
    ctx.rng.seed(2)
    for _ in range(30):
        b.asked.clear()
        c.asked.clear()
        groups.ask_to_join(ctx, c, b)
        if b.group_id == second.id:
            break
    assert b.group_id == second.id and b.id not in first.members
    left = [e for e in ctx.log.recent if e.type == ev.LEFT_GROUP and e.actors == (b.id,)]
    assert left and left[-1].data["reason"] == f"joined {second.name}"

    loyal = make_agent(x=14, agent_id=5, name="Loyal")  # likes and respects their leader
    ctx.agents[5] = loyal
    friends(loyal, a, c)
    groups.join(ctx, loyal, first, "invited", None)
    loyal.relationship(a.id, a.name).respect = 0.9
    tries = 0
    for _ in range(30):
        loyal.asked.clear()
        c.asked.clear()
        groups.ask_to_join(ctx, c, loyal)
        tries += 1
    assert loyal.group_id == first.id  # never tempted away

    died = ctx.log.emit(0, ev.DIED, actors=(c.id,), data={"cause": "old age"})
    c.alive = False
    groups.on_death(ctx, c, died)
    assert second.leader in (b.id, d.id)
    assert any(e.type == ev.LEADER_CHANGED for e in ctx.log.recent)


def test_a_challenge_is_decided_by_the_members_backing():
    world, (leader, rival, x, y), ctx = people(4, ambition=0.9)
    friends(leader, rival, x, y)
    ctx.rng.seed(3)
    groups.ask_to_join(ctx, leader, rival)
    g = groups.group_of(ctx, leader)
    for m in (x, y):
        groups.join(ctx, m, g, "invited", None)
        m.relationship(rival.id, rival.name).respect = 0.9  # everyone backs the rival
    rival.relationship(leader.id, leader.name).respect = 0.0
    assert groups.may_challenge(ctx, rival, leader)
    groups.challenge(ctx, rival, leader)
    assert g.leader == rival.id
    result = next(e for e in ctx.log.recent if e.type == ev.LEADER_CHALLENGED)
    assert result.data["won"] and result.data["votes"][rival.name] == 3


def test_orders_are_obeyed_or_refused_and_carried_out():
    world, (leader, member, needy), ctx = people(3, ambition=0.9)
    friends(leader, member, needy)
    ctx.rng.seed(4)
    groups.ask_to_join(ctx, leader, member)
    g = groups.group_of(ctx, leader)
    ctx.mind = LLMMind(LLMConfig(enabled=True), FakeModel(prefer=("obey",)))
    keys = [o.key for o in groups.order_options(ctx, leader, member)]
    assert "order:gather" in keys

    groups.give_order(ctx, leader, member, "order:gather")
    assert member.order["kind"] == "gather"
    member.inventory["food"] = 2 * world.rules.food_per_meal
    groups.contribute(ctx, member)
    assert g.store_food == world.rules.food_per_meal and member.order is None
    done = next(e for e in ctx.log.recent if e.type == ev.ORDER_DONE)
    assert done.data["done"] and done.targets == (leader.id,)

    ctx.mind = LLMMind(LLMConfig(enabled=True), FakeModel(prefer=("refuse",)))
    ctx.tick += DAY
    before = leader.relationship(member.id, member.name).grievance
    groups.give_order(ctx, leader, member, "order:gather")
    assert member.order is None and leader.relationships[member.id].grievance > before


def test_an_order_not_carried_out_lapses():
    world, (leader, member), ctx = people(2, ambition=0.9)
    friends(leader, member)
    ctx.rng.seed(4)
    groups.ask_to_join(ctx, leader, member)
    ctx.mind = LLMMind(LLMConfig(enabled=True), FakeModel(prefer=("obey",)))
    groups.give_order(ctx, leader, member, "order:gather")
    ctx.tick += (world.rules.order_days + 1) * DAY
    groups.nightly(ctx)
    lapsed = [e for e in ctx.log.recent if e.type == ev.ORDER_DONE]
    assert lapsed and lapsed[-1].data["done"] is False and member.order is None


# =================================================================
# Conflict
# =================================================================


def test_the_stronger_side_wins_most_fights():
    """docs/08 M7 check: over 200 seeded fights, strength decides (with luck)."""
    wins = 0
    for seed in range(200):
        world = World()
        strong = make_agent(agent_id=1, name="Strong", genes={"strength": 0.8})
        weak = make_agent(x=11, agent_id=2, name="Weak", genes={"strength": 0.3})
        ctx = make_ctx(world, [strong, weak], seed=seed)
        attacker, defender = (strong, weak) if seed % 2 else (weak, strong)
        conflict.fight(ctx, attacker, defender)
        winner = next(e for e in ctx.log.recent if e.type == ev.FIGHT).data["winner"]
        wins += winner == strong.id
    assert wins >= 150, f"the stronger won only {wins}/200"  # luck still matters...
    assert wins < 200  # ...so the weaker sometimes wins


def test_a_fight_hurts_is_witnessed_and_starts_a_feud():
    world = World()
    bully = make_agent(agent_id=1, name="Bully", genes={"strength": 1.0, "aggression": 0.9})
    victim = make_agent(x=11, agent_id=2, name="Victim", genes={"strength": 0.0})
    witness = make_agent(x=12, agent_id=3, name="Witness")
    sister = make_agent(x=19, y=19, agent_id=4, name="Sister")
    victim.parents = sister.parents = (90, 91)
    victim.inventory["water"] = 2
    ctx = make_ctx(world, [bully, victim, witness, sister], seed=5)
    conflict.fight(ctx, bully, victim)

    fight = next(e for e in ctx.log.recent if e.type == ev.FIGHT)
    assert fight.data["winner"] == bully.id and victim.health < 100
    assert fight.data["took"] == {"resource": "water", "amount": 1}
    assert fight.data["witnesses"] == [witness.id]
    assert witness.relationships[bully.id].affinity < 0
    assert victim.relationships[bully.id].fear > 0
    assert sister.relationships[bully.id].grievance > 0  # the feud spreads to family
    assert sister.relationships[bully.id].grievance < victim.relationships[bully.id].grievance


def test_fights_can_kill_but_rarely_and_family_is_spared():
    world = World()
    a = make_agent(agent_id=1, name="A", genes={"strength": 1.0, "aggression": 1.0})
    b = make_agent(x=11, agent_id=2, name="B", genes={"strength": 0.0}, health=10)
    ctx = make_ctx(world, [a, b], seed=1)
    conflict.fight(ctx, a, b)
    assert not b.alive and b.cause_of_death == "killed"

    c = make_agent(x=12, agent_id=3, name="C", genes={"aggression": 1.0})
    d = make_agent(x=13, agent_id=4, name="D")
    c.partner, d.partner = 4, 3
    c.relationship(d.id, d.name).grievance = 0.6
    ctx = make_ctx(world, [c, d])
    assert not conflict.may_attack(ctx, c, d)  # partners don't fight without a deep grudge
    c.relationship(d.id, d.name).grievance = 0.9
    assert conflict.may_attack(ctx, c, d)


def test_provocation_temper_grudge_and_fear():
    world = World()
    gentle = make_agent(agent_id=1, name="Gentle", genes={"aggression": 0.3})
    other = make_agent(x=11, agent_id=2, name="Other")
    ctx = make_ctx(world, [gentle, other])
    rel = gentle.relationship(other.id, other.name)
    rel.grievance = 0.5
    assert not conflict.may_attack(ctx, gentle, other)
    rel.grievance = 0.75
    assert conflict.may_attack(ctx, gentle, other)
    rel.fear = 0.3  # beaten before: thinks twice
    assert not conflict.may_attack(ctx, gentle, other)


def test_a_refused_threat_can_turn_into_a_fight():
    world = World()
    bully = make_agent(agent_id=1, name="Bully", genes={"aggression": 0.9}, thirst=90)
    victim = make_agent(x=11, agent_id=2, name="Victim", genes={"strength": 1.0})
    victim.inventory["water"] = 2
    bully.relationship(victim.id, victim.name).grievance = 0.3
    ctx = make_ctx(world, [bully, victim])
    ctx.mind = LLMMind(LLMConfig(enabled=True), FakeModel(prefer=("refuse", "attack")))
    from genesis.society import interaction

    interaction.threaten(ctx, bully, victim, "water")
    kinds = [c["kind"] for c in ctx.llm_calls]
    assert kinds == ["threat", "fight"]
    assert any(e.type == ev.FIGHT for e in ctx.log.recent)


# =================================================================
# Over a whole world (docs/08 M7 checks)
# =================================================================


@pytest.fixture(scope="module")
def demo_world():
    sim = two_lakes(seed=9)
    sim.run(75 * DAY)
    return sim


def test_group_membership_changes_only_through_recorded_events(demo_world):
    sim = demo_world
    members: dict[int, list] = {}
    for e in sim.log.pending:
        g = e.data.get("group")
        if e.type == ev.FOUNDED_GROUP:
            members[g] = [*e.actors, *e.targets]
        elif e.type == ev.JOINED_GROUP:
            members[g].append(e.actors[0])
        elif e.type == ev.LEFT_GROUP:
            members[g].remove(e.actors[0])
    assert members and len(sim.groups) >= 2
    for g in sim.groups.values():
        assert sorted(members[g.id]) == sorted(g.members), g.name
    for a in sim.population.agents:
        assert a.group_id is None or a.id in sim.groups[a.group_id].members


def test_demo_two_groups_a_drought_a_fight_and_a_switch(demo_world):
    """docs/08 M7 demo (two_lakes, seed 9): drought → trespass → fight, and a switch."""
    sim = demo_world
    events = {e.id: e for e in sim.log.pending}
    homes = sorted(g.home[0] for g in sim.groups.values())
    assert homes[0] < 18 < homes[-1]  # one group by each lake

    def chain(e):
        out = []
        while e is not None:
            out.append(e)
            e = events.get(e.cause_id)
        return out

    fights = [e for e in events.values() if e.type == ev.FIGHT]
    from_drought = [f for f in fights if any(x.type == ev.WORLD_CHANGED for x in chain(f))]
    assert from_drought
    assert [x.type for x in reversed(chain(from_drought[0]))][:2] == [
        ev.WORLD_CHANGED,
        ev.TRESPASS,
    ]

    belonged: dict[int, set] = {}
    for e in events.values():
        if e.type == ev.JOINED_GROUP:
            belonged.setdefault(e.actors[0], set()).add(e.data["group"])
        if e.type == ev.FOUNDED_GROUP:
            for a in (*e.actors, *e.targets):
                belonged.setdefault(a, set()).add(e.data["group"])
    assert any(len(gs) > 1 for gs in belonged.values())


@pytest.fixture(scope="module")
def saved_world(tmp_path_factory):
    """The demo world saved tick by tick for 45 days (stopped in the middle of the drought)."""
    path = tmp_path_factory.mktemp("two_lakes") / "w.sqlite"
    sim = two_lakes(seed=9)
    store = WorldStore.create(path, sim)
    for _ in range(45 * DAY):
        sim.tick()
        store.save(sim)
    store.close()
    return path


def test_groups_save_and_resume_identically(saved_world, demo_world):
    store = WorldStore(saved_world)
    resumed = store.load()
    store.close()
    assert resumed.world.dry_lakes == frozenset({1})  # resumed mid-drought
    resumed.run(30 * DAY)  # to day 75, like the uninterrupted demo world
    assert resumed.state() == demo_world.state()
    assert resumed.groups.keys() == demo_world.groups.keys()


def test_replay_reproduces_groups_orders_and_fights():
    model = FakeModel(prefer=("group", "join", "order", "obey", "praise"))
    sim = two_lakes(seed=9, llm=model)
    sim.run(30 * DAY)
    records = {(c["tick"], c["point"]): dict(c) for c in sim.pending_llm_calls}
    kinds = {r["kind"] for r in records.values()}
    assert {"join", "group_name", "obey"} <= kinds

    replay = Simulation(sim.config)
    replay.mind = ReplayMind(records, sim.config.llm)
    replay.run(30 * DAY)
    assert replay.state() == sim.state()
    assert [g.name for g in replay.groups.values()] == [g.name for g in sim.groups.values()]


def test_daily_metrics_measure_the_network_and_groups(demo_world):
    names = {n for _, n, _ in demo_world.pending_metrics}
    assert {"social.communities", "social.density", "groups.count", "today.fight"} <= names


def test_dashboard_groups_and_network(saved_world):
    from fastapi.testclient import TestClient

    from genesis.dashboard.server import create_app

    client = TestClient(create_app(saved_world))
    data = client.get("/api/groups").json()
    assert len(data["groups"]) >= 2 and data["series"]
    g = data["groups"][0]
    assert g["members"] and g["history"] and "obeyed" in g["orders"]
    store = WorldStore(saved_world, readonly=True)
    alive = {r[0] for r in store.conn.execute("SELECT id FROM agents WHERE alive = 1")}
    store.close()
    net = client.get("/api/network").json()
    assert {n["id"] for n in net["nodes"]} == alive
    assert all(e["source"] in alive and e["target"] in alive for e in net["edges"])
    assert {e["kind"] for e in net["edges"]} <= {"friend", "family", "partner", "rival"}
    assert net["edges"]
    assert client.get("/api/overview").json()["conflict"]["groupsEver"] >= 2


def test_trespass_events_and_persona_mention_groups(demo_world):
    sim = demo_world
    tres = [e for e in sim.log.pending if e.type == ev.TRESPASS]
    assert tres and any(e.data["drought"] and e.cause_id for e in tres)

    from genesis.llm import persona
    from genesis.simulation.context import TickContext

    ctx = TickContext(
        world=sim.world,
        tick=sim.tick_count,
        log=sim.log,
        rng=random.Random(0),
        agents={a.id: a for a in sim.population.agents},
        groups=sim.groups,
    )
    member = next(a for a in sim.population.get_alive() if a.group_id is not None)
    assert "You belong to a group called" in persona.fact_block(member, ctx)
