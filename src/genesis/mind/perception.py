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


class PerceptionSystem:
    def __init__(self, vision_radius: int = 3):
        self.vision_radius = vision_radius

    def observe(self, agent, world, agents, tick: int) -> Observation:
        """Build what `agent` can see. Must be called before anyone acts this tick."""
        r = self.vision_radius
        px, py = agent.position.x, agent.position.y

        min_x, max_x = max(0, px - r), min(world.width - 1, px + r)
        min_y, max_y = max(0, py - r), min(world.height - 1, py + r)

        def in_view(p: Position) -> bool:
            return min_x <= p.x <= max_x and min_y <= p.y <= max_y

        tiles = [Position(x, y) for x in range(min_x, max_x + 1) for y in range(min_y, max_y + 1)]

        visible_food = [
            food
            for food in world.food_sources
            # Patches with less than a meal are not worth noticing.
            if food.quantity >= world.rules.food_per_meal and in_view(food.position)
        ]

        visible_water = [p for p in tiles if world.can_drink_at(p)]

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
            if other is not agent and other.alive and in_view(other.position)
        ]

        return Observation(
            tick=tick,
            position=agent.position,
            terrain=world.get_terrain(agent.position),
            visible_food=visible_food,
            visible_water=visible_water,
            visible_agents=visible_agents,
            visible_patches=[f.position for f in world.food_sources if in_view(f.position)],
            visible_tiles=tiles,
        )
