"""A living land (S2.1): woods, berry patches, water and footprints change over the years.

Once a month (`rules.land_every_days`) the land takes a step, at the end of a day:
- **water:** lakes rise in the wet season (a ring of grassland floods) and shrink in the dry
  (their edge becomes open ground); a slow climate drift over centuries makes springs and
  ponds in wet times and dries them up in dry ones;
- **woods:** spread into grassland beside them and die back at their edges, both faster the
  more woods (or open ground) a tile touches; the climate tilts the balance. A wooded tile
  whose wood has all been gathered is cut down and lies bare for a while, then turns to grass;
- **berry patches:** healthy ones seed new patches nearby, new ones appear in good soil near
  water, and a patch stripped bare month after month in the growing seasons dies (so does
  an old one, rarely); a valley that has lost patches grows new ones elsewhere;
- **footprints:** much-walked grass becomes a path (walking path to path is twice as fast);
  much-walked woods become a clearing; paths nobody uses fade.

Every step is deterministic: its own random numbers come from the world seed and the month
(the simulation's random sequence is never used), and the climate is a pure function of the
seed and the month. Everything that has changed is kept in `Land` and saved with the world,
so a resume continues exactly. Lakes keep the numbers of the basins they belong to, so
droughts still hit the right lake. Tiles with someone or something on them never flood.
"""

import math
import random

from genesis.world.world import Food, Position, TerrainType

PLAINS, FOREST, WATER = TerrainType.PLAINS, TerrainType.FOREST, TerrainType.WATER
SEASON_LEVEL = (0, +1, -1, 0)  # spring, wet, dry, cold: the lakes' level against normal
MIN_SHRINK = 16  # lakes smaller than this don't shrink in the dry season
WOODS_SWING = 0.4  # a valley's woods head for (1 + this x climate) times what it had at first
MAX_PONDS = 3  # per valley
REFILL = 0.25  # a valley with fewer patches than at first grows one back (chance a month)


def climate(seed: int, month: int) -> float:
    """-1 (dry) to +1 (wet): two slow waves over the centuries, from the seed alone."""
    rng = random.Random(f"climate:{seed}")
    long, short = rng.uniform(150, 300) * 12, rng.uniform(40, 90) * 12
    a, b = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)
    return 0.75 * math.sin(2 * math.pi * month / long + a) + 0.25 * math.sin(
        2 * math.pi * month / short + b
    )


def _key(p: Position) -> str:
    return f"{p.x},{p.y}"


def _pos(key: str) -> Position:
    x, y = key.split(",")
    return Position(int(x), int(y))


