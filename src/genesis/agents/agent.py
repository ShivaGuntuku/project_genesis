import random
from dataclasses import dataclass, field

from genesis.agents import lifecycle
from genesis.agents.abilities import Abilities, describe_skill
from genesis.agents.genome import Genome
from genesis.culture import inventions
from genesis.history import events as ev
from genesis.history.events import EventLog
from genesis.mind import planner
from genesis.mind.decision import ActionType, DecisionEngine
from genesis.mind.memory import AgentMemory, ExplorationGoal
from genesis.mind.perception import Observation, PerceptionSystem
from genesis.mind.planner import ExplorationPlanner, NavigationPlanner
from genesis.mind.stream import MemoryStream
from genesis.society import groups, interaction
from genesis.society.relationships import Relationship
from genesis.world.world import Position, World

NEEDS = {"water": "thirst", "food": "hunger"}  # resource -> need attribute


def gene_factor(gene: float, effect: float) -> float:
    """1.0 for an average gene (0.5); 1 ± effect at the extremes."""
    return 1.0 + effect * (2 * gene - 1)


def _asleep(agent) -> bool:
    """Asleep this tick: everyone decides before anyone acts, so this is current."""
    return agent is not None and agent.current_action == ActionType.SLEEP


@dataclass
class Agent:
    id: int
    name: str
    position: Position
    genome: Genome
    born_tick: int = 0  # negative for founders, who were born before the world began
    generation: int = 0
    parents: tuple[int, ...] = ()

    hunger: float = 30
    thirst: float = 30
    energy: float = 100
    health: float = 100

    alive: bool = True
    sleeping: bool = False
    facing: str = "down"  # direction of the last step (for drawing)
    died_tick: int | None = None
    cause_of_death: str | None = None
    stage: str = lifecycle.ADULT

    current_action: ActionType | None = None
    last_observation: Observation | None = None
    last_scores: dict = field(default_factory=dict)

    memory: AgentMemory = field(default_factory=AgentMemory)
    abilities: Abilities = field(default_factory=Abilities)

    # Open causes, so later events can point back to them.
    # {"water": event_id} while thirst is pressing; {"water": event_id} while dehydrating.
    pressing_events: dict = field(default_factory=dict)
    suffering_events: dict = field(default_factory=dict)
    stage_event: int | None = None  # the event that started the current life stage

    # Society (M3)
    inventory: dict = field(default_factory=lambda: {"food": 0, "water": 0})
    relationships: dict = field(default_factory=dict)  # other id -> Relationship (my view)
    asked: dict = field(default_factory=dict)  # "other:kind:resource" -> tick last asked
    help_request: tuple | None = None  # set by decide(), used by act() in the same tick
    stream: MemoryStream = field(default_factory=MemoryStream)  # memories (own table in the db)
    story: str = ""  # self-story in their own words, rewritten by nightly reflection (M5)
    # Family (M6)
    partner: int | None = None  # pair bond, formed when two agree to have a child
    children: list = field(default_factory=list)  # ids, in birth order
    next_mate_tick: int = 0  # cooldown after a conception (or a try that didn't conceive)
    pregnant: bool = False  # carrying a child (any adult can; there are no sexes yet)
    care_target: tuple | None = None  # (child id, resource), set by decide() for act()
    # Groups (M7)
    group_id: int | None = None
    order: dict | None = None  # an order being carried out: kind, target, leader, tick
    group: object = None  # GroupView of their group, refreshed by the engine every tick
    # Watches (M8a): hours left in this watch, and whether they have talked in it yet.
    hours_left: int = 1
    talked: bool = False
    route: list | None = field(default=None, repr=False)  # path being walked this watch
    # Invention (M8b)
    materials: dict = field(default_factory=dict)  # material -> count carried
    items: list = field(default_factory=list)  # [{"concept": id, "uses": n}]
    knowledge: set = field(default_factory=set)  # concept ids they know how to make
    problems: dict = field(default_factory=dict)  # what keeps going wrong -> how much
    project: dict | None = None  # set by decide(): what to gather / try / make
    bonus: dict = field(default_factory=dict)  # what their things add (see inventions)
    goal: dict | None = None  # M8c: {need, text, tick, how, fed, taught}
    talent: str | None = None  # M8d: one inborn talent (see agents/talents.py)
    work: dict = field(default_factory=dict)  # kind of work -> how much lately (fades)
    market: dict = field(default_factory=dict)  # good -> [times seen taken, times seen offered]
    owes: list = field(default_factory=list)  # promises: {to, good, due, event}
    sick_until: int | None = None  # M8e: sick until this tick
    sick_event: int | None = None
    immune_until: int = 0
    home_region: str | None = None  # the valley they live in
    away_days: int = 0  # days spent in another valley in a row
    migrating: str | None = None  # on the way to this valley
    plan: dict | None = None  # M8f: their own plan for these days {focus, label, why, until}
    deeds: dict = field(default_factory=dict)  # counts the goals check: fed, taught, ...

    def __post_init__(self):
        self.decision_engine = DecisionEngine()
        self.navigator = NavigationPlanner()
        self.memory.remember_position(self.position)

    # =================================================================
    # Body, derived from genes, age and skills
    # =================================================================

    def age(self, tick: int) -> int:
        return tick - self.born_tick

    def lifespan(self, world: World) -> int:
        return lifecycle.lifespan_ticks(self.genome, world.rules, world.calendar.ticks_per_day)

    def vision_radius(self, world: World) -> int:
        r = world.rules
        radius = r.vision_radius + round(
            (2 * self.genome.perception - 1) * r.perception_vision_range
        )

        if self.stage == lifecycle.ELDER:
            radius -= r.elder_vision_penalty

        searching_water = self.memory.get_exploration_goal() == ExplorationGoal.FIND_WATER
        if searching_water and self.abilities.get("water_finding") >= r.water_finding_vision_at:
            radius += 1
        if r.talents and (
            self.talent == "keen-eyed" or (self.talent == "water-sense" and searching_water)
        ):
            radius += 2  # M8d talents

        return max(1, radius)

    def carry_capacity(self, resource: str, rules) -> int:
        if resource == "water":
            return rules.water_capacity + self.bonus.get("water", 0)
        base = round(rules.food_capacity_base + rules.food_capacity_strength * self.genome.strength)
        if rules.talents and self.talent == "strong-armed":
            base = round(base * 1.5)
        return base + self.bonus.get("food", 0)

    def relationship(self, other_id: int, other_name: str, met: bool = True) -> Relationship:
        """My view of someone, created on first contact (or first gossip about them)."""
        rel = self.relationships.get(other_id)
        if rel is None:
            rel = Relationship(target_id=other_id, target_name=other_name, met=met)
            self.relationships[other_id] = rel
        elif met and not rel.met:
            rel.met = True
        return rel

    def learn_told(self, resource, position, teller, message_id, ctx) -> bool:
        """Someone told me where a resource is. Returns True if it was news to me."""
        if self.memory.knows_location(resource, position):
            return False
        first_of_kind = not self.memory.get_known_locations(resource)
        self.memory.remember_location(resource, position, seen=False)
        self.memory.told[(resource, position.x, position.y)] = (teller.id, message_id)
        if first_of_kind:
            self._emit(
                ctx.log,
                ctx.tick,
                ev.DISCOVERED,
                position=position,
                data={"resource": resource, "how": "told", "by": teller.name},
                cause_id=message_id,
            )
        return True

    # =================================================================
    # Tick phases (called by Simulation in this order, for all agents)
    # =================================================================

    def update_needs(self, world: World, tick: int, log: EventLog, rng: random.Random):
        """Phase 1: body changes and ageing. May kill the agent."""
        if not self.alive:
            return

        r, g = world.rules, self.genome
        self._update_stage(world, tick, log)

        metabolism = gene_factor(g.metabolism, r.metabolism_effect)
        hours = world.calendar.hours_per_tick  # rules are per hour; a tick may be a 6-h watch
        self.hunger = min(100, self.hunger + r.hunger_per_tick * metabolism * hours)
        self.thirst = min(100, self.thirst + r.thirst_per_tick * metabolism * hours)

        if not self.sleeping:
            drain = r.energy_per_tick * gene_factor(1 - g.endurance, r.endurance_effect) * hours
            if self.stage == lifecycle.ELDER:
                drain *= r.elder_energy_factor
            if r.talents and self.talent == "tireless":
                drain *= 0.67
            self.energy = max(0, self.energy - drain)

        for resource, need in NEEDS.items():
            level = getattr(self, need)

            if level >= r.seek_threshold and resource not in self.pressing_events:
                self.pressing_events[resource] = self._emit(
                    log,
                    tick,
                    ev.NEED_BECAME_PRESSING,
                    data={"need": need, "level": round(level, 1)},
                )

            if level >= 100 and resource not in self.suffering_events:
                self.suffering_events[resource] = self._emit(
                    log,
                    tick,
                    ev.SUFFERING,
                    data={"need": need},
                    cause_id=self.pressing_events.get(resource),
                )

        toughness = gene_factor(1 - g.resilience, r.resilience_effect)
        if self.hunger >= 100:
            self.health -= r.starving_damage * toughness * hours

        if self.thirst >= 100:
            self.health -= r.dehydration_damage * toughness * hours

        if self.hunger < r.heal_below and self.thirst < r.heal_below and self.sick_until is None:
            self.health += r.heal_per_tick * gene_factor(g.resilience, r.resilience_effect) * hours

        self.health = max(0, min(100, self.health))

        if self.health == 0:
            cause = "dehydration" if self.thirst >= 100 else "starvation"
            resource = "water" if cause == "dehydration" else "food"
            self._die(tick, log, cause, self.suffering_events.get(resource))
            return

        # Old age: the chance rises sharply after the natural lifespan.
        chance = lifecycle.old_age_death_chance(self.age(tick), g, r, world.calendar.ticks_per_day)
        if chance > 0 and rng.random() < chance:
            self._die(tick, log, "old age", self.stage_event)

    def perceive(self, ctx):
        """Phase 2: observe the world as it was at the start of the tick."""
        if not self.alive:
            return

        world, tick, log = ctx.world, ctx.tick, ctx.log
        radius = self.vision_radius(world)
        observation = PerceptionSystem(radius).observe(self, world, ctx.agents.values(), tick)
        self.last_observation = observation

        new = self.memory.remember_seen(observation.visible_tiles)
        if new and world.rules.talents and tick > 0:  # M8f: seeing new land is a scout's work
            self.work["scout"] = round(self.work.get("scout", 0.0) + new / 40, 4)
            self._practice("scouting", new / 80, world, tick, log)

        for other in observation.visible_agents:
            met_id = None
            if not self.memory.knows_agent(other["id"]):
                met_id = self._emit(
                    log, tick, ev.MET, targets=(other["id"],), data={"name": other["name"]}
                )

            new_encounter = self.memory.remember_agent(
                other["id"], other["name"], other["position"], tick, met_id
            )
            if new_encounter:
                interaction.feel(ctx, self, ctx.agents[other["id"]], "encounter", met_id)
            if self.group_id is not None:
                groups.notice(ctx, self, other["id"])

        self._check_told_places(observation, ctx)
        self._update_resource_memory(observation, world, log)

    def decide(self, world: World):
        """Phase 3: choose an action. Does not change the world."""
        if not self.alive:
            self.current_action = None
            return None

        self.current_action = self.decision_engine.decide(self, self.last_observation, world)
        return self.current_action

    def act(self, ctx):
        """Phase 4: carry out the chosen action."""
        if not self.alive or self.current_action is None:
            return

        world, tick, log = ctx.world, ctx.tick, ctx.log
        r = world.rules
        action = self.current_action
        self.sleeping = action == ActionType.SLEEP
        hours_before = self.hours_left

        if action == ActionType.EAT:
            self._eat(world, tick, log)
        elif action == ActionType.DRINK:
            self._drink(world, tick, log)
        elif action == ActionType.SLEEP:
            rest = r.sleep_energy * world.calendar.hours_per_tick
            if world.structures and inventions.sheltered(self, world.structures):
                rest *= 1.5  # a shelter of their own or their group's (M8b)
            self.energy = min(100, self.energy + rest)
        elif action == ActionType.REST:
            self.energy = min(100, self.energy + r.rest_energy * world.calendar.hours_per_tick)
        elif action == ActionType.MOVE_TO_FOOD:
            self._walking(world, lambda: self._move_to_known("food", world))
        elif action == ActionType.MOVE_TO_WATER:
            self._walking(world, lambda: self._move_to_known("water", world))
        elif action == ActionType.EXPLORE:
            self._walking(world, lambda: self._explore(world, tick, log))
        elif action == ActionType.SOCIALIZE:
            self._walking(world, lambda: self._socialize(ctx))
        elif action == ActionType.ASK_FOR_HELP:
            self._walking(world, lambda: self._ask_for_help(ctx))
        elif action == ActionType.EAT_CARRIED:
            self._eat_carried(world, tick, log)
        elif action == ActionType.DRINK_CARRIED:
            self._drink_carried(world, tick, log)
        elif action == ActionType.CARE:
            self._walking(world, lambda: self._care(ctx))
        elif action == ActionType.FOLLOW:
            self._walking(world, lambda: self._follow(ctx))
        elif action == ActionType.GO_HOME:
            if self.group is not None:
                self._walking(world, lambda: self.step_towards(self.group.home, ctx.world))
        elif action == ActionType.TEND:
            self._walking(world, lambda: self._tend(ctx))
        elif action == ActionType.MIGRATE:
            dest = next((Position(x, y) for n, x, y in world.regions if n == self.migrating), None)
            if dest is not None:
                self._walking(world, lambda: self.step_towards(dest, world))
        elif action == ActionType.CONTRIBUTE:
            groups.contribute(ctx, self)
        elif action == ActionType.TAKE_STORE:
            groups.take(ctx, self)
        elif action == ActionType.GATHER:
            self._walking(world, lambda: self._gather_step(ctx))
        elif action == ActionType.EXPERIMENT:
            inventions.experiment(ctx, self, self.project["problem"])
        elif action == ActionType.MAKE:
            concept = world.concepts.get(self.project["concept"])
            if concept is not None:
                inventions.make(ctx, self, concept)

        # Hours used (watches): walking counted itself; sleeping, resting and exploring fill
        # the rest of the watch; anything else takes an hour.
        if action in (ActionType.SLEEP, ActionType.REST, ActionType.EXPLORE):
            self.hours_left = 0
        elif self.hours_left == hours_before:
            self.hours_left = max(0, self.hours_left - 1)

        if world.calendar.watches and self.alive:
            self._upkeep(world, tick, log, action)

    def _upkeep(self, world, tick, log, action):
        """Watches only: six hours include meals and drinks along the way, from what they carry
        (one of each at most, and not twice if that was the watch's main action)."""
        r = world.rules
        if (
            self.thirst >= r.top_up_threshold
            and self.inventory["water"] > 0
            and action not in (ActionType.DRINK, ActionType.DRINK_CARRIED)
        ):
            self._drink_carried(world, tick, log)
        if (
            self.hunger >= r.top_up_threshold
            and self.inventory["food"] > 0
            and action not in (ActionType.EAT, ActionType.EAT_CARRIED)
        ):
            self._eat_carried(world, tick, log)

    def _walking(self, world, step):
        """Walk one tile per hour of the watch, until arrived, busy with someone (that hour is
        spent with them), or stuck. In story mode (one hour per tick) it is one call, exactly
        as before M8a."""
        self.route = []  # route caching on: a fresh plan each watch, followed step by step
        while self.hours_left > 0:
            before = self.position
            step()
            self.hours_left -= 1
            if self.position == before or not self.alive:
                break
        self.route = None

    # =================================================================
    # Actions
    # =================================================================

    def _eat(self, world, tick, log):
        food = world.get_food_at(self.position, min_quantity=1)

        if not food:
            return

        r = world.rules
        units = min(r.food_per_meal, food.quantity)
        food.quantity -= units

        self.hunger = max(0, self.hunger - units * self._nourishment(r))
        self._practice("foraging", r.foraging_practice * units / r.food_per_meal, world, tick, log)

        # Pick some to carry, if the patch has plenty left.
        if food.quantity >= r.gather_min_patch_meals * r.food_per_meal:
            room = self.carry_capacity("food", r) - self.inventory["food"]
            take = max(0, min(room, food.quantity - r.food_per_meal))
            food.quantity -= take
            self.inventory["food"] += take

        self._close_need("food", world, tick, log)

    def _eat_carried(self, world, tick, log):
        r = world.rules
        units = min(r.food_per_meal, self.inventory["food"])
        if units <= 0:
            return
        self.inventory["food"] -= units
        self.hunger = max(0, self.hunger - units * self._nourishment(r))
        self._close_need("food", world, tick, log)

    def _nourishment(self, r) -> float:
        value = r.hunger_per_food_unit * (1 + r.foraging_bonus * self.abilities.get("foraging"))
        if self.stage == lifecycle.CHILD:
            value *= r.child_forage_efficiency
        if self.bonus.get("cuts"):  # a cutting tool gets more from a meal's gathering (M8b)
            value *= 1.2
        return value

    def _drink(self, world, tick, log):
        if not world.can_drink_at(self.position):
            return

        self.thirst = max(0, self.thirst - world.rules.thirst_per_drink)
        self.inventory["water"] = self.carry_capacity("water", world.rules)  # fill up
        self._close_need("water", world, tick, log)

    def _drink_carried(self, world, tick, log):
        if self.inventory["water"] <= 0:
            return
        self.inventory["water"] -= 1
        self.thirst = max(0, self.thirst - world.rules.thirst_per_drink)
        self._close_need("water", world, tick, log)

    def _close_need(self, resource, world, tick, log):
        """After eating or drinking: end open 'pressing' and 'suffering' causes."""
        level = getattr(self, NEEDS[resource])

        if level < 100:
            self.suffering_events.pop(resource, None)

        if level < world.rules.seek_threshold and resource in self.pressing_events:
            cause = self.pressing_events.pop(resource)
            self._emit(log, tick, ev.NEED_SATISFIED, data={"need": NEEDS[resource]}, cause_id=cause)

    def _move_to_known(self, resource, world):
        locations = self.memory.get_known_locations(resource)

        if not locations:
            return

        target = min(
            locations,
            key=lambda loc: (self.position.distance(loc.position), loc.position.x, loc.position.y),
        )

        if not self.step_towards(target.position, world):
            # Can't get there: stop believing it's useful.
            self.memory.forget_location(resource, target.position)

    def _explore(self, world, tick, log):
        goal = self.memory.get_exploration_goal()
        if goal is None:
            return

        if goal == ExplorationGoal.FIND_WATER:
            self._practice("water_finding", 0.2, world, tick, log)
        if world.rules.talents and self.talent == "water-sense":  # M8f: their calling
            self._practice("water_finding", 0.15, world, tick, log)

        explorer = ExplorationPlanner(vision_radius=self.vision_radius(world))
        target = self.memory.get_exploration_target()

        # Pick a new target when there is none, it was reached, or it no longer reveals anything.
        if (
            target is None
            or target == self.position
            or explorer.unseen_around(target, world, self.memory) == 0
            or not planner.within_leash(self, world, target)
        ):
            target = explorer.choose_target(self, world)

            if target is None:
                return

            self.memory.set_exploration_target(target)

        if not self.step_towards(target, world):
            self.memory.clear_exploration_target()

    def _socialize(self, ctx):
        observation = self.last_observation

        visible = observation.visible_agents if observation is not None else []
        if ctx.world.rules.scheduled_sleep:  # nobody wakes a sleeper for a chat
            visible = [v for v in visible if not _asleep(ctx.agents.get(v["id"]))]
        if not visible:
            rest = ctx.world.rules.rest_energy * ctx.world.calendar.hours_per_tick
            self.energy = min(100, self.energy + rest)
            return

        target = self._social_target_among(visible)
        other = ctx.agents[target["id"]]

        if self.position.distance(other.position) > 1:
            self.step_towards(target["position"], ctx.world)
            return

        self.talked = True
        interaction.converse(ctx, self, other)

    def _tend(self, ctx):
        """Go to someone hurt or sick and tend them (M8f: a healer's calling)."""
        from genesis.society import economy

        other = ctx.agents.get(self.care_target[0]) if self.care_target else None
        if other is None or not other.alive or not economy.may_heal(ctx, self, other):
            self.hours_left = 0
            return
        if self.position.distance(other.position) > 1:
            self.step_towards(other.position, ctx.world)
            return
        self.talked = True
        economy.heal(ctx, self, other)
        self.hours_left = 0

    def _social_target_among(self, visible):
        """Generous agents go to someone who looks in need; otherwise to the nearest person."""

        friend = None  # M8f: a plan to spend time with someone
        if self.plan and self.plan["focus"].startswith("company:"):
            friend = int(self.plan["focus"].split(":")[1])

        def key(other):
            if other["id"] == friend:
                return (False, -1, other["id"])
            needy = bool(other["looks"]) and self.genome.generosity >= 0.6
            return (not needy, self.position.distance(other["position"]), other["id"])

        return min(visible, key=key)

    def _care(self, ctx):
        """Walk to the child; when next to them, hand over food or water."""
        if self.care_target is None:
            return
        child_id, resource = self.care_target
        child = ctx.agents.get(child_id)
        if child is None or not child.alive:
            return
        if self.position.distance(child.position) > 1:
            self.step_towards(child.position, ctx.world)
            return
        given = interaction.give(ctx, self, child, resource)
        self._care_done(ctx, child, given)

    def _gather_step(self, ctx):
        """Walk to the gathering spot; there, an hour of gathering."""
        spot = self.project.get("spot") if self.project else None
        if spot is None:
            return
        target = Position(*spot)
        if self.position != target:
            self.step_towards(target, ctx.world)
            return
        inventions.gather(ctx, self)

    def _care_done(self, ctx, child, given):
        o = self.order
        if given is not None and o is not None and o["kind"] == "help" and o["target"] == child.id:
            groups.order_fulfilled(ctx, self, "help", given)

    def _follow(self, ctx):
        if self.care_target is None:
            return
        parent = ctx.agents.get(self.care_target[0])
        if parent is not None and parent.alive:
            self.step_towards(parent.position, ctx.world)

    def _ask_for_help(self, ctx):
        if self.help_request is None:
            return
        target_id, kind, resource = self.help_request
        other = ctx.agents.get(target_id)
        if other is None or not other.alive:
            return
        if ctx.world.rules.scheduled_sleep and _asleep(other):
            return  # asleep: try someone else, or something else, next time

        if self.position.distance(other.position) > 1:
            self.step_towards(other.position, ctx.world)
            return

        self.asked[f"{target_id}:{kind}:{resource}"] = ctx.tick
        self.talked = True
        interaction.converse(ctx, self, other, purpose=(kind, resource))

    def fade_feelings(self, log, tick):
        """Once a day: gratitude, grievance and fear fade a little."""
        for other_id in sorted(self.relationships):
            rel = self.relationships[other_id]
            before = rel.label()
            rel.decay()
            after = rel.label()
            if after != before:
                self._emit(
                    log,
                    tick,
                    ev.RELATION_CHANGED,
                    targets=(other_id,),
                    data={"from": before, "to": after, "name": rel.target_name, "faded": True},
                )

    def _check_told_places(self, observation, ctx):
        """Look at places others told me about. A place that isn't what they said was a lie."""
        world = ctx.world
        visible = set(observation.visible_tiles)
        patches = set(observation.visible_patches)

        for key in list(self.memory.told):
            resource, x, y = key
            position = Position(x, y)
            if position not in visible:
                continue

            teller_id, message_id = self.memory.told.pop(key)
            real = world.can_drink_at(position) if resource == "water" else position in patches
            if real:
                continue  # confirmed with my own eyes

            self.memory.forget_location(resource, position)
            teller = ctx.agents[teller_id]
            found_out = self._emit(
                ctx.log,
                ctx.tick,
                ev.LIE_DISCOVERED,
                targets=(teller_id,),
                position=position,
                data={"resource": resource, "name": teller.name},
                cause_id=message_id,
            )
            interaction.feel(ctx, self, teller, "lied_to", found_out)

    # =================================================================
    # Helpers
    # =================================================================

    def step_towards(self, target: Position, world) -> bool:
        """Move one tile along the shortest path. False if unreachable."""
        if target == self.position:
            return True

        route = self.route
        if route and len(route) >= 2 and route[0] == self.position and route[-1] == target:
            path = route  # still on the path planned earlier in this watch
        else:
            path = self.navigator.find_path(self.position, target, world)

        if not path or len(path) < 2:
            return False

        nxt = path[1]
        if self.route is not None:  # only while walking within one watch
            self.route = path[1:]
        dx, dy = nxt.x - self.position.x, nxt.y - self.position.y
        self.facing = "right" if dx > 0 else "left" if dx < 0 else "down" if dy > 0 else "up"
        self.position = nxt
        self.memory.remember_position(self.position)
        return True

    def _practice(self, skill, amount, world, tick, log):
        if world.rules.talents and self.talent:
            from genesis.agents import talents

            amount *= talents.learning(self, skill, world.rules)
        reached = self.abilities.practice(
            skill, amount, self.genome.intelligence, world.rules.skill_learning_rate
        )
        if reached:
            value = self.abilities.get(skill)
            self._emit(
                log,
                tick,
                ev.SKILL_IMPROVED,
                data={
                    "skill": skill,
                    "level": reached,
                    "description": describe_skill(skill, value),
                },
            )

    def _update_stage(self, world, tick, log):
        stage = lifecycle.life_stage(
            self.age(tick), self.genome, world.rules, world.calendar.ticks_per_day
        )
        if stage != self.stage:
            self.stage = stage
            self.stage_event = self._emit(
                log,
                tick,
                ev.LIFE_STAGE,
                data={"stage": stage, "age_days": world.calendar.age_days(self.age(tick))},
            )

    def _update_resource_memory(self, observation, world, log):
        tick = observation.tick

        for food in observation.visible_food:
            if self.memory.remember_location("food", food.position):
                self._emit(
                    log,
                    tick,
                    ev.DISCOVERED,
                    position=food.position,
                    data={"resource": "food"},
                    cause_id=self.pressing_events.get("food"),
                )

        # Forget food sources that are in view but no longer hold a meal.
        visible_food = {food.position for food in observation.visible_food}
        seen = set(observation.visible_tiles)

        for loc in self.memory.get_known_locations("food"):
            if loc.position in seen and loc.position not in visible_food:
                self.memory.forget_location("food", loc.position)

        # ...and shores in view that have dried up (a drought, M7).
        visible_water = set(observation.visible_water)
        for loc in self.memory.get_known_locations("water"):
            if loc.position in seen and loc.position not in visible_water:
                self.memory.forget_location("water", loc.position)

        first_water = not any(k.resource_type == "water" for k in self.memory.discovered)

        for shore in observation.visible_water:
            self.memory.remember_location("water", shore)

        if observation.visible_water and first_water:
            self._emit(
                log,
                tick,
                ev.DISCOVERED,
                position=observation.visible_water[0],
                data={"resource": "water"},
                cause_id=self.pressing_events.get("water"),
            )
            self._practice("water_finding", 3, world, tick, log)

    def _die(self, tick, log, cause, cause_id):
        self.cause_of_death = cause
        self._emit(log, tick, ev.DIED, data={"cause": cause}, cause_id=cause_id)
        self.alive = False
        self.sleeping = False
        self.current_action = None
        self.died_tick = tick

    def _emit(self, log, tick, type, targets=(), position=None, data=None, cause_id=None):
        """Record an event this agent did or experienced, in the world log and its memory."""
        event_id = log.emit(
            tick,
            type,
            actors=(self.id,),
            targets=targets,
            position=position if position is not None else self.position,
            data=data,
            cause_id=cause_id,
        )
        self.memory.remember_event(event_id)
        return event_id

    # =================================================================
    # Saving and loading
    # =================================================================

    def to_state(self) -> dict:
        """Everything needed to continue this agent exactly, except what the database keeps
        in its own columns and tables (id, name, position, needs, genes, skills)."""
        return {
            "sleeping": self.sleeping,
            "stage": self.stage,
            "stage_event": self.stage_event,
            "memory": self.memory.to_dict(),
            "pressing_events": self.pressing_events,
            "suffering_events": self.suffering_events,
            "inventory": self.inventory,
            "facing": self.facing,
            "asked": self.asked,
            "story": self.story,
            "partner": self.partner,
            "children": self.children,
            "next_mate_tick": self.next_mate_tick,
            "pregnant": self.pregnant,
            "group_id": self.group_id,
            "order": self.order,
            "materials": self.materials,
            "items": self.items,
            "knowledge": sorted(self.knowledge),
            "problems": self.problems,
            "bonus": self.bonus,
            "goal": self.goal,
            "deeds": self.deeds,
            "talent": self.talent,
            "work": self.work,
            "market": self.market,
            "owes": self.owes,
            "sick_until": self.sick_until,
            "sick_event": self.sick_event,
            "immune_until": self.immune_until,
            "home_region": self.home_region,
            "away_days": self.away_days,
            "migrating": self.migrating,
            "plan": self.plan,
        }

    @classmethod
    def from_state(
        cls, columns: dict, state: dict, genome: Genome, abilities: dict, relationships=None
    ) -> "Agent":
        parents = tuple(p for p in (columns.get("parent_a"), columns.get("parent_b")) if p)
        return cls(
            id=columns["id"],
            name=columns["name"],
            position=Position(columns["x"], columns["y"]),
            genome=genome,
            born_tick=columns["born_tick"],
            generation=columns["generation"],
            parents=parents,
            hunger=columns["hunger"],
            thirst=columns["thirst"],
            energy=columns["energy"],
            health=columns["health"],
            alive=bool(columns["alive"]),
            sleeping=state["sleeping"],
            died_tick=columns["died_tick"],
            cause_of_death=columns["cause_of_death"],
            stage=state["stage"],
            stage_event=state["stage_event"],
            memory=AgentMemory.from_dict(state["memory"]),
            abilities=Abilities(abilities),
            current_action=(
                ActionType(columns["current_action"]) if columns.get("current_action") else None
            ),
            pressing_events=dict(state["pressing_events"]),
            suffering_events=dict(state["suffering_events"]),
            inventory=dict(state["inventory"]),
            facing=state["facing"],
            asked=dict(state["asked"]),
            story=state.get("story", ""),
            partner=state.get("partner"),
            children=list(state.get("children", [])),
            next_mate_tick=state.get("next_mate_tick", 0),
            pregnant=state.get("pregnant", False),
            group_id=state.get("group_id"),
            order=state.get("order"),
            materials=dict(state.get("materials", {})),
            items=[dict(i) for i in state.get("items", [])],
            knowledge=set(state.get("knowledge", [])),
            problems=dict(state.get("problems", {})),
            bonus=dict(state.get("bonus", {})),
            goal=dict(state["goal"]) if state.get("goal") else None,
            deeds=dict(state.get("deeds", {})),
            talent=state.get("talent"),
            work=dict(state.get("work", {})),
            market={k: list(v) for k, v in state.get("market", {}).items()},
            owes=[dict(d) for d in state.get("owes", [])],
            sick_until=state.get("sick_until"),
            sick_event=state.get("sick_event"),
            immune_until=state.get("immune_until", 0),
            home_region=state.get("home_region"),
            away_days=state.get("away_days", 0),
            migrating=state.get("migrating"),
            plan=dict(state["plan"]) if state.get("plan") else None,
            relationships={r.target_id: r for r in (relationships or [])},
        )

    def comparable(self) -> tuple:
        """Everything that defines this agent, for determinism checks."""
        return (
            self.id,
            self.name,
            self.position,
            self.genome,
            self.born_tick,
            self.hunger,
            self.thirst,
            self.energy,
            self.health,
            self.alive,
            self.died_tick,
            self.cause_of_death,
            tuple(sorted(self.abilities.to_dict().items())),
            repr(self.to_state()),
            tuple(r.to_dict().items().__repr__() for _, r in sorted(self.relationships.items())),
        )
