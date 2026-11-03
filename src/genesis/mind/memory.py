from collections.abc import Iterable
from enum import Enum
from typing import NamedTuple

from genesis.society.social_memory import SocialMemory
from genesis.world.world import Position


class ExplorationGoal(Enum):
    GENERAL = "general"
    FIND_FOOD = "find_food"
    FIND_WATER = "find_water"


EVENTS_KEPT = 300


class KnownLocation(NamedTuple):  # S2.6.6: a named tuple, same hash as the dataclass it was
    resource_type: str
    position: Position


class AgentMemory:
    """Everything one agent knows. Never shared between agents."""

    def __init__(self):
        self.known_locations: list[KnownLocation] = []
        self._known: set[KnownLocation] = set()  # same contents, for fast lookups
        # S2.6.6: steps from every tile to the nearest known place, per resource:
        # resource -> [grid, places learned since it was made]. Dropped when one is forgotten.
        self._near: dict[str, list] = {}
        # Every resource location ever learned (kept even after forgetting it as useful).
        self.discovered: set[KnownLocation] = set()
        self.events: list[int] = []  # ids of events this agent took part in
        self.visited_positions: set[Position] = set()
        self.seen_positions: set[Position] = set()
        self._seen_grid = None  # numpy copy of seen_positions, kept in step (M8e speed)
        self._seen_new: list[Position] = []
        self._pack_cache: dict[str, tuple] = {}  # name -> (the set, its size, packed)
        self._last_seen: tuple | None = None  # (view, the set, its size) after the last look

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
            near = self._near.get(resource_type)
            if near is not None:
                near[1].append(position)

        return first_time

    def knows_location(self, resource_type: str, position: Position) -> bool:
        return KnownLocation(resource_type, position) in self._known

    def forget_location(self, resource_type: str, position: Position):
        gone = KnownLocation(resource_type, position)
        self.known_locations = [location for location in self.known_locations if location != gone]
        self._known.discard(gone)
        self._near.pop(resource_type, None)

    def near_grid(self, resource_type: str, width: int, height: int):
        """Manhattan steps from each tile to the nearest known place of this resource (the
        same numbers as checking every place; kept up to date as places are learned)."""
        import numpy as np

        near = self._near.get(resource_type)
        if near is None or near[0].shape != (width, height):
            grid = np.full((width, height), 10**6, dtype=np.int64)
            places = [k.position for k in self.known_locations if k.resource_type == resource_type]
            near = self._near[resource_type] = [grid, places]
        if near[1]:
            xs, ys = _indices(width, height)
            grid = near[0]
            for p in near[1]:
                np.minimum(grid, np.abs(xs - p.x) + np.abs(ys - p.y), out=grid)
            near[1] = []
        return near[0]

    def forget_many(self, gone: list):
        """Forget several known locations at once (the order of the rest is kept)."""
        if not gone:
            return
        drop = set(gone)
        self.known_locations = [k for k in self.known_locations if k not in drop]
        self._known -= drop
        for k in drop:
            self._near.pop(k.resource_type, None)

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
        """The newest EVENTS_KEPT events this agent took part in (S2.6.6: the whole list was
        rewritten every day and only grew; the event_agents table keeps them all)."""
        self.events.append(event_id)
        if len(self.events) > 2 * EVENTS_KEPT:
            del self.events[:-EVENTS_KEPT]

    # --------------------------------
    # Spatial memory
    # --------------------------------

    def remember_position(self, position: Position):
        self.visited_positions.add(position)

    def remember_seen(self, positions) -> int:
        """Returns how many of them were new."""
        seen = self.seen_positions
        last = self._last_seen
        if last is not None and last[0] is positions and last[1] is seen and last[2] == len(seen):
            return 0  # S2.6.6: the same view as last time, all of it seen already
        new = [p for p in positions if p not in seen]
        if self._seen_grid is not None:
            self._seen_new.extend(new)
        seen.update(new)
        self._last_seen = (positions, seen, len(seen))  # holds the view, so `is` stays safe
        return len(set(new))

    def seen_grid(self, width: int, height: int):
        """Seen tiles as a bool array [x, y]. The set only grows, so the copy stays exact."""
        import numpy as np

        grid = self._seen_grid
        if grid is None or grid.shape != (width, height):
            grid = np.zeros((width, height), dtype=bool)
            new: Iterable[Position] = self.seen_positions
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
            "events": self.events[-EVENTS_KEPT:],
            "visited": self._packed("visited", self.visited_positions),
            "seen": self._packed("seen", self.seen_positions),
            "exploration_target": pos(self.exploration_target),
            "exploration_goal": self.exploration_goal.value if self.exploration_goal else None,
            "social": [m.to_dict() for m in self.social_memory.values()],
            "told": [[*key, *value] for key, value in self.told.items()],
        }

    def _packed(self, name: str, tiles: set):
        """A set of tiles as a bitmap (S2.6.6: a daily save wrote thousands of pairs per
        person). Made again only when the set has grown; these sets only grow."""
        cache = self._pack_cache.get(name)
        if cache is not None and cache[0] is tiles and cache[1] == len(tiles):
            return cache[2]
        packed = _pack(tiles)
        self._pack_cache[name] = (tiles, len(tiles), packed)
        return packed

    @classmethod
    def from_dict(cls, data: dict) -> "AgentMemory":
        memory = cls()
        memory.known_locations = [
            KnownLocation(t, Position(x, y)) for t, x, y in data["known_locations"]
        ]
        memory._known = set(memory.known_locations)
        memory.discovered = {KnownLocation(t, Position(x, y)) for t, x, y in data["discovered"]}
        memory.events = list(data["events"])
        memory.visited_positions = _unpack(data["visited"])
        memory.seen_positions = _unpack(data["seen"])
        target = data["exploration_target"]
        memory.exploration_target = Position(*target) if target else None
        goal = data["exploration_goal"]
        memory.exploration_goal = ExplorationGoal(goal) if goal else None
        memory.told = {(t, x, y): (teller, msg) for t, x, y, teller, msg in data["told"]}
        for m in data["social"]:
            social = SocialMemory.from_dict(m)
            memory.social_memory[social.agent_id] = social
        return memory


