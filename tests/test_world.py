from genesis.mind.planner import NavigationPlanner
from genesis.rules import Rules
from helpers import Position, World

FOOD_PER_MEAL = Rules().food_per_meal


def test_lake_is_not_walkable_but_shore_is_drinkable():
    world = World()
    assert not world.is_walkable(Position(9, 15))  # middle of the lake
    assert world.can_drink_at(Position(11, 15))  # east shore
    assert world.can_drink_at(Position(7, 14))  # west shore
    assert not world.can_drink_at(Position(1, 1))  # far away
    assert not world.can_drink_at(Position(9, 15))  # in the water itself


def test_out_of_bounds_is_not_walkable():
    world = World()
    assert not world.is_walkable(Position(-1, 0))
    assert not world.is_walkable(Position(0, world.height))


def test_food_regrows_up_to_max():
    world = World()
    food = world.food_sources[0]
    food.quantity = 0
    world.update()
    assert food.quantity == food.regen_per_tick
    food.quantity = food.max_quantity
    world.update()
    assert food.quantity == food.max_quantity


def test_food_below_one_meal_is_not_offered():
    world = World()
    food = world.food_sources[0]
    food.quantity = FOOD_PER_MEAL - 1
    assert world.get_food_at(food.position) is None
    assert world.get_food_at(food.position, min_quantity=1) is food


def test_path_goes_around_the_lake():
    world = World()
    path = NavigationPlanner().find_path(Position(7, 15), Position(11, 15), world)
    assert path is not None
    assert all(world.is_walkable(p) for p in path)
    assert len(path) - 1 > Position(7, 15).distance(Position(11, 15))  # had to detour


def test_path_to_water_tile_is_impossible():
    assert NavigationPlanner().find_path(Position(5, 5), Position(9, 15), World()) is None
