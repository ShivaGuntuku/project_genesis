from dataclasses import dataclass, field

from genesis.world.world import Position, TerrainType


@dataclass
class Observation:
    tick: int
    position: Position
    terrain: TerrainType
    visible_food: list = field(default_factory=list)
    # Shore tiles (walkable, next to water) the agent can see.
    visible_water: list = field(default_factory=list)
    # [{"id", "name", "position", "looks": ["thirsty", ...], "carrying": {"food": bool, ...}}]
    visible_agents: list = field(default_factory=list)
    # Every food patch in view, even bare ones (a bare bush is still a bush).
    visible_patches: list = field(default_factory=list)
    # Every tile inside the vision square (used to track explored area).
    visible_tiles: list = field(default_factory=list)
    visible_set: frozenset = frozenset()  # the same tiles as a set (empty: build it)

    def tile_set(self):
        return self.visible_set or frozenset(self.visible_tiles)


class PerceptionSystem:
    def __init__(self, vision_radius: int = 3):
        self.vision_radius = vision_radius

    def observe(self, agent, world, agents, tick: int) -> Observation:
        """Build what `agent` can see. Must be called before anyone acts this tick."""
        r = self.vision_radius
        px, py = agent.position.x, agent.position.y

        min_x, max_x = max(0, px - r), min(world.width - 1, px + r)
        min_y, max_y = max(0, py - r), min(world.height - 1, py + r)

        tiles, tile_set = _square(min_x, max_x, min_y, max_y)
        meal = world.rules.food_per_meal
        patches = [  # every food patch in view (S2.6.6: the comparisons inline)
            f
            for f in world.food_sources
            if min_x <= f.position.x <= max_x and min_y <= f.position.y <= max_y
        ]
        # Patches with less than a meal are not worth noticing.
        visible_food = [f for f in patches if f.quantity >= meal]

        shore, dry = world.shore_lakes, world.dry_lakes  # what can_drink_at asks, inline
        visible_water = [
            p
            for p in tiles
            if (lakes := shore.get(p)) is not None and (not dry or bool(lakes - dry))
        ]

        seek = world.rules.seek_threshold
        visible_agents = [
            {
                "id": other.id,
                "name": other.name,
                "position": other.position,
                # What anyone can see: how they look, and whether they carry food or water.
                "looks": [
                    word
                    for word, value in (("thirsty", other.thirst), ("hungry", other.hunger))
                    if value >= seek
                ]
                + (  # M8f: with talents (healers), wounds and sickness can be seen too
                    (["hurt"] if other.health < world.rules.tend_below else [])
                    + (["sick"] if other.sick_until is not None else [])
                    if world.rules.talents
                    else []
                ),
                "carrying": {k: v > 0 for k, v in other.inventory.items()},
            }
            for other in agents
            if other is not agent
            and other.alive
            and min_x <= other.position.x <= max_x
            and min_y <= other.position.y <= max_y
        ]

        return Observation(
            tick=tick,
            position=agent.position,
            terrain=world.get_terrain(agent.position),
            visible_food=visible_food,
            visible_water=visible_water,
            visible_agents=visible_agents,
            visible_patches=[f.position for f in patches],
            visible_tiles=tiles,  # type: ignore[arg-type]  # shared: read only
            visible_set=tile_set,
        )


_SQUARES: dict[tuple[int, int, int, int], tuple[tuple[Position, ...], frozenset]] = {}


def _square(min_x: int, max_x: int, min_y: int, max_y: int):
    """The tiles of a square, x then y (S2.6.6: made once and kept; a long run sees a few
    thousand different squares)."""
    key = (min_x, max_x, min_y, max_y)
    tiles = _SQUARES.get(key)
    if tiles is None:
        if len(_SQUARES) > 200_000:
            _SQUARES.clear()
        square = tuple(
            Position(x, y) for x in range(min_x, max_x + 1) for y in range(min_y, max_y + 1)
        )
        tiles = _SQUARES[key] = (square, frozenset(square))
    return tiles
