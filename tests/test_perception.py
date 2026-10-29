from dataclasses import fields

from genesis.mind.perception import Observation, PerceptionSystem
from helpers import Position, World, make_agent


def test_observation_has_documented_fields():
    """Regression: code used to index the observation like a dict and crash."""
    names = {f.name for f in fields(Observation)}
    assert {
        "tick",
        "position",
        "terrain",
        "visible_food",
        "visible_water",
        "visible_agents",
        "visible_tiles",
    } <= names


def test_sees_agents_in_range_only_and_not_self():
    world = World()
    me = make_agent(x=10, y=10, agent_id=1, name="Me")
    near = make_agent(x=12, y=10, agent_id=2, name="Near")
    far = make_agent(x=18, y=18, agent_id=3, name="Far")
    obs = PerceptionSystem(vision_radius=3).observe(me, world, [me, near, far], tick=0)
    assert [a["name"] for a in obs.visible_agents] == ["Near"]


def test_dead_agents_are_not_seen():
    world = World()
    me = make_agent(agent_id=1, name="Me")
    ghost = make_agent(x=11, agent_id=2, name="Ghost", alive=False)
    obs = PerceptionSystem().observe(me, world, [me, ghost], tick=0)
    assert obs.visible_agents == []


def test_sees_shore_tiles_near_lake():
    world = World()
    me = make_agent(x=12, y=15)
    obs = PerceptionSystem(vision_radius=3).observe(me, world, [me], tick=0)
    assert Position(11, 15) in obs.visible_water
    assert all(world.can_drink_at(p) for p in obs.visible_water)


def test_vision_is_clipped_at_map_edge():
    world = World()
    me = make_agent(x=0, y=0)
    obs = PerceptionSystem(vision_radius=3).observe(me, world, [me], tick=0)
    assert len(obs.visible_tiles) == 16  # 4 x 4 corner