def _pack(tiles: set) -> dict:
    """{"w", "h", "bits"}: a (w, h) grid of the tiles, bit-packed and base64-encoded."""
    import base64

    import numpy as np

    if not tiles:
        return {"w": 0, "h": 0, "bits": ""}
    xs = np.fromiter((p.x for p in tiles), dtype=np.int64, count=len(tiles))
    ys = np.fromiter((p.y for p in tiles), dtype=np.int64, count=len(tiles))
    w, h = int(xs.max()) + 1, int(ys.max()) + 1
    grid = np.zeros((w, h), dtype=bool)
    grid[xs, ys] = True
    return {"w": w, "h": h, "bits": base64.b64encode(np.packbits(grid)).decode()}


def _unpack(data) -> set:
    """The tiles back, added in x-then-y order, as the sorted lists of older saves were (the
    same order gives the same set)."""
    if isinstance(data, list):  # saved before S2.6.6
        return {Position(x, y) for x, y in data}
    import base64

    import numpy as np

    w, h = data["w"], data["h"]
    if not w or not h:
        return set()
    bits = np.unpackbits(np.frombuffer(base64.b64decode(data["bits"]), dtype=np.uint8))
    xs, ys = np.nonzero(bits[: w * h].reshape(w, h).astype(bool))
    return {Position(int(x), int(y)) for x, y in zip(xs.tolist(), ys.tolist(), strict=True)}


_INDICES: dict = {}


def _indices(width: int, height: int):
    """x and y of every tile, as two (width, height) grids."""
    import numpy as np

    if (width, height) not in _INDICES:
        _INDICES[(width, height)] = np.indices((width, height), dtype=np.int64)
    return _INDICES[(width, height)]
