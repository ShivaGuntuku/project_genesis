from dataclasses import dataclass, field
from random import Random

from genesis.history.events import EventLog


@dataclass
class TickContext:
    """Everything an agent may use while acting in one tick."""

    world: object
    tick: int
    log: EventLog
    rng: Random
    agents: dict = field(default_factory=dict)  # id -> Agent
    # (tick, source_id, target_id, dimension, old, new, event_id), saved with the tick
    relationship_history: list = field(default_factory=list)
    mind: object = None  # None: the rules decide everything; else an LLM or replay mind
    llm_calls: list = field(default_factory=list)  # records of model calls, saved with the tick
    points: int = 0  # decision points so far this tick
    pregnancies: list = field(default_factory=list)  # the simulation's (shared, not a copy)
    groups: dict = field(default_factory=dict)  # id -> Group (the simulation's, shared; M7)

    def next_point(self) -> int:
        self.points += 1
        return self.points

    @property
    def day(self) -> int:
        # Days since the world began, counted from its first tick (not from midnight): used
        # for once-a-day limits. Kept this way so story worlds replay exactly as before M8a.
        return self.tick // self.world.calendar.ticks_per_day
