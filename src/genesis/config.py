"""World configuration, loaded from a TOML file.

See worlds/eden.toml for a documented example.
"""

import tomllib
from dataclasses import asdict, dataclass, field, replace
from importlib import resources
from pathlib import Path

from genesis.llm.settings import LLMConfig
from genesis.rules import Rules

TERRAIN_TYPES = {"plains", "forest", "mountain", "water", "peak"}


class ConfigError(ValueError):
    pass


def _generated(data: dict) -> dict:
    """Fill world.regions, resources.food and population.starts from the map generator."""
    from genesis.world import generate

    world, population = dict(data["world"]), dict(data.get("population", {}))
    if world["generate"] != "valleys":
        raise ConfigError('world.generate must be "valleys"')
    try:
        layout = generate.valleys(
            world["seed"],
            world["width"],
            world["height"],
            world.get("valleys", 2),
            population.get("founders", 3),
            passes=world.get("passes", True),
        )
    except KeyError as e:
        raise ConfigError(f"Missing required setting: {e.args[0]}") from e
    except ValueError as e:
        raise ConfigError(str(e)) from e
    world["regions"] = layout["regions"] + world.get("regions", [])
    population["starts"] = layout["starts"]
    population.setdefault("start_center", [layout["starts"][0]["x"], layout["starts"][0]["y"]])
    resources = dict(data.get("resources", {}))
    resources["food"] = layout["food"] + resources.get("food", [])
    return {**data, "world": world, "population": population, "resources": resources}


@dataclass(frozen=True)
class Region:
    terrain: str
    x: tuple[int, int]  # inclusive
    y: tuple[int, int]  # inclusive


@dataclass(frozen=True)
class FoodConfig:
    x: int
    y: int
    quantity: int = 100
    max_quantity: int = 100
    regen_per_tick: int = 1
    stage: int = 0  # M8e: dormant until this many people have lived at once


EVENT_KINDS = {"drought", "famine"}


@dataclass(frozen=True)
class StartConfig:
    """Where some of the founders start (M8a: several valleys). Without any, all founders
    start near population.start_center."""

    x: int
    y: int
    founders: int
    radius: int = 3
    name: str = ""  # e.g. the valley's name, for people reading the config


@dataclass(frozen=True)
class WorldEventConfig:
    """Something scheduled to happen to the world (M7): a drought dries a lake, a famine
    stops a berry patch regrowing. `target` is the lake number (1 = the first lake found,
    scanning columns left to right) or the food patch number (1 = first in the file)."""

    kind: str
    day: int  # first day (1-based)
    days: int  # how long it lasts
    target: int = 1


