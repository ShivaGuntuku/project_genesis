"""Shared pieces of the dashboard: the read-only world and small helpers."""

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from genesis.config import WorldConfig
from genesis.dashboard.language import LanguageStats, load_dictionary
from genesis.history import events as ev
from genesis.history.describe import clock, describe
from genesis.persistence.db import WorldStore
from genesis.simulation.calendar import calendar_of

PAGE = 25
MAJOR_EVENTS = (
    ev.DISASTER,
    ev.MIGRATED,
    ev.INVENTED,
    ev.CONCEPT_LOST,
    ev.GOAL_REACHED,
    ev.BUILT,
    ev.FIGHT,
    ev.FOUNDED_GROUP,
    ev.LEFT_GROUP,
    ev.LEADER_CHALLENGED,
    ev.LEADER_CHANGED,
    ev.WORLD_CHANGED,
    ev.BIRTH,
    ev.DIED,
    ev.CONCEIVED,
    ev.GRIEVED,
    ev.LIE_DISCOVERED,
    ev.REFLECTED,
    ev.LIFE_STAGE,
    ev.PREGNANCY_LOST,
    ev.RELATION_CHANGED,
)


def dashboard_dist() -> Path:
    override = os.environ.get("GENESIS_DASHBOARD_DIST")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "dashboard" / "dist"


class World:
    """Read-only access to one world file, plus cached statistics."""

    def __init__(self, path: Path):
        self.path = Path(path)
        store = self.open()
        meta = store.meta()
        self.config = WorldConfig.from_dict(json.loads(meta["config_json"]))
        store.close()
        self.calendar = calendar_of(self.config)
        self.language = LanguageStats(self.calendar, load_dictionary())
        self.lock = threading.Lock()

    def open(self) -> WorldStore:
        if not self.path.exists():
            raise HTTPException(404, f"No world file at {self.path}")
        return WorldStore(self.path, readonly=True)

    def clock(self, tick: int | None) -> str | None:
        return None if tick is None else clock(tick, self.calendar)

    def day(self, tick: int) -> int:
        return self.calendar.day(tick)


def names(conn) -> dict[int, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT id, name FROM agents")}


def event_item_of(world: World):
    """One event as the dashboard shows it (clock in this world's calendar)."""

    def event_item(e, names_):
        act = e.data.get("act") if e.type == ev.MESSAGE else None
        return {
            "id": e.id,
            "tick": e.tick,
            "clock": world.clock(e.tick),
            "type": e.type,
            "act": act,
            "text": describe(e, names_),
            "actors": list(e.actors),
            "targets": list(e.targets),
            "lie": e.type == ev.MESSAGE and e.data.get("truthful") is False,
            "thought": e.data.get("thought"),
            "hasCause": e.cause_id is not None,
            "god": e.origin == ev.GOD,  # M9: the creator's hand (highlighted)
        }

    return event_item


def lineage_objects(conn) -> dict[int, SimpleNamespace]:
    out = {}
    for r in conn.execute("SELECT id, parent_a, parent_b, state_json FROM agents"):
        state = json.loads(r["state_json"])
        out[r["id"]] = SimpleNamespace(
            id=r["id"],
            parents=tuple(p for p in (r["parent_a"], r["parent_b"]) if p),
            partner=state.get("partner"),
        )
    return out
