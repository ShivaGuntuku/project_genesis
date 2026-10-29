from dataclasses import dataclass
from enum import Enum

from genesis.society.social_memory import SocialMemory
from genesis.world.world import Position


class ExplorationGoal(Enum):
    GENERAL = "general"
    FIND_FOOD = "find_food"
    FIND_WATER = "find_water"


@dataclass(frozen=True)
class KnownLocation:
    resource_type: str
    position: Position


class AgentMemory:
    """Everything one agent knows. Never shared between agents."""

    def __init__(self):
        self.known_locations: list[KnownLocation] = []
        self._known: set[KnownLocation] = set()  # same contents, for fast lookups
        # Every resource location ever learned (kept even after forgetting it as useful).
        self.discovered: set[KnownLocation] = set()
        self.events: list[int] = []  # ids of events this agent took part in
        self.visited_positions: set[Position] = set()
        self.seen_positions: set[Position] = set()
        self._seen_grid = None  # numpy copy of seen_positions, kept in step (M8e speed)
        self._seen_new: list[Position] = []

        self.exploration_target: Position | None = None
        self.exploration_goal: ExplorationGoal | None = None

        # Keyed by the other agent's id.
        self.social_memory: dict[int, SocialMemory] = {}

        # Locations learned from others, not yet seen with own eyes:
        # (resource, x, y) -> (teller_id, message_event_id)
        self.told: dict[tuple[str, int, int], tuple[int, int]] = {}

    # --------------------------------
    # Resource memory
    # --------------------------------

    def remember_location(self, resource_type: str, position: Position, seen: bool = True) -> bool:
        """Returns True the first time this location is seen with one's own eyes.

        seen=False: only heard about it (it may not even be true).
        """
        location = KnownLocation(resource_type, position)
        first_time = seen and location not in self.discovered
        if seen:
            self.discovered.add(location)

        if location not in self._known:
            self.known_locations.append(location)
            self._known.add(location)

        return first_time

    def knows_location(self, resource_type: str, position: Position) -> bool:
        return KnownLocation(resource_type, position) in self._known

    def forget_location(self, resource_type: str, position: Position):
        gone = KnownLocation(resource_type, position)
        self.known_locations = [location for location in self.known_locations if location != gone]
        self._known.discard(gone)

    def get_known_locations(self, resource_type: str | None = None):
        if resource_type is None:
            return list(self.known_locations)

        return [
            location for location in self.known_locations if location.resource_type == resource_type
        ]

    # --------------------------------
    # Event memory
    # --------------------------------

    def remember_event(self, event_id: int):
        self.events.append(event_id)

    # --------------------------------
    # Spatial memory
    # --------------------------------

    def remember_position(self, position: Position):
        self.visited_positions.add(position)

    def remember_seen(self, positions) -> int:
        """Returns how many of them were new."""
        new = [p for p in positions if p not in self.seen_positions]
        if self._seen_grid is not None:
            self._seen_new.extend(new)
        self.seen_positions.update(new)
        return len(set(new))

    def seen_grid(self, width: int, height: int):
        """Seen tiles as a bool array [x, y]. The set only grows, so the copy stays exact."""
        import numpy as np

        grid = self._seen_grid
        if grid is None or grid.shape != (width, height):
            grid = np.zeros((width, height), dtype=bool)
            new = self.seen_positions
        else:
            new = self._seen_new
        for p in new:
            if 0 <= p.x < width and 0 <= p.y < height:
                grid[p.x, p.y] = True
        self._seen_grid, self._seen_new = grid, []
        return grid

    def set_exploration_goal(self, goal: ExplorationGoal):
        if goal != self.exploration_goal:
            self.exploration_target = None

        self.exploration_goal = goal

    def get_exploration_goal(self):
        return self.exploration_goal

    def clear_exploration_goal(self):
        self.exploration_goal = None
        self.exploration_target = None

    def set_exploration_target(self, position: Position):
        self.exploration_target = position

    def get_exploration_target(self):
        return self.exploration_target

    def clear_exploration_target(self):
        self.exploration_target = None

    # --------------------------------
    # Social memory
    # --------------------------------

    def knows_agent(self, agent_id: int) -> bool:
        return agent_id in self.social_memory

    def remember_agent(
        self,
        agent_id: int,
        name: str,
        position: Position,
        tick: int,
        met_event_id: int | None = None,
    ):
        """Returns True if this is the first meeting or a new encounter after being apart."""
        if agent_id not in self.social_memory:
            self.social_memory[agent_id] = SocialMemory(
                agent_id=agent_id,
                name=name,
                last_position=(position.x, position.y),
                first_seen=tick,
                last_seen=tick,
                met_event_id=met_event_id,
            )
            return True
        return self.social_memory[agent_id].record_encounter((position.x, position.y), tick)

    # --------------------------------
    # Saving and loading
    # --------------------------------

    def to_dict(self) -> dict:
        def pos(p):
            return [p.x, p.y] if p is not None else None

        return {
            "known_locations": [
                [k.resource_type, k.position.x, k.position.y] for k in self.known_locations
            ],
            "discovered": sorted(
                [k.resource_type, k.position.x, k.position.y] for k in self.discovered
            ),
            "events": list(self.events),
            "visited": sorted([p.x, p.y] for p in self.visited_positions),
            "seen": sorted([p.x, p.y] for p in self.seen_positions),
            "exploration_target": pos(self.exploration_target),
            "exploration_goal": self.exploration_goal.value if self.exploration_goal else None,
            "social": [m.to_dict() for m in self.social_memory.values()],
            "told": [[*key, *value] for key, value in self.told.items()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentMemory":
        memory = cls()
        memory.known_locations = [
            KnownLocation(t, Position(x, y)) for t, x, y in data["known_locations"]
        ]
        memory._known = set(memory.known_locations)
        memory.discovered = {KnownLocation(t, Position(x, y)) for t, x, y in data["discovered"]}
        memory.events = list(data["events"])
        memory.visited_positions = {Position(x, y) for x, y in data["visited"]}
        memory.seen_positions = {Position(x, y) for x, y in data["seen"]}
        target = data["exploration_target"]
        memory.exploration_target = Position(*target) if target else None
        goal = data["exploration_goal"]
        memory.exploration_goal = ExplorationGoal(goal) if goal else None
        memory.told = {(t, x, y): (teller, msg) for t, x, y, teller, msg in data["told"]}
        for m in data["social"]:
            social = SocialMemory.from_dict(m)
            memory.social_memory[social.agent_id] = social
        return memory
