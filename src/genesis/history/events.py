"""The world's history: every notable event, with a link to what caused it."""

from dataclasses import dataclass, field

# Event types used so far. Keep this list in sync with docs/06.
WORLD_CREATED = "world_created"
FOUNDER_ARRIVED = "founder_arrived"
NEED_BECAME_PRESSING = "need_became_pressing"  # thirst or hunger crossed the seek threshold
NEED_SATISFIED = "need_satisfied"
DISCOVERED = "discovered"  # first time an agent learns of a resource
MET = "met"  # first time two agents see each other
SUFFERING = "suffering"  # started losing health from thirst or hunger
DIED = "died"
LIFE_STAGE = "life_stage"  # came of age, or became an elder
SKILL_IMPROVED = "skill_improved"  # reached a new skill level (capable / skilled / expert)
MESSAGE = "message"  # one speech act from one agent to another (also stored in `messages`)
RELATION_CHANGED = "relation_changed"  # a relationship label changed (e.g. became a friend)
LIE_DISCOVERED = "lie_discovered"  # found out a told location was false
CONCEIVED = "conceived"  # two partners are expecting a child (M6)
BIRTH = "birth"  # a child was born; cause chain leads back to the agreement
PREGNANCY_LOST = "pregnancy_lost"  # the parent carrying the child died first
GRIEVED = "grieved"  # someone in the family died
WORLD_CHANGED = "world_changed"  # a scheduled drought or famine began or ended (M7)
FOUNDED_GROUP = "founded_group"  # M7: groups
JOINED_GROUP = "joined_group"
LEFT_GROUP = "left_group"
GROUP_ENDED = "group_ended"
LEADER_CHANGED = "leader_changed"  # succession (the leader died or left)
LEADER_CHALLENGED = "leader_challenged"  # a challenge and its result (won or not)
GROUP_STORE = "group_store"  # food put into or taken from a group's store
TRESPASS = "trespass"  # an outsider in a group's home area (once per outsider, group, day)
ORDER_GIVEN = "order_given"  # with the member's answer (obeyed / refused)
ORDER_DONE = "order_done"
FIGHT = "fight"  # M7: conflict
EXPERIMENTED = "experimented"  # M8b: a try that didn't work (what worked is "invented")
INVENTED = "invented"  # a new concept, named in the valley's words
MADE = "made"  # made a thing someone invented
BUILT = "built"  # a structure placed in the world (a shelter)
LEARNED = "learned"  # after being taught (data.learned says whether it stuck)
CONCEPT_LOST = "concept_lost"  # nobody alive knows it any more
GOAL_SET = "goal_set"  # M8c: drives and goals
GOAL_REACHED = "goal_reached"
GOAL_ABANDONED = "goal_abandoned"
PROMISE_BROKEN = "promise_broken"  # M8d: economy
CURRENCY_EMERGED = "currency_emerged"
DISASTER = "disaster"  # M8e: started or ended (data.started)
FELL_SICK = "fell_sick"
RECOVERED = "recovered"
BEAST_ATTACK = "beast_attack"
MIGRATED = "migrated"
PLANNED = "planned"  # M8f: their own plan for the coming days
INTERVENTION = "intervention"  # M9: God Mode (origin = god)
REFLECTED = "reflected"  # an agent's nightly reflection: new beliefs, self-story, feelings (M5)

NATURAL = "natural"
SYSTEM = "system"  # created by the program (e.g. world creation), invisible to agents
GOD = "god"  # creator interventions (milestone 9)


@dataclass(frozen=True)
class Event:
    id: int
    tick: int
    type: str
    actors: tuple[int, ...] = ()
    targets: tuple[int, ...] = ()
    x: int | None = None
    y: int | None = None
    data: dict = field(default_factory=dict, hash=False, compare=True)
    cause_id: int | None = None
    origin: str = NATURAL


class EventLog:
    """Append-only log. Ids are sequential, so the same run always produces the same ids."""

    def __init__(self, next_id: int = 1, counts: dict | None = None):
        self.next_id = next_id
        self.counts: dict[str, int] = dict(counts or {})  # events so far, by type (and act)
        self.pending: list[Event] = []  # created since the last save
        self.recent: list[Event] = []  # kept in memory for live views
        self.max_recent = 500

    def emit(
        self,
        tick: int,
        type: str,
        actors=(),
        targets=(),
        position=None,
        data=None,
        cause_id: int | None = None,
        origin: str = NATURAL,
    ) -> int:
        event = Event(
            id=self.next_id,
            tick=tick,
            type=type,
            actors=tuple(actors),
            targets=tuple(targets),
            x=position.x if position is not None else None,
            y=position.y if position is not None else None,
            data=dict(data or {}),
            cause_id=cause_id,
            origin=origin,
        )
        self.next_id += 1
        self.counts[type] = self.counts.get(type, 0) + 1
        if type == MESSAGE:
            key = f"message.{event.data.get('act')}"
            self.counts[key] = self.counts.get(key, 0) + 1
            if event.data.get("truthful") is False:
                self.counts["message.lie"] = self.counts.get("message.lie", 0) + 1
        self.pending.append(event)
        self.recent.append(event)

        if len(self.recent) > self.max_recent:
            del self.recent[: len(self.recent) - self.max_recent]

        return event.id

    def take_pending(self) -> list[Event]:
        events, self.pending = self.pending, []
        return events
