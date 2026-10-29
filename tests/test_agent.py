from genesis.mind.decision import ActionType
from helpers import World, make_agent, step


def test_needs_stay_within_bounds():
    world = World()
    agent = make_agent()
    for tick in range(300):
        step(agent, world, tick=tick)
        for value in (agent.hunger, agent.thirst, agent.energy, agent.health):
            assert 0 <= value <= 100


def test_agent_dies_at_zero_health_and_stops_acting():
    world = World()
    agent = make_agent(x=1, y=1, health=1, thirst=100)
    step(agent, world)
    assert not agent.alive
    assert agent.cause_of_death == "dehydration"

    position = agent.position
    for tick in range(1, 20):
        step(agent, world, tick=tick)
    assert agent.position == position
    assert agent.current_action is None
    assert agent.health == 0


def test_thirsty_agent_on_shore_drinks():
    world = World()
    agent = make_agent(x=11, y=15, thirst=80)
    step(agent, world)
    assert agent.current_action == ActionType.DRINK
    assert agent.thirst < 80


def test_agents_have_independent_memories():
    a = make_agent(agent_id=1, name="A")
    b = make_agent(agent_id=2, name="B")
    assert a.memory is not b.memory
    a.memory.remember_event(99)
    assert b.memory.events == []


def test_exploring_agent_finds_water():
    world = World()
    agent = make_agent(x=2, y=2, thirst=60)
    for tick in range(40):
        step(agent, world, tick=tick)
        if agent.memory.get_known_locations("water"):
            break
    assert agent.memory.get_known_locations("water")
