from genesis.mind.decision import ActionType
from helpers import Position, World, make_agent, perceive


def decide(agent, world, agents=None):
    perceive(agent, world, agents)
    return agent.decide(world)


def test_urgent_thirst_beats_food_underfoot():
    """Regression: agents used to keep eating on a food patch while dying of thirst."""
    world = World()
    food = world.food_sources[1]  # (15, 10)
    agent = make_agent(x=food.position.x, y=food.position.y, thirst=80, hunger=60)
    agent.memory.remember_location("water", Position(11, 13))
    assert decide(agent, world) == ActionType.MOVE_TO_WATER


def test_fills_up_on_food_when_nothing_urgent():
    world = World()
    food = world.food_sources[1]
    agent = make_agent(x=food.position.x, y=food.position.y, thirst=40, hunger=30)
    assert decide(agent, world) == ActionType.EAT


def test_ignores_almost_empty_food_patch():
    """Regression: agents nibbled at a 1-unit patch instead of finding real food."""
    world = World()
    food = world.food_sources[1]
    food.quantity = 1
    agent = make_agent(x=food.position.x, y=food.position.y, hunger=60, thirst=20)
    assert decide(agent, world) != ActionType.EAT


def test_drinks_before_a_long_food_trip():
    world = World()
    # At the lake's north shore, hungry, with only far-away food known.
    agent = make_agent(x=9, y=13, hunger=60, thirst=50)
    agent.memory.remember_location("food", Position(5, 5))
    agent.memory.remember_location("water", Position(9, 13))
    assert decide(agent, world) == ActionType.DRINK


def test_explores_when_resource_unknown():
    world = World()
    agent = make_agent(x=2, y=2, thirst=60)
    assert decide(agent, world) == ActionType.EXPLORE


def test_socialize_only_offered_with_someone_in_view():
    world = World()
    alone = make_agent(x=2, y=2, hunger=0, thirst=0)
    decide(alone, world)
    assert "socialize" not in alone.last_scores