class Land:
    """What the land has become, on top of the generated map."""

    def __init__(self, world, seed: int):
        self.seed = seed
        self.month = 0  # steps taken
        # tile -> [terrain now, terrain before the land changed it, lake number or None]
        self.changed: dict[Position, list] = {}
        self.ponds: dict[Position, int] = {}  # water the climate made: tile -> lake number
        self.bare: dict[Position, int] = {}  # cut or cleared ground: months until grass
        self.paths: dict[Position, int] = {}  # path -> months it has gone little used
        self.walks: dict[Position, int] = {}  # steps on each tile this month
        self.cut: dict[Position, int] = {}  # wood gathered from a wooded tile
        self.low: dict[int, int] = {}  # patch -> ticks stripped bare this month
        self.stress: dict[int, int] = {}  # patch -> months in a row stripped bare
        self.born: dict[int, dict] = {}  # patches the land made: id -> {x, y, regen, max, tick}
        self.dead: dict[int, int] = {}  # patches that died: id -> tick
        self.year: dict = {}  # this year's numbers (for the yearly snapshot)
        self.next_food = max((f.id for f in world.food_sources), default=0) + 1
        # What each valley had at first: woods head back towards it (as the climate allows)
        # and patches that die are replaced, so the land changes but doesn't run out.
        self.woods0: dict[str, int] = {}
        for p, t in world.terrain.items():
            if t == FOREST:
                region = world.region_at.get(p, world.region_names[0])
                self.woods0[region] = self.woods0.get(region, 0) + 1
        self.patches0: dict[str, int] = {}
        for f in world.food_sources:
            if f.stage == 0:
                region = world.food_region.get(f.id, world.region_names[0])
                self.patches0[region] = self.patches0.get(region, 0) + 1
        self.rebase(world)

    def rebase(self, world):
        """The lakes' basins and the grass around them that can flood (again after God)."""
        own = {p for p, c in self.changed.items() if c[0] == WATER}
        self.basin = {p: n for p, n in world.lake_of.items() if p not in own}
        for p, c in self.changed.items():  # lake bed lying dry now: still its basin's
            if c[1] == WATER and c[2] is not None:
                self.basin[p] = c[2]
        self.next_lake = max(world.lakes, *self.ponds.values(), 0) + 1
        self.ring: dict[Position, int] = {}
        for p, n in sorted(self.basin.items(), key=lambda kv: (kv[0].x, kv[0].y)):
            for q in p.neighbors():
                if q in self.ring or q in self.basin or not world.is_valid_position(q):
                    continue
                base = self.changed[q][1] if q in self.changed else world.terrain[q]
                if base != PLAINS or self._edge(world, q):
                    continue
                self.ring[q] = n

    def _edge(self, world, p: Position) -> bool:
        """On the map's border or next to a ridge: flooding there could cut a way through."""
        if p.x in (0, world.width - 1) or p.y in (0, world.height - 1):
            return True
        return any(world.terrain[q] == TerrainType.PEAK for q in p.neighbors())

    # --------------------------------
    # Hooks from the simulation
    # --------------------------------

    def on_path(self, a: Position, b: Position) -> bool:
        return a in self.paths and b in self.paths

    def cut_wood(self, p: Position):
        self.cut[p] = self.cut.get(p, 0) + 1

    # --------------------------------
    # The monthly step
    # --------------------------------

    def step(self, sim, tick: int) -> list[tuple[str, Position, dict]]:
        """One month of change. Returns what is worth an event: [(kind, where, data)]."""
        world, r = sim.world, sim.world.rules
        self.month += 1
        rng = random.Random(f"land:{self.seed}:{self.month}")
        wet = climate(self.seed, self.month)
        season = world.calendar.season_index(tick) if world.calendar.seasons else 0
        busy = {a.position for a in sim.population.get_alive()}
        busy |= {f.position for f in world.food_sources}
        busy |= {Position(s["x"], s["y"]) for s in sim.structures}
        homes = [g for g in sim.groups.values() if g.ended_tick is None]
        for g in homes:  # a group's home and the ground around it never flood
            h = Position(*g.home)
            busy |= {h, *h.neighbors()}
        changes: dict[Position, tuple[TerrainType, int | None]] = {}
        events: list[tuple[str, Position, dict]] = []

        self._water(world, season, wet, busy, changes)
        self._ponds(world, r, rng, wet, busy, changes, events)
        self._woods(world, r, rng, wet, busy, changes)
        self._footprints(world, r, changes)
        self._apply(world, changes)
        if changes:
            self._follow_shores(sim, world)
            self._dry_homes(world, homes)
        self._patches(sim, r, rng, wet, busy, tick, events, growing=season in (0, 1))
        self.low = {}
        self._count(world)
        return events

    def _set(self, world, changes, p: Position, terrain: TerrainType, lake: int | None = None):
        """Change a tile, remembering what it was before the land first changed it."""
        before = self.changed[p][1] if p in self.changed else world.terrain[p]
        if terrain == WATER and lake is None:
            lake = self.basin.get(p) or self.ring.get(p) or self.ponds.get(p)
        if terrain != WATER:
            lake = self.basin.get(p) if before == WATER else None  # a dry lake bed's basin
        if terrain == before:
            self.changed.pop(p, None)
        else:
            self.changed[p] = [terrain, before, lake]
        changes[p] = (terrain, lake if terrain == WATER else None)

    def _water(self, world, season, wet, busy, changes):
        level = SEASON_LEVEL[season]
        if level > 0 and wet < -0.5:
            level = 0  # a dry age: the wet season doesn't flood
        elif level < 0 and wet > 0.5:
            level = 0  # a wet age: the dry season doesn't shrink the lakes
        sizes: dict[int, int] = {}
        for n in self.basin.values():
            sizes[n] = sizes.get(n, 0) + 1
        want = set(self.basin)
        if level < 0:
            want -= {
                p
                for p, n in self.basin.items()
                if sizes[n] >= MIN_SHRINK and any(q not in self.basin for q in p.neighbors())
            }
        elif level > 0:
            want |= set(self.ring)
        for p in sorted(set(self.basin) | set(self.ring), key=lambda q: (q.x, q.y)):
            is_water = world.terrain[p] == WATER
            if p in want and not is_water and p not in busy:
                self._set(world, changes, p, WATER, self.basin.get(p, self.ring.get(p)))
            elif p not in want and is_water:
                self._set(world, changes, p, PLAINS)

    def _ponds(self, world, r, rng, wet, busy, changes, events):
        if wet < -0.3:
            for n in sorted(set(self.ponds.values())):
                if rng.random() < r.pond_dry:
                    tiles = sorted((p for p, m in self.ponds.items() if m == n), key=_xy)
                    for p in tiles:
                        del self.ponds[p]
                        self._set(world, changes, p, PLAINS)
                    events.append(("pond_dried", tiles[0], {"lake": n}))
        if wet <= 0.6:
            return
        ponds: dict[str, set[int]] = {}
        for p, n in self.ponds.items():
            ponds.setdefault(world.region_at.get(p, ""), set()).add(n)
        for region in world.region_names:
            if len(ponds.get(region, ())) >= MAX_PONDS or rng.random() >= r.pond_chance:
                continue
            spot = self._spot(world, rng, region, busy, water=False)
            if spot is None:
                continue
            n = self.next_lake
            self.next_lake += 1
            tiles = [spot]
            extra = spot.neighbors()[rng.randrange(4)]
            if self._free(world, extra, busy, water=False) and rng.random() < 0.5:
                tiles.append(extra)
            for p in tiles:
                self.ponds[p] = n
                self._set(world, changes, p, WATER, n)
            events.append(("pond_formed", spot, {"lake": n, "region": region}))

    def _woods(self, world, r, rng, wet, busy, changes):
        forest = {p for p, t in world.terrain.items() if t == FOREST}
        now: dict[str, int] = {}
        for p in forest:
            region = world.region_at.get(p, world.region_names[0])
            now[region] = now.get(region, 0) + 1
        grow, die = {}, {}
        for region, first in self.woods0.items():  # each valley's woods head for a level
            aim = first * (1 + WOODS_SWING * wet)  # the climate sets
            have = max(1, now.get(region, 0))
            grow[region] = r.woods_spread * min(2.0, aim / have)
            die[region] = r.woods_dieback * min(2.0, have / aim)
        edge: set[Position] = set()
        for p in forest:
            edge.update(q for q in p.neighbors() if q in world.terrain and q not in forest)
        for p in sorted(edge, key=_xy):
            if world.terrain[p] != PLAINS or p in world.burned or p in self.bare or p in changes:
                continue
            if p in self.paths or p in busy or p in self.ring or p in self.ponds:
                continue
            if p in self.changed and self.changed[p][1] == WATER:
                continue  # an exposed lake bed
            touching = sum(q in forest for q in p.neighbors())  # more woods around: likelier
            if rng.random() < grow.get(world.region_at.get(p, ""), 0) * touching / 2:
                self._set(world, changes, p, FOREST)
        for p in sorted(forest, key=_xy):
            if p in world.burned or p in changes:
                continue
            if self.cut.get(p, 0) >= r.wood_per_tree:  # every tree here gathered
                self.cut.pop(p)
                self.bare[p] = r.bare_months
                self._set(world, changes, p, PLAINS)
            else:
                open_sides = 4 - sum(q in forest for q in p.neighbors())  # 0 inside the woods
                chance = die.get(world.region_at.get(p, ""), r.woods_dieback) * open_sides / 2
                if open_sides and rng.random() < chance:
                    self._set(world, changes, p, PLAINS)
        for p in sorted(self.bare, key=_xy):
            self.bare[p] -= 1
            if self.bare[p] <= 0:
                del self.bare[p]  # grass again

    def _footprints(self, world, r, changes):
        for p, n in sorted(self.walks.items(), key=lambda kv: _xy(kv[0])):
            t = changes.get(p, (world.terrain[p], None))[0]
            if t == PLAINS and n >= r.path_steps:
                self.paths[p] = 0
            elif t == FOREST and n >= 2 * r.path_steps and p not in world.burned:
                self.bare[p] = max(self.bare.get(p, 0), 6)  # a clearing
                self._set(world, changes, p, PLAINS)
        for p in sorted(self.paths, key=_xy):
            if self.walks.get(p, 0) >= r.path_steps // 4:
                self.paths[p] = 0
                continue
            self.paths[p] += 1
            if self.paths[p] >= r.path_fade_months or world.terrain[p] == WATER:
                del self.paths[p]
        self.walks = {}

    def _apply(self, world, changes):
        world.change_tiles(changes)
        for p in [p for p in self.paths if world.terrain[p] != PLAINS]:
            del self.paths[p]  # flooded or grown over

    def _dry_homes(self, world, groups):
        """A home under water (God, or an older file) moves to the nearest dry ground."""
        for g in groups:
            h = Position(*g.home)
            if world.is_walkable(h):
                continue
            ground = min(
                (p for p in world.walkable if p.distance(h) <= 6),
                key=lambda p: (p.distance(h), p.x, p.y),
                default=None,
            )
            if ground is not None:
                g.home = (ground.x, ground.y)

    def _follow_shores(self, sim, world):
        """People know their lakes, not single tiles: a remembered shore that moved is now
        remembered where the lake's edge is (the nearest shore within 2 tiles), or forgotten."""
        shore = world.shore
        for a in sim.population.get_alive():
            memory = a.memory
            for loc in memory.get_known_locations("water"):
                p = loc.position
                if p in shore:
                    continue
                near = [
                    Position(p.x + dx, p.y + dy)
                    for dx in range(-2, 3)
                    for dy in range(-2, 3)
                    if abs(dx) + abs(dy) <= 2 and Position(p.x + dx, p.y + dy) in shore
                ]
                memory.forget_location("water", p)
                told = memory.told.pop(("water", p.x, p.y), None)  # not a lie: the lake moved
                if near:
                    best = min(near, key=lambda q: (p.distance(q), q.x, q.y))
                    if not memory.knows_location("water", best):
                        memory.remember_location("water", best, seen=told is None)
                        if told is not None:
                            memory.told[("water", best.x, best.y)] = told

    def _patches(self, sim, r, rng, wet, busy, tick, events, growing=True):
        world = sim.world
        ticks = max(1, r.land_every_days * world.calendar.ticks_per_day)
        active = [f for f in world.food_sources if f.stage <= world.peak_population]
        per_valley: dict[str, int] = {}
        for f in active:
            region = world.food_region.get(f.id, world.region_names[0])
            per_valley[region] = per_valley.get(region, 0) + 1
        born: list[Food] = []
        died = False
        for f in sorted(active, key=lambda f: f.id):
            region = world.food_region.get(f.id, world.region_names[0])
            stripped = self.low.get(f.id, 0) / ticks
            if not growing:
                pass  # bare in the dry and cold seasons is normal: it doesn't count
            elif stripped > 0.5:
                self.stress[f.id] = self.stress.get(f.id, 0) + 1  # over-picked while growing
            else:
                self.stress.pop(f.id, None)
            months = self.stress.get(f.id, 0)
            old = rng.random() < r.patch_old_age
            if (months >= r.patch_strip_months or old) and per_valley[region] > 2:
                world.food_sources.remove(f)
                world.food_region.pop(f.id, None)
                self.stress.pop(f.id, None)
                self.dead[f.id] = tick
                per_valley[region] -= 1
                died = True
                why = "old" if old and months < r.patch_strip_months else "stripped"
                events.append(("patch_died", f.position, {"food": f.id, "why": why}))
                self._tally("died", region)
                continue
            healthy = months == 0 and stripped < 0.1 and f.quantity >= 0.8 * f.max_quantity
            if healthy and per_valley[region] < r.patches_per_valley:
                if rng.random() < r.patch_seed * (1 + 0.5 * wet):
                    spot = self._near(world, rng, f.position, region, busy)
                    if spot is not None:
                        born.append(self._new_patch(world, spot, f.max_quantity, f.regen_per_tick))
                        busy.add(spot)
                        per_valley[region] += 1
        for region in world.region_names:
            have = per_valley.get(region, 0)
            if have >= r.patches_per_valley:
                continue
            chance = REFILL if have < self.patches0.get(region, 0) else r.patch_spawn
            if rng.random() < chance * (1 + 0.5 * wet):
                spot = self._spot(world, rng, region, busy, water=True)
                if spot is not None:
                    like = [f for f in active if world.food_region.get(f.id) == region]
                    regen = like[0].regen_per_tick if like else 3
                    born.append(self._new_patch(world, spot, 100, regen))
                    busy.add(spot)
                    per_valley[region] = per_valley.get(region, 0) + 1
        for f in born:
            region = world.food_region[f.id]
            self.born[f.id] = {
                "x": f.position.x,
                "y": f.position.y,
                "regen": f.regen_per_tick,
                "max": f.max_quantity,
                "tick": tick,
            }
            events.append(("patch_born", f.position, {"food": f.id, "region": region}))
            self._tally("born", region)
        if born or died:
            world.land_version += 1

    def _new_patch(self, world, spot: Position, most: int, regen: int) -> Food:
        f = Food(self.next_food, spot, quantity=most // 5, max_quantity=most, regen_per_tick=regen)
        self.next_food += 1
        world.food_sources.append(f)
        world.food_region[f.id] = world.region_at.get(spot, world.region_names[0])
        return f

    # --------------------------------
    # Where new things can go
    # --------------------------------

    def _free(self, world, p: Position, busy, water: bool) -> bool:
        """Open grass nobody is on, away from patches; `water`: near a shore (good soil),
        else away from water (a pond)."""
        if p not in world.terrain or world.terrain[p] != PLAINS or p in busy:
            return False
        if p in self.paths or p in self.bare or p in self.ring or p in self.changed:
            return False
        if self._edge(world, p):
            return False
        if any(f.position.distance(p) < 3 for f in world.food_sources):
            return False
        near = any(
            world.terrain.get(Position(p.x + dx, p.y + dy)) == WATER
            for dx in range(-3, 4)
            for dy in range(-3, 4)
        )
        return near if water else not near

    def _spot(self, world, rng, region, busy, water: bool) -> Position | None:
        for _ in range(40):
            p = Position(rng.randrange(world.width), rng.randrange(world.height))
            if world.region_at.get(p) == region and self._free(world, p, busy, water):
                return p
        return None

    def _near(self, world, rng, origin: Position, region, busy) -> Position | None:
        for _ in range(20):
            p = Position(origin.x + rng.randint(-4, 4), origin.y + rng.randint(-4, 4))
            if 2 <= origin.distance(p) and world.region_at.get(p) == region:
                if self._free(world, p, busy, water=True):  # berries grow near water
                    return p
        return None

    # --------------------------------
    # Numbers for the yearly snapshot and the History page
    # --------------------------------

    def _tally(self, what: str, region: str):
        valley = self.year.setdefault("valleys", {}).setdefault(region, {"born": 0, "died": 0})
        valley[what] += 1

    def _count(self, world):
        water = sum(1 for t in world.terrain.values() if t == WATER)
        self.year["water_min"] = min(self.year.get("water_min", water), water)
        self.year["water_max"] = max(self.year.get("water_max", water), water)

    def yearly(self, world) -> dict:
        """This year's land, for the yearly snapshot; starts counting the next year."""
        woods: dict[str, int] = {}
        for p, t in world.terrain.items():
            if t == FOREST:
                region = world.region_at.get(p, world.region_names[0])
                woods[region] = woods.get(region, 0) + 1
        patches: dict[str, int] = {}
        for f in world.food_sources:
            if f.stage <= world.peak_population:
                region = world.food_region.get(f.id, world.region_names[0])
                patches[region] = patches.get(region, 0) + 1
        water = sum(1 for t in world.terrain.values() if t == WATER)
        tallies = self.year.get("valleys", {})
        out = {
            "woods": sum(woods.values()),
            "water": water,
            "water_min": self.year.get("water_min", water),
            "water_max": self.year.get("water_max", water),
            "patches": sum(patches.values()),
            "ponds": len(set(self.ponds.values())),
            "paths": len(self.paths),
            "bare": len(self.bare),
            "climate": round(climate(self.seed, self.month), 3),
            "valleys": {
                name: {
                    "woods": woods.get(name, 0),
                    "patches": patches.get(name, 0),
                    "born": tallies.get(name, {}).get("born", 0),
                    "died": tallies.get(name, {}).get("died", 0),
                }
                for name in world.region_names
            },
        }
        self.year = {}
        return out

    # --------------------------------
    # Saving and loading
    # --------------------------------

    def to_dict(self) -> dict:
        def tiles(d):
            return {_key(p): v for p, v in sorted(d.items(), key=lambda kv: _xy(kv[0]))}

        return {
            "month": self.month,
            "changed": {
                _key(p): [c[0].value, c[1].value, c[2]]
                for p, c in sorted(self.changed.items(), key=lambda kv: _xy(kv[0]))
            },
            "ponds": tiles(self.ponds),
            "bare": tiles(self.bare),
            "paths": tiles(self.paths),
            "walks": tiles(self.walks),
            "cut": tiles(self.cut),
            "low": {str(k): v for k, v in sorted(self.low.items())},
            "stress": {str(k): v for k, v in sorted(self.stress.items())},
            "born": {str(k): v for k, v in sorted(self.born.items())},
            "dead": {str(k): v for k, v in sorted(self.dead.items())},
            "year": self.year,
            "next_food": self.next_food,
        }

    def restore(self, world, saved: dict):
        """On load: the land as it was saved (after God's changes are back)."""
        if not saved:
            return
        self.month = saved["month"]
        self.ponds = {_pos(k): v for k, v in saved["ponds"].items()}
        self.bare = {_pos(k): v for k, v in saved["bare"].items()}
        self.paths = {_pos(k): v for k, v in saved["paths"].items()}
        self.walks = {_pos(k): v for k, v in saved["walks"].items()}
        self.cut = {_pos(k): v for k, v in saved["cut"].items()}
        self.low = {int(k): v for k, v in saved["low"].items()}
        self.stress = {int(k): v for k, v in saved["stress"].items()}
        self.born = {int(k): v for k, v in saved["born"].items()}
        self.dead = {int(k): v for k, v in saved["dead"].items()}
        self.year = saved.get("year", {})
        self.next_food = saved["next_food"]
        apply_saved(world, saved)
        self.changed = {
            _pos(k): [TerrainType(c[0]), TerrainType(c[1]), c[2]]
            for k, c in saved["changed"].items()
        }
        self.rebase(world)

    def fingerprint(self) -> str:
        """For `Simulation.state()`: everything that can change, comparably."""
        return repr(sorted(self.to_dict().items()))


def apply_saved(world, saved: dict):
    """Put a saved land onto a freshly built world: its tiles and its patches (also used by
    the dashboard's map)."""
    if not saved:
        return
    world.change_tiles(
        {
            _pos(k): (TerrainType(c[0]), c[2] if c[0] == WATER.value else None)
            for k, c in saved.get("changed", {}).items()
        }
    )
    dead = {int(k) for k in saved.get("dead", {})}
    world.food_sources = [f for f in world.food_sources if f.id not in dead]
    known = {f.id for f in world.food_sources}
    for k, b in sorted(saved.get("born", {}).items(), key=lambda kv: int(kv[0])):
        fid = int(k)
        if fid in dead or fid in known:
            continue
        p = Position(b["x"], b["y"])
        world.food_sources.append(Food(fid, p, 0, b["max"], b["regen"], 0))
        world.food_region[fid] = world.region_at.get(p, world.region_names[0])
    for fid in dead:
        world.food_region.pop(fid, None)


def _xy(p: Position) -> tuple[int, int]:
    return (p.x, p.y)
