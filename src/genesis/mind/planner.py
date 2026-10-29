from genesis.world.world import Position


def leashes(agent, world) -> list:
    """M8e: [(known spots of a need, how far from them it is safe to be)]: half the hours
    left before that need turns urgent (walking is an hour a tile). Off unless the world's
    `explore_leash` rule is on; nothing to keep to before anything is known."""
    r = world.rules
    if not r.explore_leash:
        return []
    out = []
    for resource, level, rate in (
        ("water", agent.thirst, r.thirst_per_tick),
        ("food", agent.hunger, r.hunger_per_tick),
    ):
        spots = [k.position for k in agent.memory.get_known_locations(resource)]
        if spots and rate > 0:
            hours = max(0.0, r.urgent_threshold - level) / rate
            out.append((spots, max(3, int(hours / 2))))
    return out


def within_leash(agent, world, target) -> bool:
    return all(
        min(target.distance(p) for p in spots) <= limit for spots, limit in leashes(agent, world)
    )


class NavigationPlanner:
    """Shortest path on the grid, routing around water and peaks.

    A* with the Manhattan distance (exact for 4-way steps), so on a big map only the tiles
    towards the goal are searched (M8.5 speed). Ties are broken by when a tile was reached,
    so the same start and goal always give the same path."""

    def find_path(self, start: Position, goal: Position, world):
        if start == goal:
            return [start]
        if not world.is_walkable(goal):
            return None

        import heapq

        gx, gy = goal.x, goal.y
        adjacent_of = world.adjacent
        came_from = {start: None}
        cost = {start: 0}
        order = 0
        frontier = [(abs(start.x - gx) + abs(start.y - gy), 0, order, start)]
        while frontier:
            _, g, _, current = heapq.heappop(frontier)
            if current == goal:
                break
            if g > cost[current]:
                continue
            adjacent = adjacent_of.get(current)
            if adjacent is None:
                adjacent = [n for n in current.neighbors() if world.is_walkable(n)]
            for neighbor in adjacent:
                step = g + 1
                if step >= cost.get(neighbor, 1 << 30):
                    continue
                cost[neighbor] = step
                came_from[neighbor] = current
                order += 1
                h = abs(neighbor.x - gx) + abs(neighbor.y - gy)
                heapq.heappush(frontier, (step + h, step, order, neighbor))
        if goal not in came_from:
            return None

        path = [goal]
        while path[-1] != start:
            path.append(came_from[path[-1]])
        path.reverse()
        return path


class ExplorationPlanner:
    """Picks where to explore next: the spot that reveals the most unseen tiles."""

    def __init__(self, vision_radius: int = 3, search_radius: int = 8):
        self.vision_radius = vision_radius
        self.search_radius = search_radius

    def choose_target(self, agent, world):
        """The tile within `search_radius` that reveals most unseen tiles for the walk
        (gain - 1.5 × distance), ties to the smallest x, then y. Vectorised (M8a)."""
        import numpy as np

        memory = agent.memory
        origin = agent.position
        r, v = self.search_radius, self.vision_radius
        table = self._unseen_table(world, memory)

        xs = np.arange(origin.x - r, origin.x + r + 1)
        ys = np.arange(origin.y - r, origin.y + r + 1)
        inside_x = (xs >= 0) & (xs < world.width)
        inside_y = (ys >= 0) & (ys < world.height)
        xs, ys = xs[inside_x], ys[inside_y]
        if len(xs) and len(ys):
            gx, gy = np.meshgrid(xs, ys, indexing="ij")
            x0, x1 = np.maximum(0, gx - v), np.minimum(world.width - 1, gx + v)
            y0, y1 = np.maximum(0, gy - v), np.minimum(world.height - 1, gy + v)
            gain = table[x1 + 1, y1 + 1] - table[x0, y1 + 1] - table[x1 + 1, y0] + table[x0, y0]
            walkable = _walkable_grid(world)[gx, gy]
            ok = walkable & (gain > 0) & ~((gx == origin.x) & (gy == origin.y))
            for spots, limit in leashes(agent, world):  # M8e: never too far to get back
                near = np.full(gx.shape, 10**6)
                for p in spots:
                    near = np.minimum(near, np.abs(gx - p.x) + np.abs(gy - p.y))
                ok &= near <= limit
            if ok.any():
                score = gain - 1.5 * (np.abs(gx - origin.x) + np.abs(gy - origin.y))
                score = np.where(ok, score, -np.inf)
                best = score.max()
                ties = np.argwhere(score == best)  # in (x, y) order: smallest x, then y
                i, j = ties[0]
                return Position(int(gx[i, j]), int(gy[i, j]))

        # Nothing unseen nearby: head for the closest unseen walkable tile anywhere (that is
        # within the leash).
        return self._nearest_unseen(origin, world, memory, leashes(agent, world))

    @staticmethod
    def _unseen_table(world, memory):
        """Summed-area table of unseen tiles: any square's count in four lookups (M8a speed-up;
        the counts are exactly those of unseen_around)."""
        import numpy as np

        grid = (~memory.seen_grid(world.width, world.height)).astype(np.int32)
        table = np.zeros((world.width + 1, world.height + 1), dtype=np.int32)
        table[1:, 1:] = grid.cumsum(0).cumsum(1)
        return table

    def _count(self, table, center, world) -> int:
        v = self.vision_radius
        x0, x1 = max(0, center.x - v), min(world.width - 1, center.x + v)
        y0, y1 = max(0, center.y - v), min(world.height - 1, center.y + v)
        if x0 > x1 or y0 > y1:
            return 0
        return int(table[x1 + 1, y1 + 1] - table[x0, y1 + 1] - table[x1 + 1, y0] + table[x0, y0])

    def unseen_around(self, center, world, memory):
        v = self.vision_radius
        seen = memory.seen_positions
        x0, x1 = max(0, center.x - v), min(world.width - 1, center.x + v)
        y0, y1 = max(0, center.y - v), min(world.height - 1, center.y + v)
        return sum(
            1 for x in range(x0, x1 + 1) for y in range(y0, y1 + 1) if Position(x, y) not in seen
        )

    @staticmethod
    def _nearest_unseen(origin, world, memory, limits=()):
        """The closest unseen walkable tile (ties: smallest x, then y). Vectorised (M8e)."""
        import numpy as np

        unseen = _walkable_grid(world) & ~memory.seen_grid(world.width, world.height)
        xs, ys = np.nonzero(unseen)  # in (x, y) order
        if not len(xs):
            return None
        keep = np.ones(len(xs), dtype=bool)
        for spots, limit in limits:
            near = np.full(len(xs), 10**6)
            for p in spots:
                near = np.minimum(near, np.abs(xs - p.x) + np.abs(ys - p.y))
            keep &= near <= limit
        if not keep.any():
            return None
        xs, ys = xs[keep], ys[keep]
        i = int(np.argmin(np.abs(xs - origin.x) + np.abs(ys - origin.y)))  # first of equals
        return Position(int(xs[i]), int(ys[i]))


def _walkable_grid(world):
    grid = getattr(world, "_walkable_grid", None)
    if grid is None:
        import numpy as np

        grid = np.zeros((world.width, world.height), dtype=bool)
        for p in world.walkable:
            grid[p.x, p.y] = True
        world._walkable_grid = grid
    return grid