@dataclass(frozen=True)
class WorldConfig:
    name: str
    seed: int
    width: int
    height: int
    default_terrain: str = "plains"
    regions: tuple[Region, ...] = ()
    food: tuple[FoodConfig, ...] = ()
    founders: int = 3
    names: tuple[str, ...] = ()
    start_center: tuple[int, int] = (10, 10)
    start_radius: int = 3
    start_hour: int = 8
    ticks_per_day: int = 24  # 24: story mode (hours); 4: millennium mode (watches of 6 h)
    days_per_year: int = 360
    seasons: bool = False  # resources change with the seasons
    save_every_ticks: int = 1  # save to disk every N ticks (1 = every tick; long runs: a day)
    rules: Rules = field(default_factory=Rules)
    llm: LLMConfig = field(default_factory=LLMConfig)
    events: tuple[WorldEventConfig, ...] = ()
    starts: tuple[StartConfig, ...] = ()
    source_toml: str = ""  # the exact text it was loaded from (saved with the world)

    # --------------------------------

    @classmethod
    def from_toml(cls, text: str) -> "WorldConfig":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"Invalid TOML: {e}") from e

        world = data.get("world", {})
        population = data.get("population", {})
        time = data.get("time", {})
        if world.get("generate"):  # M8e: valleys made from the seed
            data = _generated(data)
            world, population = data["world"], data["population"]

        try:
            config = cls(
                name=world["name"],
                seed=world["seed"],
                width=world["width"],
                height=world["height"],
                default_terrain=world.get("default_terrain", "plains"),
                regions=tuple(
                    Region(r["terrain"], tuple(r["x"]), tuple(r["y"]))
                    for r in world.get("regions", [])
                ),
                food=tuple(FoodConfig(**f) for f in data.get("resources", {}).get("food", [])),
                founders=population.get("founders", 3),
                names=tuple(population.get("names", [])),
                start_center=tuple(population.get("start_center", [10, 10])),
                start_radius=population.get("start_radius", 3),
                start_hour=time.get("start_hour", 8),
                ticks_per_day=time.get("ticks_per_day", 24),
                days_per_year=time.get("days_per_year", 360),
                seasons=time.get("seasons", False),
                save_every_ticks=time.get("save_every_ticks", 1),
                rules=Rules.from_dict(data.get("rules", {})),
                llm=LLMConfig.from_dict(data.get("llm", {})),
                events=tuple(WorldEventConfig(**e) for e in data.get("events", [])),
                starts=tuple(StartConfig(**s) for s in population.get("starts", [])),
                source_toml=text,
            )
        except KeyError as e:
            raise ConfigError(f"Missing required setting: {e.args[0]}") from e
        except (TypeError, ValueError) as e:
            raise ConfigError(f"Bad setting: {e}") from e

        config.validate()
        return config

    @classmethod
    def from_file(cls, path: str | Path) -> "WorldConfig":
        return cls.from_toml(Path(path).read_text())

    @classmethod
    def default(cls) -> "WorldConfig":
        text = resources.files("genesis").joinpath("default_world.toml").read_text()
        return cls.from_toml(text)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WorldConfig":
        return cls(
            **{
                **d,
                "regions": tuple(
                    Region(r["terrain"], tuple(r["x"]), tuple(r["y"])) for r in d["regions"]
                ),
                "food": tuple(FoodConfig(**f) for f in d["food"]),
                "names": tuple(d["names"]),
                "start_center": tuple(d["start_center"]),
                "rules": Rules.from_dict(d["rules"]),
                "llm": LLMConfig.from_dict(d.get("llm", {})),
                "events": tuple(WorldEventConfig(**e) for e in d.get("events", [])),
                "starts": tuple(StartConfig(**s) for s in d.get("starts", [])),
            }
        )

    def with_overrides(self, **changes) -> "WorldConfig":
        """A copy with some fields changed (source_toml is cleared: it no longer matches)."""
        config = replace(self, source_toml="", **changes)
        config.validate()
        return config

    # --------------------------------

    def validate(self):
        def fail(message):
            raise ConfigError(message)

        if not self.name or not self.name.replace("-", "").replace("_", "").isalnum():
            fail("world.name must be letters, digits, - or _")
        if not (5 <= self.width <= 500 and 5 <= self.height <= 500):
            fail("world.width and world.height must be between 5 and 500")
        if self.default_terrain not in TERRAIN_TYPES:
            fail(f"world.default_terrain must be one of {sorted(TERRAIN_TYPES)}")

        for r in self.regions:
            if r.terrain not in TERRAIN_TYPES:
                fail(f"region terrain must be one of {sorted(TERRAIN_TYPES)}, got {r.terrain!r}")
            if len(r.x) != 2 or len(r.y) != 2 or r.x[0] > r.x[1] or r.y[0] > r.y[1]:
                fail(f"region ranges must be [low, high], got x={r.x} y={r.y}")

        for f in self.food:
            if not (0 <= f.x < self.width and 0 <= f.y < self.height):
                fail(f"food at ({f.x},{f.y}) is outside the map")
            if not (0 <= f.quantity <= f.max_quantity) or f.regen_per_tick < 0:
                fail(f"food at ({f.x},{f.y}) has invalid quantities")

        if self.founders < 0:
            fail("population.founders cannot be negative")
        if self.starts and sum(s.founders for s in self.starts) != self.founders:
            fail("population.starts founders must add up to population.founders")
        for s in self.starts:
            if not (0 <= s.x < self.width and 0 <= s.y < self.height):
                fail(f"start ({s.x},{s.y}) is outside the map")
        if self.founders > 500:
            fail("population.founders must be 500 or fewer")
        if len(set(self.names)) != len(self.names):
            fail("population.names must be unique")
        if not 0 <= self.start_hour < 24:
            fail("time.start_hour must be 0-23")
        if self.ticks_per_day not in (1, 2, 3, 4, 6, 8, 12, 24):
            fail("time.ticks_per_day must divide 24 (24 = hours, 4 = watches)")
        if self.save_every_ticks < 1:
            fail("time.save_every_ticks must be at least 1")
        if self.days_per_year < 4 or self.days_per_year % 4:
            fail("time.days_per_year must be a multiple of 4 (four seasons)")
        for e in self.events:
            if e.kind not in EVENT_KINDS:
                fail(f"events.kind must be one of {sorted(EVENT_KINDS)}, got {e.kind!r}")
            if e.day < 1 or e.days < 1 or e.target < 1:
                fail("events need day >= 1, days >= 1 and target >= 1")
            if e.kind == "famine" and e.target > len(self.food):
                fail(f"famine target {e.target}: there are only {len(self.food)} food patches")
