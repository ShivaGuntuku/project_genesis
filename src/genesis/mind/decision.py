from enum import Enum

from genesis.agents import rhythm
from genesis.mind.memory import ExplorationGoal
from genesis.mind.utility import UtilityScorer
from genesis.world.world import Position


class ActionType(Enum):
    EAT = "eat"
    DRINK = "drink"
    SLEEP = "sleep"
    MOVE_TO_FOOD = "move_to_food"
    MOVE_TO_WATER = "move_to_water"
    EXPLORE = "explore"
    SOCIALIZE = "socialize"
    REST = "rest"
    EAT_CARRIED = "eat_carried"
    DRINK_CARRIED = "drink_carried"
    ASK_FOR_HELP = "ask_for_help"  # walk to someone and ask (where is it? / can you spare some?)
    CARE = "care"  # a parent takes food or water to their child (M6)
    FOLLOW = "follow"  # a child keeps close to a parent (M6)
    GO_HOME = "go_home"  # back to the group's home (M7)
    CONTRIBUTE = "contribute"  # put a meal into the group's store
    TAKE_STORE = "take_store"  # take a meal from the group's store
    GATHER = "gather"  # gather a material (M8b)
    EXPERIMENT = "experiment"  # try a way of making something (M8b)
    MAKE = "make"  # make a thing they know how to make (M8b)
    MIGRATE = "migrate"  # set out for another valley (M8e)
    TEND = "tend"  # go and tend someone hurt or sick (M8f: a healer's calling)


