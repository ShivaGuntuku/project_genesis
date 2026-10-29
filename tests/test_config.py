from pathlib import Path

import pytest

from genesis.config import ConfigError, WorldConfig
from genesis.rules import Rules
from genesis.world.world import Position, TerrainType, World

REPO = Path(__file__).resolve().parents[1]


def test_default_config_loads_and_builds_the_eden_map():
    config = WorldConfig.default()
    world = World(config)
    assert (config.name, config.seed, world.width, world.height) == ("eden", 42, 20, 20)
    assert world.get_terrain(Position(9, 15)) == TerrainType.WATER
    assert world.get_terrain(Position(3, 11)) == TerrainType.FOREST
    assert [f.position for f in world.food_sources] == [
        Position(5, 5),
        Position(15, 10),
        Position(4, 16),
    ]


def test_example_file_matches_built_in_default():
    example = WorldConfig.from_file(REPO / "worlds" / "eden.toml")
    assert example.to_dict() | {"source_toml": ""} == WorldConfig.default().to_dict() | {
        "source_toml": ""
    }


def test_rules_can_be_overridden_in_toml():
    text = WorldConfig.default().source_toml.replace(
        "[rules]", "[rules]\nthirst_per_tick = 5\nvision_radius = 4"
    )
    config = WorldConfig.from_toml(text)
    assert config.rules.thirst_per_tick == 5
    assert config.rules.vision_radius == 4
    assert config.rules.hunger_per_tick == Rules().hunger_per_tick


@pytest.mark.parametrize(
    "change, message",
    [
        (("[rules]", "[rules]\nthirst_per_tik = 5"), "Unknown rule"),
        (("width = 20", "width = 2"), "between 5 and 500"),
        (("founders = 3", "founders = 999"), "500 or fewer"),
        (('terrain = "forest"', 'terrain = "lava"'), "region terrain"),
        (("x = 5\ny = 5", "x = 50\ny = 5"), "outside the map"),
        (('name = "eden"', ""), "Missing required setting: name"),
    ],
)
def test_bad_configs_are_rejected_with_clear_messages(change, message):
    text = WorldConfig.default().source_toml.replace(*change, 1)
    with pytest.raises(ConfigError, match=message):
        WorldConfig.from_toml(text)


def test_config_round_trips_through_dict():
    config = WorldConfig.default().with_overrides(seed=9, founders=5)
    assert WorldConfig.from_dict(config.to_dict()) == config


def test_food_regeneration_comes_from_config():
    config = WorldConfig.default()
    text = config.source_toml.replace("regen_per_tick = 3", "regen_per_tick = 4", 1)
    world = World(WorldConfig.from_toml(text))
    food = world.food_sources[0]
    food.quantity = 0
    world.update()
    assert food.quantity == 4


def test_more_founders_than_names_get_made_up_names():
    from genesis.simulation.engine import Simulation

    config = WorldConfig.default().with_overrides(founders=20, seed=19)
    sim = Simulation(config)
    names = [a.name for a in sim.population.agents]
    assert len(names) == 20 and len(set(n.lower() for n in names)) == 20
    assert names[:10] == list(config.names[:10])
    assert [a.name for a in Simulation(config).population.agents] == names  # same every time
