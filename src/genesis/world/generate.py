"""Generated maps (M8e): 2-3 valleys side by side, separated by ridges, joined by passes.

Everything comes from the world seed, so the same seed always gives the same land. Each
valley gets a big lake and a small one, woods, rocky hills (stone and flint), and berry
patches. Some patches start dormant and wake when the whole population reaches their
`stage` (the land "opens up" as people grow in number: staged growth, see docs/15 15.6).
"""

import random
from collections import deque

VALLEY_NAMES = {
    1: ["the valley"],
    2: ["west valley", "east valley"],
    3: ["west valley", "middle valley", "east valley"],
}
RIDGE = 4  # tiles of mountain between valleys
PASS = 3  # tiles of open ground through a ridge
STAGES = (0, 0, 0, 0, 20, 30, 40)  # one per patch in a valley: wakes at this many living


def valleys(
    seed: int, width: int, height: int, count: int, founders: int, passes: bool = True
) -> dict:
    """Regions, food and starts for a WorldConfig, from the seed. Tries until every start
    and patch can be reached from every other."""
    if count not in VALLEY_NAMES:
        raise ValueError("world.valleys must be 1, 2 or 3")
    for attempt in range(50):
        rng = random.Random(f"map:{seed}:{attempt}")
        layout = _layout(rng, width, height, count, founders, passes)
        if layout is not None and _connected(layout, width, height, passes):
            return layout
    raise ValueError("could not generate a connected map; try another seed or a bigger map")


def _layout(rng, width, height, count, founders, passes=True) -> dict:
    regions, food, starts = [], [], []
    band = width // count
    for k in range(1, count):  # ridges with a pass each
        x = k * band - RIDGE // 2
        regions.append({"terrain": "peak", "x": [x, x + RIDGE - 1], "y": [0, height - 1]})
        py = rng.randint(height // 5, height * 4 // 5 - PASS)
        if passes:  # P3 (M8.5): isolated valleys have no pass at all
            regions.append({"terrain": "plains", "x": [x, x + RIDGE - 1], "y": [py, py + PASS - 1]})

    for v in range(count):
        x0 = v * band + (RIDGE // 2 + 1 if v else 1)
        x1 = (v + 1) * band - (RIDGE // 2 + 2) if v < count - 1 else width - 2
        bounds = (x0, 1, x1, height - 2)
        taken: list[tuple[int, int, int, int]] = []
        lake = _place(rng, bounds, taken, rng.randint(5, 8), rng.randint(4, 7), margin=4)
        if lake is None:
            return None
        parts = [("water", lake)]
        parts.append(("water", _place(rng, bounds, taken, rng.randint(2, 3), rng.randint(2, 3), 3)))
        for _ in range(rng.randint(2, 3)):
            parts.append(
                ("forest", _place(rng, bounds, taken, rng.randint(4, 9), rng.randint(3, 7)))
            )
        for _ in range(rng.randint(1, 2)):
            parts.append(
                ("mountain", _place(rng, bounds, taken, rng.randint(2, 4), rng.randint(2, 4)))
            )
        for terrain, box in parts:
            if box is not None:
                regions.append({"terrain": terrain, "x": [box[0], box[2]], "y": [box[1], box[3]]})

        # Start beside the big lake, on open ground; berries around it.
        sx = lake[2] + 3 if lake[2] + 3 <= x1 else lake[0] - 3
        sx = min(x1, max(x0, sx))
        sy = (lake[1] + lake[3]) // 2
        starts.append({"name": VALLEY_NAMES[count][v], "x": sx, "y": sy, "radius": 4})
        regen = rng.choice((3, 3, 4, 4))  # valleys differ in how rich they are
        for stage in STAGES:
            spot = _berry_spot(rng, bounds, taken, food, sx, sy, 5 if stage == 0 else 14)
            if spot is not None:
                food.append(
                    {
                        "x": spot[0],
                        "y": spot[1],
                        "quantity": 100 if stage == 0 else 0,
                        "regen_per_tick": regen,
                        "stage": stage,
                    }
                )

    per = [founders // count + (1 if v < founders % count else 0) for v in range(count)]
    for s, n in zip(starts, per, strict=True):
        s["founders"] = n
    return {"regions": regions, "food": food, "starts": starts}


def _place(rng, bounds, taken, w, h, margin=2):
    """A free w x h box inside bounds, at least `margin` from the others (or None)."""
    x0, y0, x1, y1 = bounds
    for _ in range(200):
        x = rng.randint(x0, max(x0, x1 - w + 1))
        y = rng.randint(y0, max(y0, y1 - h + 1))
        box = (x - margin, y - margin, x + w - 1 + margin, y + h - 1 + margin)
        if all(not _overlap(box, t) for t in taken):
            taken.append((x, y, x + w - 1, y + h - 1))
            return taken[-1]
    return None


def _berry_spot(rng, bounds, taken, food, sx, sy, near):
    x0, y0, x1, y1 = bounds
    for _ in range(200):
        fx = rng.randint(max(x0, sx - near), min(x1, sx + near))
        fy = rng.randint(max(y0, sy - near), min(y1, sy + near))
        if any(t[0] - 1 <= fx <= t[2] + 1 and t[1] - 1 <= fy <= t[3] + 1 for t in taken):
            continue
        if abs(fx - sx) + abs(fy - sy) < 3:
            continue
        if any(abs(fx - f["x"]) + abs(fy - f["y"]) < 4 for f in food):
            continue
        return fx, fy
    return None


def _overlap(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def terrain_grid(regions, width, height, default="plains") -> list[list[str]]:
    grid = [[default] * height for _ in range(width)]
    for r in regions:
        for x in range(max(0, r["x"][0]), min(width - 1, r["x"][1]) + 1):
            for y in range(max(0, r["y"][0]), min(height - 1, r["y"][1]) + 1):
                grid[x][y] = r["terrain"]
    return grid


def _connected(layout, width, height, passes=True) -> bool:
    """Every start and patch reachable from the first start (with passes), or, for isolated
    valleys, from its own valley's start."""
    if not passes:
        band = width // len(layout["starts"])
        return all(
            _connected(
                {
                    "regions": layout["regions"],
                    "starts": [s],
                    "food": [f for f in layout["food"] if f["x"] // band == s["x"] // band],
                },
                width,
                height,
            )
            for s in layout["starts"]
        )
    grid = terrain_grid(layout["regions"], width, height)
    spots = [(s["x"], s["y"]) for s in layout["starts"]] + [
        (f["x"], f["y"]) for f in layout["food"]
    ]
    if any(grid[x][y] in ("water", "peak") for x, y in spots):
        return False
    seen = {spots[0]}
    queue = deque([spots[0]])
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < width and 0 <= ny < height and (nx, ny) not in seen:
                if grid[nx][ny] not in ("water", "peak"):
                    seen.add((nx, ny))
                    queue.append((nx, ny))
    return all(s in seen for s in spots)