class DecisionEngine:
    """Rule-based choice. Reads the agent and its observation; never changes the world."""

    def __init__(self):
        self.utility = UtilityScorer()

    def decide(self, agent, observation, world):
        if not agent.alive:
            return None
        self._world = world

        r = world.rules
        agent.last_scores = {}
        agent.help_request = None
        agent.care_target = None
        agent.project = None
        urgent = agent.thirst >= r.urgent_threshold or agent.hunger >= r.urgent_threshold

        # Sleep on a schedule (millennium worlds): their own hours, unless need is urgent.
        if r.scheduled_sleep and not urgent and observation is not None:
            cal = world.calendar
            hours = cal.hours_per_tick
            # Not if thirst or hunger would turn urgent while they sleep.
            urgent_soon = (
                agent.thirst + r.thirst_per_tick * hours >= r.urgent_threshold
                and not agent.inventory.get("water")
            ) or (
                agent.hunger + r.hunger_per_tick * hours >= r.urgent_threshold
                and not agent.inventory.get("food")
            )
            if not urgent_soon and rhythm.asleep_at(
                agent.genome, r, cal.hour(observation.tick), hours
            ):
                return self._done_searching(agent, ActionType.SLEEP)
            if agent.sleeping and agent.energy < r.sleep_at_energy:  # exhausted: sleep on
                return ActionType.SLEEP
        # Keep sleeping until rested, unless thirst or hunger becomes urgent.
        elif agent.sleeping and agent.energy < r.wake_at_energy and not urgent:
            return ActionType.SLEEP

        # Worst need first (thirst wins ties: it kills faster).
        needs = [("water", agent.thirst), ("food", agent.hunger)]
        needs.sort(key=lambda item: -item[1])
        worst_resource, worst_level = needs[0]

        # 1. A critical need beats everything.
        if worst_level >= r.urgent_threshold:
            return self._satisfy(agent, worst_resource, world, observation, urgent=True)

        # 2. Fill up on whatever is right here (saves walking back and forth).
        for resource, level in needs:
            if level >= r.top_up_threshold and self._available_here(resource, agent, world):
                return self._done_searching(agent, self._consume(resource))

        # 2b. A parent feeds a hungry or thirsty child nearby, if carrying what it needs
        #     (or a member helps someone their leader told them to help).
        care = self._child_in_need(agent, observation, r) or self._ordered_help(agent, observation)
        if care is not None:
            agent.care_target = care
            return self._done_searching(agent, ActionType.CARE)

        # 3. Go and find the worst need, unless the other need won't last the round trip.
        if worst_level >= r.seek_threshold:
            other = "food" if worst_resource == "water" else "water"

            if not self._trip_is_safe(agent, worst_resource, other, r):
                return self._satisfy(agent, other, world, observation)

            return self._satisfy(agent, worst_resource, world, observation)

        # 3b. Group duties: an order to bring food, or a full pack at home.
        duty = self._group_duty(agent, r)
        if duty is not None:
            return self._done_searching(agent, duty)

        if agent.energy <= r.sleep_at_energy:
            return self._done_searching(agent, ActionType.SLEEP)

        if agent.health <= r.rest_at_health:
            return self._done_searching(agent, ActionType.REST)

        return self._free_time(agent, observation, r)

    # --------------------------------

    @staticmethod
    def _trip_is_safe(agent, going_for, other, r) -> bool:
        """Would `other` stay below the safety limit on the way to `going_for` and back?

        Only judged when both are known; unknown places can't be planned around.
        """
        targets = agent.memory.get_known_locations(going_for)
        returns = agent.memory.get_known_locations(other)

        if not targets or not returns:
            return True

        target = min(targets, key=lambda loc: agent.position.distance(loc.position)).position
        back = min(returns, key=lambda loc: target.distance(loc.position)).position
        trip = agent.position.distance(target) + target.distance(back)

        level = agent.thirst if other == "water" else agent.hunger
        rate = r.thirst_per_tick if other == "water" else r.hunger_per_tick

        return level + rate * trip < r.trip_safety_limit

    def _satisfy(self, agent, resource, world, observation, urgent=False):
        """Here → carried → (urgent: ask a nearby carrier) → known place → ask → explore."""
        if self._available_here(resource, agent, world):
            return self._done_searching(agent, self._consume(resource))

        if agent.inventory.get(resource, 0) > 0:
            return self._done_searching(
                agent, ActionType.DRINK_CARRIED if resource == "water" else ActionType.EAT_CARRIED
            )

        r = world.rules
        if urgent:
            carrier = self._helper(
                agent,
                observation,
                resource,
                "item",
                r,
                max_distance=r.ask_item_max_distance,
                cooldown=world.calendar.ticks(hours=r.ask_cooldown_ticks),
            )
            if carrier is not None:
                return self._ask(agent, carrier, "item", resource)

        store = self._from_store(agent, resource, r)
        if store is not None:
            return self._done_searching(agent, store)

        if agent.memory.get_known_locations(resource):
            return self._seek(agent, resource)

        informant = self._helper(
            agent,
            observation,
            resource,
            "info",
            r,
            cooldown=world.calendar.ticks(hours=r.ask_cooldown_ticks),
        )
        if informant is not None:
            return self._ask(agent, informant, "info", resource)

        return self._seek(agent, resource)

    @staticmethod
    def _helper(agent, observation, resource, kind, r, max_distance=None, cooldown=None):
        """A visible agent worth asking, not asked about this recently. Prefers friends."""
        if observation is None or agent.talked:
            return None
        best = None
        for other in observation.visible_agents:
            if kind == "item" and not other["carrying"].get(resource):
                continue
            last = agent.asked.get(f"{other['id']}:{kind}:{resource}")
            wait = cooldown if cooldown is not None else r.ask_cooldown_ticks
            if last is not None and observation.tick - last < wait:
                continue
            distance = agent.position.distance(other["position"])
            if max_distance is not None and distance > max_distance:
                continue
            rel = agent.relationships.get(other["id"])
            key = (-(rel.trust if rel else 0.3), distance, other["id"])
            if best is None or key < best[0]:
                best = (key, other["id"])
        return best[1] if best else None

    @staticmethod
    def _ask(agent, target_id, kind, resource):
        agent.memory.clear_exploration_goal()
        agent.help_request = (target_id, kind, resource)
        return ActionType.ASK_FOR_HELP

    @staticmethod
    def _available_here(resource, agent, world) -> bool:
        if resource == "water":
            return world.can_drink_at(agent.position)
        return world.get_food_at(agent.position) is not None

    @staticmethod
    def _consume(resource):
        return ActionType.DRINK if resource == "water" else ActionType.EAT

    @staticmethod
    def _done_searching(agent, action):
        agent.memory.clear_exploration_goal()
        return action

    @staticmethod
    def _seek(agent, resource):
        if agent.memory.get_known_locations(resource):
            agent.memory.clear_exploration_goal()
            if resource == "water":
                return ActionType.MOVE_TO_WATER
            return ActionType.MOVE_TO_FOOD

        if resource == "water":
            agent.memory.set_exploration_goal(ExplorationGoal.FIND_WATER)
        else:
            agent.memory.set_exploration_goal(ExplorationGoal.FIND_FOOD)
        return ActionType.EXPLORE

    @staticmethod
    def _child_in_need(agent, observation, r):
        if not agent.children or observation is None:
            return None
        best = None
        for other in observation.visible_agents:
            if other["id"] not in agent.children:
                continue
            distance = agent.position.distance(other["position"])
            if distance > r.care_distance:
                continue
            for resource, word in (("water", "thirsty"), ("food", "hungry")):
                if word in other["looks"] and agent.inventory.get(resource, 0) > 0:
                    key = (distance, other["id"])
                    if best is None or key < best[0]:
                        best = (key, (other["id"], resource))
                    break
        return best[1] if best else None

    @staticmethod
    def _ordered_help(agent, observation):
        o = agent.order
        if o is None or o["kind"] != "help" or observation is None:
            return None
        for other in observation.visible_agents:
            if other["id"] != o["target"]:
                continue
            for resource, word in (("water", "thirsty"), ("food", "hungry")):
                if word in other["looks"] and agent.inventory.get(resource, 0) > 0:
                    return (other["id"], resource)
        return None

    @staticmethod
    def _group_duty(agent, r):
        g = agent.group
        if g is None or agent.stage == "child":
            return None
        meal = r.food_per_meal
        ordered = agent.order is not None and agent.order["kind"] == "gather"
        providing = agent.goal is not None and agent.goal["need"] == "provide"  # M8c
        providing = providing or (agent.plan or {}).get("focus") == "provide"  # M8f
        providing = providing or (r.talents and agent.talent in ("strong-armed", "tireless"))
        spare = agent.inventory.get("food", 0) >= (meal if ordered or providing else 3 * meal)
        if not spare or agent.hunger >= r.seek_threshold:
            return None
        if agent.position.distance(g.home) <= 1:
            return ActionType.CONTRIBUTE
        return ActionType.GO_HOME if ordered or providing else None

    @staticmethod
    def _from_store(agent, resource, r):
        """Food from the group's store, if home is nearer than any known berry patch."""
        g = agent.group
        if resource != "food" or g is None or g.store_food < r.food_per_meal:
            return None
        home = agent.position.distance(g.home)
        known = agent.memory.get_known_locations("food")
        if known and min(agent.position.distance(k.position) for k in known) < home:
            return None
        return ActionType.TAKE_STORE if home <= 1 else ActionType.GO_HOME

    @staticmethod
    def _parent_far_away(agent, observation, r):
        """A child's nearest visible parent (or an adult's partner), if beyond follow distance."""
        if observation is None:
            return None
        if agent.stage == "child":
            wanted = set(agent.parents)
        elif agent.partner is not None:
            wanted = {agent.partner}
        else:
            return None
        parents = [o for o in observation.visible_agents if o["id"] in wanted]
        if not parents:
            return None
        nearest = min(parents, key=lambda o: (agent.position.distance(o["position"]), o["id"]))
        if agent.position.distance(nearest["position"]) <= r.child_follow_distance:
            return None
        return nearest["id"]

    @staticmethod
    def _project(agent, world, r, tick):
        """Invention (M8b): work on what keeps going wrong. Make the thing if they know how,
        else, if curious, gather materials and experiment."""
        from genesis.culture import inventions as inv
        from genesis.culture.materials import SOLVES

        problem = inv.main_problem(agent, r)
        goal = agent.goal["need"] if agent.goal else None
        driven = goal in SOLVES  # a goal makes them work on it, curious or not (M8c)
        if problem is None and driven:
            problem = goal
        focus = agent.plan["focus"] if agent.plan else None
        # M8f: a plan to tinker, or a tinkerer's calling, makes any trouble worth working on.
        keen = focus == "tinker" or (r.talents and agent.talent == "tinkerer")
        if focus and focus.startswith("make:"):
            c = world.concepts.get(int(focus.split(":")[1]))
            if c is not None and c.function in SOLVES.values():
                problem = next(p for p, f in SOLVES.items() if f == c.function)
                driven, goal = True, problem
        if problem is None and keen:
            pressing = sorted(
                ((s, p) for p, s in agent.problems.items() if p in SOLVES and s > 0), reverse=True
            )
            problem = pressing[0][1] if pressing else ("carry food" if focus == "tinker" else None)
            driven, goal = problem is not None, problem
        if problem is None or agent.stage == "child":
            return None
        target = SOLVES[problem]
        concepts = world.concepts
        known = inv.known_for(agent, concepts, target)
        if known:
            if target == "shelter":
                if inv.sheltered(agent, world.structures):
                    return None
            elif inv.has_item_for(agent, concepts, target):
                return None
            concept = known[0]
            missing = inv.missing_material(agent, concept)
            if missing is None:
                agent.project = {"concept": concept.id}
                return ActionType.MAKE
            spot = inv.gather_spot(agent, world, missing)
            if spot is None:
                return None
            agent.project = {"material": missing, "spot": [spot.x, spot.y]}
            return ActionType.GATHER

        if agent.genome.curiosity < r.tinker_min_curiosity and not (driven and problem == goal):
            return None
        seen = agent.asked.get(f"seen:{target}")
        if seen is not None and tick - seen < world.calendar.ticks(days=r.learn_wait_days):
            return None  # someone nearby knows how: better to learn it from them
        last = agent.asked.get("tinkered")
        wait = r.tinker_cooldown_days / max(0.25, agent.genome.curiosity)  # the less curious
        if last is not None and tick - last < world.calendar.ticks(days=wait):  # try less often
            return None
        kinds = inv.material_kinds(agent)
        full = inv.carrying_materials(agent) >= r.materials_capacity
        if len(kinds) >= 3 or (full and kinds):
            agent.project = {"problem": problem}
            return ActionType.EXPERIMENT
        spot = inv.gather_spot(agent, world, None)
        if spot is not None:
            agent.project = {"material": None, "spot": [spot.x, spot.y]}
            return ActionType.GATHER
        if kinds:
            agent.project = {"problem": problem}
            return ActionType.EXPERIMENT
        return None

    def _migration(self, agent, r):
        """M8e: leaving a valley in trouble for a neighbouring one, or keeping on the way.
        Only the bold set out, and only fed, watered and carrying water."""
        w = self._world
        starts = {name: Position(x, y) for name, x, y in w.regions}
        if agent.migrating is not None:
            dest = starts.get(agent.migrating)
            here = w.region_at.get(agent.position)
            if dest is None or (here == agent.migrating and agent.position.distance(dest) <= 6):
                agent.migrating = None
                return None
            return ActionType.MIGRATE
        here = w.region_at.get(agent.position)
        planned = (agent.plan or {}).get("move")  # M8f: they decided to leave
        if here not in w.troubled:
            return None
        if agent.genome.risk_tolerance < r.migrate_min_risk and not planned:
            return None
        if agent.inventory.get("water", 0) < 1 or agent.thirst > 30 or agent.hunger > 50:
            return None
        options = [n for n in w.region_names if n != here and n not in w.troubled and n in starts]
        if not options:
            return None
        dest_name = min(options, key=lambda n: (agent.position.distance(starts[n]), n))
        if planned in options:
            dest_name = planned
        dest = starts[dest_name]
        if not w.shore:
            return None
        shore = min(w.shore, key=lambda p: (p.distance(dest), p.x, p.y))
        # Only if the walk (through the pass) can be made on the water they carry: an hour a
        # tile, with a margin, before thirst turns urgent.
        path = agent.navigator.find_path(agent.position, shore, w)
        hours = r.urgent_threshold - agent.thirst + agent.inventory["water"] * r.thirst_per_drink
        if not path or len(path) * r.thirst_per_tick > 0.8 * hours:
            return None
        agent.migrating = dest_name
        # What they have heard of the land beyond the ridge: where its lake and berries are.
        agent.memory.remember_location("water", shore, seen=False)
        berries = [f for f in w.food_sources if w.food_region.get(f.id) == agent.migrating]
        if berries:
            near = min(berries, key=lambda f: (f.position.distance(dest), f.id))
            agent.memory.remember_location("food", near.position, seen=False)
        return ActionType.MIGRATE

    def _plan_and_calling(self, agent, observation, r, scores):
        """M8f: lean free time towards their plan (LLM) and their talent's work."""
        focus = agent.plan["focus"] if agent.plan else ""
        talent = agent.talent if r.talents else None
        calling = 1.0 if focus == "calling" else 0.0  # only when they chose it (their plan)
        social = ActionType.SOCIALIZE in scores
        if focus == "explore":
            scores[ActionType.EXPLORE] += 0.6
        elif focus == "rest":
            scores[ActionType.REST] += 0.4
        elif (focus == "trade" or focus.startswith("company:")) and social:
            scores[ActionType.SOCIALIZE] += 0.5
        if talent == "healer" and observation is not None:  # someone in view needs tending
            for other in observation.visible_agents:
                if ("hurt" in other["looks"] or "sick" in other["looks"]) and (
                    agent.asked.get(f"healed:{other['id']}", -1)
                    != observation.tick // self._world.calendar.ticks_per_day
                ):
                    agent.care_target = (other["id"], None)
                    return
        if talent in ("keen-eyed", "water-sense"):
            scores[ActionType.EXPLORE] += 0.3 * calling
        elif talent == "storyteller" and social:
            scores[ActionType.SOCIALIZE] += 0.3 * calling

    def _free_time(self, agent, observation, r=None):
        if (
            r is not None
            and r.disasters
            and agent.stage != "child"
            and len(self._world.regions) > 1
        ):
            move = self._migration(agent, r)
            if move is not None:
                agent.memory.clear_exploration_goal()
                return move
        if r is not None and r.inventions and observation is not None:
            project = self._project(agent, self._world, r, observation.tick)
            if project is not None:
                agent.memory.clear_exploration_goal()
                return project
        if r is not None:
            parent = self._parent_far_away(agent, observation, r)
            if parent is not None:
                agent.care_target = (parent, None)
                agent.memory.clear_exploration_goal()
                return ActionType.FOLLOW

        scores = {
            ActionType.EXPLORE: self.utility.score_explore(agent),
            ActionType.REST: self.utility.score_rest(agent),
        }
        g = agent.group
        if r is not None and g is not None and agent.position.distance(g.home) > r.home_radius:
            scores[ActionType.GO_HOME] = 0.4 + 0.3 * agent.genome.sociability

        # Socializing needs someone to socialize with.
        if observation.visible_agents and not agent.talked:  # one conversation per watch
            scores[ActionType.SOCIALIZE] = self.utility.score_socialize(agent)

        # Trouble at home (a disaster, too many people) makes people look further afield (M8e).
        if r is not None and self._world.troubled:
            here = self._world.region_at.get(agent.position)
            if here in self._world.troubled:
                scores[ActionType.EXPLORE] += 0.3 + 0.4 * agent.genome.risk_tolerance

        # Their own plan for these days (M8f, chosen by the LLM) and their talent's calling.
        if r is not None:
            self._plan_and_calling(agent, observation, r, scores)
            if agent.care_target is not None:
                return ActionType.TEND

        # A goal pulls free time towards it (M8c).
        need = agent.goal["need"] if r is not None and r.goals and agent.goal else None
        if agent.plan and agent.plan["focus"] == "goal":
            if need == "know the land":
                scores[ActionType.EXPLORE] += 0.3
            elif ActionType.SOCIALIZE in scores:
                scores[ActionType.SOCIALIZE] += 0.2
        if need == "know the land":
            scores[ActionType.EXPLORE] += 0.5
        elif need in ("be respected", "be remembered") and ActionType.SOCIALIZE in scores:
            scores[ActionType.SOCIALIZE] += 0.4

        agent.last_scores = {action.value: round(score, 3) for action, score in scores.items()}

        # max() keeps the first of equal scores, so ties are deterministic.
        best = max(scores, key=scores.get)

        if best == ActionType.EXPLORE:
            agent.memory.set_exploration_goal(ExplorationGoal.GENERAL)
        else:
            agent.memory.clear_exploration_goal()

        return best
