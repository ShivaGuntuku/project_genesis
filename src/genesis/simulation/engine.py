import random

from genesis.agents.population import Population
from genesis.config import WorldConfig
from genesis.history import events as ev
from genesis.history.events import EventLog
from genesis.history.metrics import daily_metrics
from genesis.mind import goals
from genesis.mind import stream as memory
from genesis.simulation.context import TickContext
from genesis.society import family, groups
from genesis.world import disasters
from genesis.world.world import Position, World

TICKS_PER_DAY = 24


class Simulation:
    """Runs one world. Same config (incl. seed) = same history."""

    def __init__(self, config: WorldConfig, *, _empty: bool = False):
        self.config = config
        self.rng = random.Random(config.seed)  # the only source of randomness
        self.world = World(config)
        self.population = Population(self.rng, seed=config.seed)
        self.log = EventLog()
        self.tick_count = 0
        self.pending_metrics: list[tuple[int, str, float]] = []  # saved with the next tick
        self.pending_yearly: list[tuple[int, int, dict]] = []  # yearly snapshots (M8e)
        self.god_schedule: dict[int, list[dict]] = {}  # replays only: {tick: God commands}
        self.pending_relationship_history: list[tuple] = []  # saved with the next tick
        self.counts_at_day_start: dict[str, int] = {}  # for "messages per day" metrics
        self.on_tick: list = []  # callbacks(sim) after every tick (e.g. the live viewer)
        self.mind = None  # None = rules only; see genesis.llm.mind.attach
        self.pending_llm_calls: list[dict] = []  # saved with the next tick
        # Memory streams (M5). Memories live on the agents; these track what to save.
        self.next_memory_id = 1
        self.pending_memories: list[tuple[int, memory.Memory]] = []  # (agent id, memory)
        self.pending_forgotten: list[tuple[int, int]] = []  # (memory id, tick forgotten)
        # Embeddings: a cache derived from memory text (not world state; never affects history)
        self.memory_vectors: dict = {}  # memory id -> unit vector (numpy)
        self.pregnancies: list[dict] = []  # unborn children (M6), see society/family.py
        self.last_save_tick = 0  # agents who died since then are still saved in full
        self.groups: dict = {}  # id -> Group (M7), see society/groups.py
        self.concepts = self.world.concepts  # M8b: the concept registry (on the world)
        self.structures = self.world.structures
        self.pending_vectors: list[int] = []  # memory ids whose vectors are new
        self.dropped_vectors: list[int] = []  # memory ids whose vectors can be deleted

        if _empty:  # filled in by persistence when loading
            return

        created = self.log.emit(
            0, ev.WORLD_CREATED, data={"name": config.name, "seed": config.seed}, origin=ev.SYSTEM
        )

        r = config.rules
        starts = [((s.x, s.y), s.radius) for s in config.starts for _ in range(s.founders)] or [
            (config.start_center, config.start_radius)
        ] * config.founders
        # Founders of a generated world arrive at their valley's lake and know where it is
        # (M8e: on a big map they would otherwise wander off and die of thirst).
        know_lake = bool(self.world.disasters) or any(f.stage for f in config.food)
        for name, (center, radius) in zip(self._founder_names(config), starts, strict=True):
            position = self._random_start_position(center, radius)
            # Founders arrive as adults of different ages.
            tpd = self.world.calendar.ticks_per_day
            age = self.rng.randint(r.founder_min_age_days * tpd, r.founder_max_age_days * tpd)
            agent = self.population.create_agent(name, position, born_tick=-age)
            if know_lake and self.world.shore:
                shore = min(self.world.shore, key=lambda p: (p.distance(position), p.x, p.y))
                agent.memory.remember_location("water", shore)
                patches = sorted(
                    (f for f in self.world.food_sources if f.stage == 0),
                    key=lambda f: (f.position.distance(position), f.id),
                )
                for f in patches[:2]:
                    agent.memory.remember_location("food", f.position)
            agent._emit(
                self.log,
                0,
                ev.FOUNDER_ARRIVED,
                data={"name": name, "age_days": self.world.calendar.age_days(age)},
                cause_id=created,
            )

    def _founder_names(self, config) -> list[str]:
        """The world's names first; if there are more founders than names, new names are made
        from syllables (seeded, so the same world always gets the same names)."""
        names = list(config.names[: config.founders])
        taken = {n.lower() for n in config.names}
        while len(names) < config.founders:
            name = "".join(
                self.rng.choice(family.SYLLABLES) for _ in range(self.rng.choice((2, 2, 3)))
            ).capitalize()
            if name.lower() not in taken:
                taken.add(name.lower())
                names.append(name)
        return names

    @classmethod
    def create(cls, seed: int = 42, founders: int = 3) -> "Simulation":
        """The default world with a given seed and founder count (handy for tests and demos)."""
        return cls(WorldConfig.default().with_overrides(seed=seed, founders=founders))

    # --------------------------------
    # Clock
    # --------------------------------

    @property
    def day(self) -> int:
        return self.world.calendar.day(self.tick_count)

    @property
    def hour(self) -> int:
        return self.world.calendar.hour(self.tick_count)

    def _random_start_position(self, center=None, radius=None) -> Position:
        """A walkable tile near a start centre."""
        cx, cy = center or self.config.start_center
        radius = self.config.start_radius if radius is None else radius

        for _ in range(10_000):
            p = Position(
                cx + self.rng.randint(-radius, radius), cy + self.rng.randint(-radius, radius)
            )

            if self.world.is_walkable(p):
                return p

        raise ValueError("No walkable start position near population.start_center")

    # --------------------------------
    # Running
    # --------------------------------

    def tick(self):
        """One hour. Everyone observes the same frozen world, then everyone acts.

        Order: world update → needs → perceive (all) → decide (all) → act (seeded shuffled order).
        Agents may share a tile. If two agents want the last food, the one acting first gets it.
        """
        tick = self.tick_count
        if self.god_schedule:  # a replay: God's commands at the tick they were made (M9)
            from genesis.god import interventions as god

            for command in self.god_schedule.pop(tick, []):
                god.apply(self, command)
        first_event = self.log.next_id
        self._world_conditions(tick)
        self.world.update(tick)

        agents = self.population.agents
        ctx = TickContext(
            world=self.world,
            tick=tick,
            log=self.log,
            rng=self.rng,
            agents={a.id: a for a in agents},
            mind=self.mind,
            pregnancies=self.pregnancies,
            groups=self.groups,
        )
        if self.pregnancies:
            family.births(ctx, self)  # before anyone acts: newborns join this tick

        for agent in agents:  # a fresh tick: all its hours ahead, nobody has talked yet
            agent.hours_left = self.world.calendar.hours_per_tick
            agent.talked = False
            agent.update_needs(self.world, tick, self.log, self.rng)
        if self.world.disasters:
            self._disasters(ctx)

        for agent in agents:
            agent.perceive(ctx)

        for agent in agents:  # what each member knows of their group, for decisions
            g = self.groups.get(agent.group_id) if agent.group_id is not None else None
            agent.group = groups.view(g) if g is not None else None

        config = getattr(self.mind, "config", None)
        if config is not None and self.mind.active and config.plan_every_days:
            from genesis.mind import plans  # M8f: each adult's own plan, from the LLM

            plans.ask_all(ctx, agents)

        for agent in agents:
            agent.decide(self.world)

        order = list(self.population.get_alive())
        self.rng.shuffle(order)

        hours = self.world.calendar.hours_per_tick
        for agent in order:
            agent.act(ctx)

        # Watches (M8a): with hours left, an agent looks around and decides again (up to two
        # more times), in the same order. Story mode (1-hour ticks) never gets here.
        if hours > 1:
            for _ in range(2):
                again = [a for a in order if a.alive and a.hours_left > 0 and not a.sleeping]
                if not again:
                    break
                for agent in again:
                    agent.perceive(ctx)
                for agent in again:
                    agent.decide(self.world)
                for agent in again:
                    agent.act(ctx)

        if self.world.rules.inventions:
            from genesis.culture import inventions

            for agent in agents:
                if agent.alive and not agent.sleeping:
                    inventions.notice_problems(ctx, agent)
        self._grieve(ctx, first_event)
        self._remember(first_event)
        alive = sum(1 for a in agents if a.alive)
        self.world.peak_population = max(self.world.peak_population, alive)
        self.tick_count += 1

        if self.world.calendar.is_day_end(self.tick_count):  # a day just ended
            self._end_of_day(ctx)

        self.pending_relationship_history.extend(ctx.relationship_history)
        self.pending_llm_calls.extend(ctx.llm_calls)

        for callback in self.on_tick:
            callback(self)

    def _world_conditions(self, tick: int):
        """Scheduled droughts and famines (M7) and what disasters do to the land (M8e)."""
        w = self.world
        if not w.schedule and not w.disasters:
            return
        day = self.world.calendar.day(tick)
        dry, famine = self.world.conditions_on(day)
        if w.disasters:
            c = disasters.conditions(w, w.calendar.day_index(tick))
            dry, famine = dry | c["dry"], famine | c["famine"]
            w.regrow = c["factor"]
            w.set_burned(c["burned"])
        for kind, old, new in (
            ("drought", self.world.dry_lakes, dry),
            ("famine", self.world.famine, famine),
        ):
            for target in sorted(new - old):
                self.world.condition_events[f"{kind}:{target}"] = self.log.emit(
                    tick, ev.WORLD_CHANGED, data={"kind": kind, "target": target, "started": True}
                )
            for target in sorted(old - new):
                started = self.world.condition_events.pop(f"{kind}:{target}", None)
                self.log.emit(
                    tick,
                    ev.WORLD_CHANGED,
                    data={"kind": kind, "target": target, "started": False},
                    cause_id=started,
                )
        self.world.dry_lakes, self.world.famine = dry, famine

    def sync_world_conditions(self):
        """After loading: the conditions of the last tick that ran (no events)."""
        if self.tick_count > 0:
            w = self.world
            day = w.calendar.day(self.tick_count - 1)
            w.dry_lakes, w.famine = w.conditions_on(day)
            if w.disasters:
                c = disasters.conditions(w, w.calendar.day_index(self.tick_count - 1))
                w.dry_lakes, w.famine = w.dry_lakes | c["dry"], w.famine | c["famine"]
                w.regrow = c["factor"]
                w.set_burned(c["burned"])
                self._troubles()

    def _disasters(self, ctx):
        """Start and end disasters on their days, and what they do each tick (M8e)."""
        w = self.world
        day = w.calendar.day_index(ctx.tick)
        for d in w.disasters:
            if d.day > day + 1:
                break
            running = d.id in w.disaster_events
            if d.active(day) and not running:
                disasters.start(ctx, d)
            elif running and not d.active(day):
                disasters.end(ctx, d)
        self._troubles()
        disasters.each_tick(ctx, disasters.active(w, day))

    def _troubles(self):
        """Valleys people want to leave: a disaster there now, or too many people."""
        w, r = self.world, self.world.rules
        day = w.calendar.day_index(max(0, self.tick_count - 1))
        bad = {"drought", "flood", "wildfire", "predators"}
        troubled = {
            region
            for d in disasters.active(w, day)
            if d.kind in bad
            for region in w.region_names
            if d.hits(region)
        }
        if r.crowd_per_valley:
            count: dict[str, int] = {}
            for a in self.population.get_alive():
                region = w.region_at.get(a.position, "")
                count[region] = count.get(region, 0) + 1
            troubled |= {k for k, n in count.items() if n > r.crowd_per_valley}
        w.troubled = frozenset(troubled)

    def _grieve(self, ctx, first_event_id: int):
        deaths = [e for e in self._events_since(first_event_id) if e.type == ev.DIED]
        for event in deaths:
            family.grieve(ctx, event)
            dead = ctx.agents.get(event.actors[0])
            if dead is not None:
                groups.on_death(ctx, dead, event.id)
            if self.world.rules.inventions and self.concepts:
                from genesis.culture import inventions

                inventions.check_lost(ctx, event.id)

    def _events_since(self, first_event_id: int) -> list:
        new = []
        for event in reversed(self.log.pending):
            if event.id < first_event_id:
                break
            new.append(event)
        return new[::-1]

    def _end_of_day(self, ctx):
        tick = self.tick_count
        first_event = self.log.next_id
        for agent in self.population.get_alive():
            agent.fade_feelings(self.log, tick)
        self._remember(first_event)

        every = max(1, self.world.rules.reflect_every_days)
        day = self.world.calendar.day_index(tick - 1)
        if ctx.mind is not None and ctx.mind.active and day % every == every - 1:
            from genesis.llm import reflection  # the LLM package is only needed with a mind

            first_event = self.log.next_id
            reflection.reflect_all(ctx, self, self.population.get_alive())  # M8.5: together
            self._remember(first_event)

        if self.world.rules.inventions:
            from genesis.culture import inventions

            for agent in self.population.get_alive():
                inventions.fade_problems(agent)
        first_event = self.log.next_id
        groups.nightly(ctx)
        self._remember(first_event)
        if self.world.rules.goals:
            first_event = self.log.next_id
            goals.nightly(ctx, self)
            self._remember(first_event)
        if self.world.rules.talents:
            from genesis.agents import talents

            for agent in self.population.get_alive():
                talents.fade_work(agent)
        if self.world.rules.trade:
            from genesis.society import economy

            first_event = self.log.next_id
            economy.nightly(ctx, self)
            self._remember(first_event)
        if len(self.world.region_names) > 1:
            first_event = self.log.next_id
            self._migration(ctx)
            self._remember(first_event)
        if self.world.calendar.watches and self.world.calendar.day_of_year(tick) == 1:  # new year
            self._yearly_snapshot(tick)

        every = max(1, round(self.world.rules.forget_every_days))
        if self.world.calendar.day_index(tick - 1) % every == 0:  # M8.5: not every night
            for agent in self.population.get_alive():
                self._forget(agent)
        self.pending_metrics.extend(daily_metrics(self))
        self.counts_at_day_start = dict(self.log.counts)

    def _migration(self, ctx):
        """Someone who has lived in another valley for a while has moved there (M8e)."""
        w, r = self.world, self.world.rules
        for a in self.population.get_alive():
            here = w.region_at.get(a.position, w.region_names[0])
            if a.home_region is None:
                a.home_region = here
            if here == a.home_region:
                a.away_days = 0
                continue
            a.away_days += 1
            if a.away_days >= r.migrate_days:
                event_id = a._emit(
                    ctx.log,
                    ctx.tick,
                    ev.MIGRATED,
                    data={"from": a.home_region, "to": here},
                )
                a.home_region, a.away_days = here, 0
                _ = event_id

    def _yearly_snapshot(self, tick):
        """Once a year: each living mind and each valley, as they are (M8e, for long runs)."""
        from genesis.agents import talents

        w = self.world
        year = w.calendar.year(tick)
        valleys: dict[str, dict] = {
            name: {"people": 0, "ideas": set(), "names": []} for name in w.region_names
        }
        for a in self.population.get_alive():
            region = w.region_at.get(a.position, w.region_names[0])
            v = valleys.setdefault(region, {"people": 0, "ideas": set(), "names": []})
            v["people"] += 1
            v["ideas"] |= a.knowledge
            beliefs = [m.text for m in a.stream.beliefs(3)] if hasattr(a.stream, "beliefs") else []
            self.pending_yearly.append(
                (
                    year,
                    a.id,
                    {
                        "region": region,
                        "x": a.position.x,  # M10: the map at any past year
                        "y": a.position.y,
                        "home": a.home_region,
                        "age_years": a.age(tick)
                        // max(1, w.calendar.ticks(days=w.calendar.days_per_year)),
                        "story": a.story,
                        "beliefs": beliefs,
                        "goal": a.goal["text"] if a.goal else None,
                        "talent": a.talent,
                        "profession": talents.profession(a) if w.rules.talents else None,
                        "knows": sorted(w.concepts[c].name for c in a.knowledge if c in w.concepts),
                        "skills": {k: round(v, 2) for k, v in a.abilities.trained().items()},
                    },
                )
            )
        money = {c["region"]: c["name"] for c in w.economy["currencies"]}
        self.pending_yearly.append(
            (
                year,
                0,
                {
                    "valleys": {
                        k: {
                            "people": v["people"],
                            "ideas": sorted(
                                w.concepts[c].name for c in v["ideas"] if c in w.concepts
                            ),
                            "currency": money.get(k),
                        }
                        for k, v in valleys.items()
                    },
                    "concepts": len(w.concepts),
                    "lost": sum(1 for c in w.concepts.values() if c.lost_tick is not None),
                    "disasters": [
                        d.to_dict() for d in disasters.active(w, w.calendar.day_index(tick))
                    ],
                },
            )
        )

    # --------------------------------
    # Memory
    # --------------------------------

    def add_memory(self, agent, tick: int, source_event_id=None, **fields) -> memory.Memory:
        m = memory.Memory(
            id=self.next_memory_id, tick=tick, source_event_id=source_event_id, **fields
        )
        self.next_memory_id += 1
        agent.stream.add(m)
        self.pending_memories.append((agent.id, m))
        return m

    def _remember(self, first_event_id: int):
        """Everyone involved remembers the events since first_event_id (rules, no LLM)."""
        new = self._events_since(first_event_id)
        if not new:
            return
        agents = {a.id: a for a in self.population.agents}
        names = {a.id: a.name for a in self.population.agents}
        if self.world.rules.goals:
            for event in new:
                goals.count_deeds(event, agents)
        if self.world.rules.talents:
            from genesis.agents import talents

            for event in new:
                talents.count_work(event, agents)
        for event in new:
            for agent_id, fields in memory.memories_from_event(event, agents, names):
                agent = agents.get(agent_id)
                if agent is not None and (agent.alive or event.type == ev.DIED):
                    self.add_memory(agent, event.tick, source_event_id=event.id, **fields)
            if event.type == ev.DIED:  # the dead are never recalled: drop their vectors
                dead = agents.get(event.actors[0])
                if dead is not None:
                    self._drop_vectors(dead.stream.items)

    def _forget(self, agent):
        forgotten, summaries = memory.forget(
            agent.stream, self.tick_count, self.world.rules, self.world.calendar
        )
        if not forgotten:
            return
        self.pending_forgotten.extend((m.id, self.tick_count) for m in forgotten)
        self._drop_vectors(forgotten)
        for fields in summaries:
            self.add_memory(agent, **fields)

    def _drop_vectors(self, memories):
        for m in memories:
            if self.memory_vectors.pop(m.id, None) is not None:
                self.dropped_vectors.append(m.id)

    def run(self, ticks: int):
        for _ in range(ticks):
            self.tick()

    def state(self):
        """Full comparable state, used to check determinism and save/load."""
        return (
            self.tick_count,
            self.rng.getstate(),
            self.log.next_id,
            tuple(a.comparable() for a in self.population.agents),
            tuple(f.quantity for f in self.world.food_sources),
            self.next_memory_id,
            repr(self.pregnancies),
            repr([g.to_dict() for g in self.groups.values()]),
            repr([c.to_dict() for c in self.concepts.values()]),
            repr(self.structures),
            tuple(
                (a.id, len(a.stream), a.stream.items[-1].id if a.stream.items else 0)
                for a in self.population.get_alive()
            ),
        )
