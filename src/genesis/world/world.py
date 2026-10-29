from dataclasses import dataclass
from enum import Enum

from genesis.config import WorldConfig
from genesis.rules import Rules
from genesis.simulation.calendar import calendar_of


class TerrainType(Enum):
    PLAINS = "plains"
    FOREST = "forest"
    MOUNTAIN = "mountain"
    WATER = "water"
    PEAK = "peak"  # high ridge: nobody can cross it (M8e)


@dataclass(frozen=True)
class Position:
    x: int
    y: int

    def neighbors(self):
        return [
            Position(self.x + 1, self.y),
            Position(self.x - 1, self.y),
            Position(self.x, self.y + 1),
            Position(self.x, self.y - 1),
        ]

    def distance(self, other: "Position") -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)


@dataclass
class Food:
    id: int
    position: Position
    quantity: int = 100
    max_quantity: int = 100
    regen_per_tick: int = 1
    stage: int = 0  # M8e: dormant until this many people have lived at once (0 = from the start)


class World:
    """The authoritative environment: terrain, resources and the rules that apply here."""

    def __init__(self, config: WorldConfig):
        self.name = config.name
        self.width = config.width
        self.height = config.height
        self.rules: Rules = config.rules
        self.calendar = calendar_of(config)
        # Valleys (M8d): where founders started; trade and money are counted per valley.
        self.regions = [
            (s.name or f"valley {i + 1}", s.x, s.y) for i, s in enumerate(config.starts)
        ]

        self.terrain: dict[Position, TerrainType] = {}
        default = TerrainType(config.default_terrain)

        for x in range(self.width):
            for y in range(self.height):
                self.terrain[Position(x, y)] = default

        for region in config.regions:
            terrain = TerrainType(region.terrain)
            for x in range(max(0, region.x[0]), min(self.width - 1, region.x[1]) + 1):
                for y in range(max(0, region.y[0]), min(self.height - 1, region.y[1]) + 1):
                    self.terrain[Position(x, y)] = terrain

        # Terrain never changes, so walkable tiles, lakes and shores are worked out once.
        self.walkable: frozenset[Position] = frozenset(
            p for p, t in self.terrain.items() if t not in (TerrainType.WATER, TerrainType.PEAK)
        )
        # Walkable neighbours of every tile, in Position.neighbors() order (pathfinding).
        self.adjacent: dict[Position, tuple[Position, ...]] = {
            p: tuple(n for n in p.neighbors() if n in self.walkable) for p in self.terrain
        }
        self.lake_of: dict[Position, int] = self._number_lakes()
        self.lakes = max(self.lake_of.values(), default=0)
        self.shore_lakes: dict[Position, frozenset[int]] = {}
        for p, t in self.terrain.items():
            if t == TerrainType.WATER:
                continue
            near = frozenset(self.lake_of[n] for n in p.neighbors() if n in self.lake_of)
            if near:
                self.shore_lakes[p] = near
        self.shore: frozenset[Position] = frozenset(self.shore_lakes)

        # Culture (M8b): the concept registry and structures, shared with the simulation.
        self.concepts: dict = {}
        self.structures: list[dict] = []
        # Economy (M8d): recent indirect exchanges and the currencies that emerged.
        self.economy: dict = {"indirect": [], "currencies": []}

        # Scheduled world events (M7). Which lakes are dry, which patches can't regrow.
        self.schedule = config.events
        self.dry_lakes: frozenset[int] = frozenset()
        self.famine: frozenset[int] = frozenset()  # food ids
        self.condition_events: dict[str, int] = {}  # "drought:1" -> event that started it

        self.food_sources = [
            Food(
                id=i + 1,
                position=Position(f.x, f.y),
                quantity=f.quantity,
                max_quantity=f.max_quantity,
                regen_per_tick=f.regen_per_tick,
                stage=f.stage,
            )
            for i, f in enumerate(config.food)
        ]
        self.peak_population = 0  # the most people alive at once (wakes dormant patches)

        # Valleys and disasters (M8e).
        self.region_names = [name for name, _, _ in self.regions] or ["valley"]
        self.region_at: dict[Position, str] = self._valleys()
        self.food_region = {
            f.id: self.region_at.get(f.position, "valley") for f in self.food_sources
        }
        sizes: dict[int, int] = {}
        for lake in self.lake_of.values():
            sizes[lake] = sizes.get(lake, 0) + 1
        self.lakes_in: dict[str, list[int]] = {}
        for lake in sorted(sizes, key=lambda k: (-sizes[k], k)):
            shore = next((s for s, ls in self.shore_lakes.items() if lake in ls), None)
            region = (
                self.region_at.get(shore, self.region_names[0]) if shore else self.region_names[0]
            )
            self.lakes_in.setdefault(region, []).append(lake)
        self.forest: frozenset[Position] = frozenset(
            p for p, t in self.terrain.items() if t == TerrainType.FOREST
        )
        from genesis.world import disasters

        self.disasters = disasters.schedule(
            config.seed, self.rules, self.calendar.days_per_year, self.region_names
        )
        self.disaster_events: dict[int, int] = {}  # disaster id -> event that started it
        self.burned: frozenset[Position] = frozenset()
        self.regrow: dict[str, float] = {}  # region -> berry growth factor now
        self.troubled: frozenset[str] = frozenset()  # valleys people want to leave now

    # --------------------------------
    # Terrain
    # --------------------------------

    def is_valid_position(self, position: Position) -> bool:
        return 0 <= position.x < self.width and 0 <= position.y < self.height

    def get_terrain(self, position: Position) -> TerrainType:
        return self.terrain[position]

    def is_walkable(self, position: Position) -> bool:
        return position in self.walkable

    def is_water(self, position: Position) -> bool:
        return self.is_valid_position(position) and self.get_terrain(position) == TerrainType.WATER

    def can_drink_at(self, position: Position) -> bool:
        """True if a walkable tile touches water that hasn't dried up."""
        lakes = self.shore_lakes.get(position)
        if lakes is None:
            return False
        return not self.dry_lakes or bool(lakes - self.dry_lakes)

    def rebuild_terrain(self):
        """After a God edit of the land (M9): work out again everything that follows from it
        (walkable tiles, lakes, shores, valleys, woods) and drop cached grids."""
        self.walkable = frozenset(
            p for p, t in self.terrain.items() if t not in (TerrainType.WATER, TerrainType.PEAK)
        )
        self.adjacent = {
            p: tuple(n for n in p.neighbors() if n in self.walkable) for p in self.terrain
        }
        self.lake_of = self._number_lakes()
        self.lakes = max(self.lake_of.values(), default=0)
        self.shore_lakes = {}
        for p, t in self.terrain.items():
            if t == TerrainType.WATER:
                continue
            near = frozenset(self.lake_of[n] for n in p.neighbors() if n in self.lake_of)
            if near:
                self.shore_lakes[p] = near
        self.shore = frozenset(self.shore_lakes)
        self.region_at = self._valleys()
        self.food_region = {
            f.id: self.region_at.get(f.position, "valley") for f in self.food_sources
        }
        sizes: dict[int, int] = {}
        for lake in self.lake_of.values():
            sizes[lake] = sizes.get(lake, 0) + 1
        self.lakes_in = {}
        for lake in sorted(sizes, key=lambda k: (-sizes[k], k)):
            shore = next((s for s, ls in self.shore_lakes.items() if lake in ls), None)
            region = (
                self.region_at.get(shore, self.region_names[0]) if shore else self.region_names[0]
            )
            self.lakes_in.setdefault(region, []).append(lake)
        self.forest = (
            frozenset(p for p, t in self.terrain.items() if t == TerrainType.FOREST) | self.burned
        )
        for cache in ("_walkable_grid", "_material_grids"):
            if hasattr(self, cache):
                delattr(self, cache)

    def _valleys(self) -> dict[Position, str]:
        """Each tile's valley: the founders' start nearest on foot (ties: the first start)."""
        if not self.regions:
            return {p: "valley" for p in self.terrain}
        out: dict[Position, str] = {}
        frontier = []
        for name, x, y in self.regions:
            p = Position(x, y)
            if p not in out:
                out[p] = name
                frontier.append(p)
        while frontier:
            nxt = []
            for p in frontier:
                for n in self.adjacent.get(p, ()):
                    if n not in out:
                        out[n] = out[p]
                        nxt.append(n)
            frontier = nxt
        for p in self.terrain:  # water and peaks: the nearest start as the crow flies
            if p not in out:
                out[p] = min(self.regions, key=lambda s: abs(s[1] - p.x) + abs(s[2] - p.y))[0]
        return out

    def near_water(self, position: Position, distance: int) -> bool:
        return any(
            self.is_water(Position(position.x + dx, position.y + dy))
            for dx in range(-distance, distance + 1)
            for dy in range(-distance, distance + 1)
        )

    def near_forest(self, position: Position) -> bool:
        return any(
            Position(position.x + dx, position.y + dy) in self.forest
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
        )

    def set_burned(self, burned: frozenset):
        """Woods burned by wildfire are open ground until they grow back."""
        if burned == self.burned:
            return
        for p in self.burned - burned:
            self.terrain[p] = TerrainType.FOREST
        for p in burned:
            self.terrain[p] = TerrainType.PLAINS
        self.burned = burned

    def _number_lakes(self) -> dict[Position, int]:
        """Connected water areas, numbered 1, 2, ... scanning columns left to right."""
        numbers: dict[Position, int] = {}
        count = 0
        for x in range(self.width):
            for y in range(self.height):
                start = Position(x, y)
                if self.terrain[start] != TerrainType.WATER or start in numbers:
                    continue
                count += 1
                stack = [start]
                numbers[start] = count
                while stack:
                    p = stack.pop()
                    for n in p.neighbors():
                        if self.is_water(n) and n not in numbers:
                            numbers[n] = count
                            stack.append(n)
        return numbers

    def conditions_on(self, day: int) -> tuple[frozenset[int], frozenset[int]]:
        """(dry lakes, food ids in famine) on a given day, from the schedule."""
        dry, famine = set(), set()
        for e in self.schedule:
            if e.day <= day < e.day + e.days:
                if e.kind == "drought":
                    dry.add(e.target)
                elif e.target <= len(self.food_sources):
                    famine.add(self.food_sources[e.target - 1].id)
        return frozenset(dry), frozenset(famine)

    # --------------------------------
    # Resources
    # --------------------------------

    def get_food_at(self, position: Position, min_quantity: int | None = None):
        """Food here with at least `min_quantity` units (default: one full meal)."""
        if min_quantity is None:
            min_quantity = self.rules.food_per_meal

        for food in self.food_sources:
            if food.position == position and food.quantity >= min_quantity:
                return food

        return None

    def update(self, tick: int = 0):
        """Advance the world by one tick (resource regrowth: per hour × hours per tick,
        times the season's factor in worlds with seasons)."""
        factor = self.calendar.hours_per_tick
        if self.calendar.seasons:
            r = self.rules
            factor *= (r.regen_spring, r.regen_wet, r.regen_dry, r.regen_cold)[
                self.calendar.season_index(tick)
            ]
        for food in self.food_sources:
            if food.id in self.famine or food.stage > self.peak_population:
                continue
            f = factor * self.regrow.get(self.food_region[food.id], 1.0) if self.regrow else factor
            grown = food.regen_per_tick if f == 1 else round(food.regen_per_tick * f)
            food.quantity = min(food.max_quantity, food.quantity + grown)
