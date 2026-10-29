"""One world = one SQLite file. See docs/06-data-and-persistence.md.

The simulation runs on Python objects. After each tick, `save()` writes everything that
changed in ONE transaction, so a world on disk is always at a complete tick, never half-saved.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from genesis.agents.agent import Agent
from genesis.agents.genome import Genome
from genesis.config import WorldConfig
from genesis.history import events as ev
from genesis.history.events import Event, EventLog
from genesis.mind.stream import Memory
from genesis.simulation.engine import Simulation
from genesis.society.groups import Group
from genesis.society.relationships import Relationship

SCHEMA_VERSION = 10
UPGRADABLE = {4, 5, 6, 7, 8, 9}  # older formats that `load()` upgrades in place

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS world_tiles (
    x INTEGER NOT NULL, y INTEGER NOT NULL, terrain TEXT NOT NULL,
    PRIMARY KEY (x, y)
);

CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
    quantity INTEGER NOT NULL, max_quantity INTEGER NOT NULL, regen_per_tick INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
    generation INTEGER NOT NULL, parent_a INTEGER, parent_b INTEGER,
    born_tick INTEGER NOT NULL, died_tick INTEGER, cause_of_death TEXT,
    x INTEGER NOT NULL, y INTEGER NOT NULL,
    hunger REAL NOT NULL, thirst REAL NOT NULL, energy REAL NOT NULL,
    health REAL NOT NULL, alive INTEGER NOT NULL, stage TEXT NOT NULL, current_action TEXT,
    state_json TEXT NOT NULL          -- memory, open causes
);

-- Inherited at birth, never change.
CREATE TABLE IF NOT EXISTS genes (
    agent_id INTEGER NOT NULL, gene TEXT NOT NULL, value REAL NOT NULL,
    PRIMARY KEY (agent_id, gene)
);

-- Learned skills, updated as they grow.
CREATE TABLE IF NOT EXISTS abilities (
    agent_id INTEGER NOT NULL, skill TEXT NOT NULL, value REAL NOT NULL,
    PRIMARY KEY (agent_id, skill)
);

-- Daily society numbers (population, deaths, gene means, ...).
CREATE TABLE IF NOT EXISTS metrics (
    tick INTEGER NOT NULL, name TEXT NOT NULL, value REAL NOT NULL,
    PRIMARY KEY (tick, name)
);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name, tick);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, tick INTEGER NOT NULL, type TEXT NOT NULL,
    actors_json TEXT NOT NULL, targets_json TEXT NOT NULL,
    x INTEGER, y INTEGER, data_json TEXT NOT NULL,
    cause_event_id INTEGER REFERENCES events(id),
    origin TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_tick ON events(tick);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_cause ON events(cause_event_id);

-- Which agents each event involves (fast "events of agent X" queries).
CREATE TABLE IF NOT EXISTS event_agents (
    event_id INTEGER NOT NULL, agent_id INTEGER NOT NULL, role TEXT NOT NULL,
    PRIMARY KEY (event_id, agent_id, role)
);
CREATE INDEX IF NOT EXISTS idx_event_agents_agent ON event_agents(agent_id, event_id);

-- My view of you (directional). Current values; changes are in relationships_history.
CREATE TABLE IF NOT EXISTS relationships (
    source_id INTEGER NOT NULL, target_id INTEGER NOT NULL, target_name TEXT NOT NULL,
    familiarity REAL NOT NULL, affinity REAL NOT NULL, trust REAL NOT NULL,
    respect REAL NOT NULL, fear REAL NOT NULL, gratitude REAL NOT NULL, grievance REAL NOT NULL,
    met INTEGER NOT NULL, label TEXT NOT NULL, last_event_id INTEGER, grudge_event_id INTEGER,
    last_chat_day INTEGER NOT NULL, last_praise_day INTEGER NOT NULL,
    last_gossip_day INTEGER NOT NULL, last_insult_day INTEGER NOT NULL,
    PRIMARY KEY (source_id, target_id)
);

-- Every change to a feeling, and the event that caused it ("why are they rivals?").
CREATE TABLE IF NOT EXISTS relationships_history (
    tick INTEGER NOT NULL, source_id INTEGER NOT NULL, target_id INTEGER NOT NULL,
    dimension TEXT NOT NULL, old REAL NOT NULL, new REAL NOT NULL, event_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_rel_history ON relationships_history(source_id, target_id, tick);

-- Every speech act (each is also an event of type 'message' with the same id).
CREATE TABLE IF NOT EXISTS messages (
    event_id INTEGER PRIMARY KEY, tick INTEGER NOT NULL,
    from_id INTEGER NOT NULL, to_id INTEGER NOT NULL, act TEXT NOT NULL,
    payload_json TEXT NOT NULL, truthful INTEGER NOT NULL, reply_to INTEGER,
    line TEXT, source TEXT NOT NULL DEFAULT 'rules'
);
CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(from_id, to_id, tick);
CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_id, tick);

-- Every call to the local LLM (M4): prompt, answer, outcome. Replay reuses these answers.
-- (tick, point) identifies the decision point; event_id is what the agent then said.
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY, tick INTEGER NOT NULL, point INTEGER NOT NULL,
    agent_id INTEGER NOT NULL, other_id INTEGER, kind TEXT NOT NULL, model TEXT NOT NULL,
    prompt TEXT NOT NULL, response TEXT, outcome TEXT NOT NULL, error TEXT,
    latency_ms INTEGER, choice TEXT, say TEXT, thought TEXT, event_id INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (tick, point)
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_agent ON llm_calls(agent_id, tick);

-- Each agent's memory stream (M5). Forgotten memories stay for history (forgotten_tick set)
-- but are never loaded or recalled; nor are the memories of the dead.
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL, tick INTEGER NOT NULL,
    kind TEXT NOT NULL, text TEXT NOT NULL, importance INTEGER NOT NULL,
    about_json TEXT NOT NULL, source_event_id INTEGER, source_agent_id INTEGER,
    forgotten_tick INTEGER
);
CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent_id, tick);

-- Embeddings of live memories (a cache for memory search; never part of history).
CREATE TABLE IF NOT EXISTS memory_vectors (
    memory_id INTEGER PRIMARY KEY, model TEXT NOT NULL, vector BLOB NOT NULL
);

-- Groups (M7). Membership changes are events (founded/joined/left_group); this is the
-- current state of each group, including ended ones.
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, founder_id INTEGER NOT NULL,
    leader_id INTEGER NOT NULL, members_json TEXT NOT NULL, home_x INTEGER NOT NULL,
    home_y INTEGER NOT NULL, store_food INTEGER NOT NULL, founded_tick INTEGER NOT NULL,
    ended_tick INTEGER
);

-- Culture (M8b): every concept ever invented (name in the valley's words, what it does by
-- the hidden physics, its recipe) and structures built in the world.
CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, function TEXT NOT NULL,
    functions_json TEXT NOT NULL, recipe_json TEXT NOT NULL, inventor_id INTEGER NOT NULL,
    tick INTEGER NOT NULL, description TEXT NOT NULL, same_as INTEGER, lost_tick INTEGER
);
CREATE TABLE IF NOT EXISTS structures (
    id INTEGER PRIMARY KEY, concept_id INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
    owner_id INTEGER NOT NULL, group_id INTEGER, built_tick INTEGER NOT NULL
);

-- Once a year (M8e): each living mind (agent_id > 0) and the valleys (agent_id 0).
CREATE TABLE IF NOT EXISTS yearly (
    year INTEGER NOT NULL, agent_id INTEGER NOT NULL, data_json TEXT NOT NULL,
    PRIMARY KEY (year, agent_id)
);

-- M8.5: every LLM answer (or skip) the moment it is known, until the next save takes it in.
-- After a crash, the run replays them, so its history is the same as if it never stopped.
CREATE TABLE IF NOT EXISTS llm_journal (
    tick INTEGER NOT NULL, point INTEGER NOT NULL, data_json TEXT NOT NULL,
    PRIMARY KEY (tick, point)
);

-- Real-world run sessions. Kept out of `events` so stopping and resuming
-- never changes the world's own history (event ids stay identical).
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
    from_tick INTEGER NOT NULL, to_tick INTEGER, end_reason TEXT
);
"""


LLM_COLUMNS = (
    "tick",
    "point",
    "agent_id",
    "other_id",
    "kind",
    "model",
    "prompt",
    "response",
    "outcome",
    "error",
    "latency_ms",
    "choice",
    "say",
    "thought",
    "event_id",
    "created_at",
)


class WorldStoreError(Exception):
    pass


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # readers (dashboard) never block the writer
    conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; a crash loses at most the last tick
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rng_to_json(state) -> str:
    version, internal, gauss = state
    return json.dumps([version, list(internal), gauss])


def _rng_from_json(text: str):
    version, internal, gauss = json.loads(text)
    return (version, tuple(internal), gauss)


class WorldStore:
    def __init__(self, path: str | Path, readonly: bool = False):
        self.path = Path(path)
        if readonly:  # for viewers: never writes, never blocks the running world
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row
        else:
            self.conn = _connect(self.path)

    # --------------------------------
    # Create / save / load
    # --------------------------------

    @classmethod
    def create(cls, path: str | Path, sim: Simulation, overwrite: bool = False) -> "WorldStore":
        path = Path(path)
        if path.exists():
            if not overwrite:
                raise WorldStoreError(f"{path} already exists")
            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)

        path.parent.mkdir(parents=True, exist_ok=True)
        store = cls(path)

        with store.conn:
            store.conn.executescript(SCHEMA)
            store._set_meta(
                schema_version=SCHEMA_VERSION,
                config_json=json.dumps(sim.config.to_dict()),
                config_toml=sim.config.source_toml,
                created_at=_now(),
            )
            store.conn.executemany(
                "INSERT INTO world_tiles (x, y, terrain) VALUES (?, ?, ?)",
                [(p.x, p.y, t.value) for p, t in sim.world.terrain.items()],
            )

        store.save(sim)
        return store

    def save(self, sim: Simulation):
        """Write the current tick in one transaction."""
        events = sim.log.take_pending()
        metrics, sim.pending_metrics = sim.pending_metrics, []
        yearly, sim.pending_yearly = sim.pending_yearly, []
        rel_history, sim.pending_relationship_history = sim.pending_relationship_history, []
        llm_calls, sim.pending_llm_calls = sim.pending_llm_calls, []
        memories, sim.pending_memories = sim.pending_memories, []
        forgotten, sim.pending_forgotten = sim.pending_forgotten, []
        new_vectors, sim.pending_vectors = sim.pending_vectors, []
        dropped, sim.dropped_vectors = sim.dropped_vectors, []

        try:
            with self.conn:  # BEGIN ... COMMIT (or ROLLBACK on error)
                self._set_meta(
                    tick=sim.tick_count,
                    rng_state=_rng_to_json(sim.rng.getstate()),
                    next_event_id=sim.log.next_id,
                    event_counts=json.dumps(sim.log.counts, sort_keys=True),
                    counts_at_day_start=json.dumps(sim.counts_at_day_start, sort_keys=True),
                    next_memory_id=sim.next_memory_id,
                    pregnancies=json.dumps(sim.pregnancies),
                    condition_events=json.dumps(sim.world.condition_events, sort_keys=True),
                    economy=json.dumps(sim.world.economy, sort_keys=True),
                    disaster_events=json.dumps(sim.world.disaster_events, sort_keys=True),
                    peak_population=sim.world.peak_population,
                    god=json.dumps(getattr(sim.world, "god", {}), sort_keys=True),
                    saved_at=_now(),
                )
                self.conn.executemany(
                    """INSERT INTO resources
                           (id, kind, x, y, quantity, max_quantity, regen_per_tick)
                       VALUES (?, 'food', ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET quantity = excluded.quantity""",
                    [
                        (
                            f.id,
                            f.position.x,
                            f.position.y,
                            f.quantity,
                            f.max_quantity,
                            f.regen_per_tick,
                        )
                        for f in sim.world.food_sources
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO agents (id, name, generation, parent_a, parent_b, born_tick,
                           died_tick, cause_of_death, x, y, hunger, thirst, energy, health,
                           alive, stage, current_action, state_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                           died_tick = excluded.died_tick, cause_of_death = excluded.cause_of_death,
                           x = excluded.x, y = excluded.y, hunger = excluded.hunger,
                           thirst = excluded.thirst, energy = excluded.energy,
                           health = excluded.health, alive = excluded.alive,
                           stage = excluded.stage, current_action = excluded.current_action,
                           state_json = excluded.state_json""",
                    [
                        (
                            a.id,
                            a.name,
                            a.generation,
                            a.parents[0] if len(a.parents) > 0 else None,
                            a.parents[1] if len(a.parents) > 1 else None,
                            a.born_tick,
                            a.died_tick,
                            a.cause_of_death,
                            a.position.x,
                            a.position.y,
                            a.hunger,
                            a.thirst,
                            a.energy,
                            a.health,
                            int(a.alive),
                            a.stage,
                            a.current_action.value if a.current_action else None,
                            json.dumps(a.to_state()),
                        )
                        for a in sim.population.agents
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO events (id, tick, type, actors_json, targets_json, x, y,
                           data_json, cause_event_id, origin)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            e.id,
                            e.tick,
                            e.type,
                            json.dumps(e.actors),
                            json.dumps(e.targets),
                            e.x,
                            e.y,
                            json.dumps(e.data),
                            e.cause_id,
                            e.origin,
                        )
                        for e in events
                    ],
                )
                self.conn.executemany(
                    "INSERT INTO event_agents (event_id, agent_id, role) VALUES (?, ?, ?)",
                    [
                        (e.id, agent_id, role)
                        for e in events
                        for role, ids in (("actor", e.actors), ("target", e.targets))
                        for agent_id in ids
                    ],
                )
                # Genes never change: written once per agent.
                self.conn.executemany(
                    "INSERT OR IGNORE INTO genes (agent_id, gene, value) VALUES (?, ?, ?)",
                    [
                        (a.id, gene, value)
                        for a in sim.population.agents
                        for gene, value in a.genome.to_dict().items()
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO abilities (agent_id, skill, value) VALUES (?, ?, ?)
                       ON CONFLICT(agent_id, skill) DO UPDATE SET value = excluded.value""",
                    [
                        (a.id, skill, value)
                        for a in sim.population.agents
                        if a.alive
                        or (a.died_tick is not None and a.died_tick >= sim.last_save_tick)
                        for skill, value in a.abilities.to_dict().items()
                    ],
                )
                self.conn.executemany(
                    "INSERT INTO metrics (tick, name, value) VALUES (?, ?, ?)",
                    metrics,
                )
                self.conn.executemany(
                    """INSERT INTO relationships (source_id, target_id, target_name, familiarity,
                           affinity, trust, respect, fear, gratitude, grievance, met, label,
                           last_event_id, grudge_event_id, last_chat_day, last_praise_day,
                           last_gossip_day, last_insult_day)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source_id, target_id) DO UPDATE SET
                           familiarity = excluded.familiarity, affinity = excluded.affinity,
                           trust = excluded.trust, respect = excluded.respect,
                           fear = excluded.fear, gratitude = excluded.gratitude,
                           grievance = excluded.grievance, met = excluded.met,
                           label = excluded.label, last_event_id = excluded.last_event_id,
                           grudge_event_id = excluded.grudge_event_id,
                           last_chat_day = excluded.last_chat_day,
                           last_praise_day = excluded.last_praise_day,
                           last_gossip_day = excluded.last_gossip_day,
                           last_insult_day = excluded.last_insult_day""",
                    [
                        (
                            a.id,
                            rel.target_id,
                            rel.target_name,
                            rel.familiarity,
                            rel.affinity,
                            rel.trust,
                            rel.respect,
                            rel.fear,
                            rel.gratitude,
                            rel.grievance,
                            int(rel.met),
                            rel.label(),
                            rel.last_event_id,
                            rel.grudge_event_id,
                            rel.last_chat_day,
                            rel.last_praise_day,
                            rel.last_gossip_day,
                            rel.last_insult_day,
                        )
                        for a in sim.population.agents
                        if a.alive
                        or (a.died_tick is not None and a.died_tick >= sim.last_save_tick)
                        for rel in a.relationships.values()
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO relationships_history
                           (tick, source_id, target_id, dimension, old, new, event_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    rel_history,
                )
                self.conn.executemany(
                    """INSERT INTO messages (event_id, tick, from_id, to_id, act, payload_json,
                           truthful, reply_to, line, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            e.id,
                            e.tick,
                            e.actors[0],
                            e.targets[0],
                            e.data["act"],
                            json.dumps(e.data["payload"]),
                            int(e.data["truthful"]),
                            e.data["reply_to"],
                            e.data.get("line"),
                            "llm" if e.data.get("line") else "rules",
                        )
                        for e in events
                        if e.type == ev.MESSAGE
                    ],
                )
                self.conn.executemany(
                    f"INSERT INTO llm_calls ({', '.join(LLM_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(LLM_COLUMNS))})",
                    [tuple(c[k] for k in LLM_COLUMNS) for c in llm_calls],
                )
                self.conn.executemany(
                    """INSERT INTO memories (id, agent_id, tick, kind, text, importance,
                           about_json, source_event_id, source_agent_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (*row[:6], json.dumps(row[6]), *row[7:])
                        for row in (m.to_row(agent_id) for agent_id, m in memories)
                    ],
                )
                self.conn.executemany(
                    "UPDATE memories SET forgotten_tick = ? WHERE id = ?",
                    [(tick, memory_id) for memory_id, tick in forgotten],
                )
                model = sim.config.llm.embed_model
                self.conn.executemany(
                    "INSERT OR REPLACE INTO memory_vectors (memory_id, model, vector) "
                    "VALUES (?, ?, ?)",
                    [
                        (i, model, sim.memory_vectors[i].astype("float32").tobytes())
                        for i in new_vectors
                        if i in sim.memory_vectors
                    ],
                )
                self.conn.executemany(
                    "DELETE FROM memory_vectors WHERE memory_id = ?", [(i,) for i in dropped]
                )
                self.conn.executemany(
                    """INSERT INTO groups (id, name, founder_id, leader_id, members_json,
                           home_x, home_y, store_food, founded_tick, ended_tick)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET leader_id = excluded.leader_id,
                           members_json = excluded.members_json,
                           store_food = excluded.store_food, ended_tick = excluded.ended_tick""",
                    [
                        (
                            g.id,
                            g.name,
                            g.founder,
                            g.leader,
                            json.dumps(g.members),
                            g.home[0],
                            g.home[1],
                            g.store_food,
                            g.founded_tick,
                            g.ended_tick,
                        )
                        for g in sim.groups.values()
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO concepts (id, name, function, functions_json, recipe_json,
                           inventor_id, tick, description, same_as, lost_tick)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET lost_tick = excluded.lost_tick""",
                    [
                        (
                            c.id,
                            c.name,
                            c.function,
                            json.dumps(c.functions),
                            json.dumps(c.recipe),
                            c.inventor,
                            c.tick,
                            c.description,
                            c.same_as,
                            c.lost_tick,
                        )
                        for c in sim.concepts.values()
                    ],
                )
                self.conn.executemany(
                    """INSERT OR IGNORE INTO structures (id, concept_id, x, y, owner_id,
                           group_id, built_tick) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            s["id"],
                            s["concept"],
                            s["x"],
                            s["y"],
                            s["owner"],
                            s.get("group"),
                            s["built"],
                        )
                        for s in sim.structures
                    ],
                )
                self.conn.executemany(
                    "INSERT OR REPLACE INTO yearly (year, agent_id, data_json) VALUES (?, ?, ?)",
                    [(y, a, json.dumps(d, sort_keys=True)) for y, a, d in yearly],
                )
                self.conn.execute("DELETE FROM llm_journal WHERE tick < ?", (sim.tick_count,))
            sim.last_save_tick = sim.tick_count
        except Exception:
            sim.log.pending[:0] = events  # not saved: keep them for the next attempt
            sim.pending_metrics[:0] = metrics
            sim.pending_yearly[:0] = yearly
            sim.pending_relationship_history[:0] = rel_history
            sim.pending_llm_calls[:0] = llm_calls
            sim.pending_memories[:0] = memories
            sim.pending_forgotten[:0] = forgotten
            sim.pending_vectors[:0] = new_vectors
            sim.dropped_vectors[:0] = dropped
            raise

    def load(self) -> Simulation:
        meta = self.meta()
        if int(meta.get("schema_version", 0)) in UPGRADABLE:
            self.upgrade()
            meta = self.meta()
        if int(meta.get("schema_version", 0)) != SCHEMA_VERSION:
            raise WorldStoreError(
                f"This world file uses format {meta.get('schema_version')}, but this version of "
                f"Genesis needs format {SCHEMA_VERSION}. Create a new world with `genesis new`."
            )

        config = WorldConfig.from_dict(json.loads(meta["config_json"]))
        sim = Simulation(config, _empty=True)
        sim.tick_count = int(meta["tick"])
        sim.rng.setstate(_rng_from_json(meta["rng_state"]))
        sim.log = EventLog(
            next_id=int(meta["next_event_id"]), counts=json.loads(meta["event_counts"])
        )
        sim.counts_at_day_start = json.loads(meta["counts_at_day_start"])
        sim.log.recent = [
            self._row_to_event(r)
            for r in reversed(
                self.conn.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (sim.log.max_recent,)
                ).fetchall()
            )
        ]

        from genesis.god import interventions as god  # M9: what God changed stays changed

        god.restore(sim, json.loads(meta.get("god", "{}")))
        quantities = {
            r["id"]: r["quantity"] for r in self.conn.execute("SELECT id, quantity FROM resources")
        }
        for food in sim.world.food_sources:
            food.quantity = quantities[food.id]

        genes: dict[int, dict] = {}
        for r in self.conn.execute("SELECT agent_id, gene, value FROM genes"):
            genes.setdefault(r["agent_id"], {})[r["gene"]] = r["value"]
        skills: dict[int, dict] = {}
        for r in self.conn.execute("SELECT agent_id, skill, value FROM abilities"):
            skills.setdefault(r["agent_id"], {})[r["skill"]] = r["value"]

        relations: dict[int, list[Relationship]] = {}
        for r in self.conn.execute("SELECT * FROM relationships ORDER BY source_id, rowid"):
            data = {k: r[k] for k in r.keys() if k not in ("source_id", "label")}
            data["met"] = bool(data["met"])
            relations.setdefault(r["source_id"], []).append(Relationship.from_dict(data))

        for row in self.conn.execute("SELECT * FROM agents ORDER BY id"):
            agent = Agent.from_state(
                dict(row),
                json.loads(row["state_json"]),
                Genome.from_dict(genes[row["id"]]),
                skills.get(row["id"], {}),
                relations.get(row["id"], []),
            )
            sim.population.add_existing(agent)

        sim.next_memory_id = int(meta.get("next_memory_id", 1))
        sim.last_save_tick = sim.tick_count
        sim.pregnancies = json.loads(meta.get("pregnancies", "[]"))
        if self._has_table("groups"):
            for r in self.conn.execute("SELECT * FROM groups ORDER BY id"):
                sim.groups[r["id"]] = Group(
                    id=r["id"],
                    name=r["name"],
                    founder=r["founder_id"],
                    leader=r["leader_id"],
                    members=json.loads(r["members_json"]),
                    home=(r["home_x"], r["home_y"]),
                    founded_tick=r["founded_tick"],
                    store_food=r["store_food"],
                    ended_tick=r["ended_tick"],
                )
        if self._has_table("concepts"):
            from genesis.culture.inventions import Concept

            for r in self.conn.execute("SELECT * FROM concepts ORDER BY id"):
                sim.concepts[r["id"]] = Concept(
                    id=r["id"],
                    name=r["name"],
                    function=r["function"],
                    functions=json.loads(r["functions_json"]),
                    recipe=json.loads(r["recipe_json"]),
                    inventor=r["inventor_id"],
                    tick=r["tick"],
                    description=r["description"],
                    same_as=r["same_as"],
                    lost_tick=r["lost_tick"],
                )
            for r in self.conn.execute("SELECT * FROM structures ORDER BY id"):
                sim.structures.append(
                    {
                        "id": r["id"],
                        "concept": r["concept_id"],
                        "x": r["x"],
                        "y": r["y"],
                        "owner": r["owner_id"],
                        "built": r["built_tick"],
                        "group": r["group_id"],
                    }
                )
        sim.sync_world_conditions()
        sim.world.condition_events = json.loads(meta.get("condition_events", "{}"))
        sim.world.disaster_events = {
            int(k): v for k, v in json.loads(meta.get("disaster_events", "{}")).items()
        }
        sim.world.peak_population = int(meta.get("peak_population", 0))
        sim.world.economy = json.loads(meta.get("economy", '{"currencies": [], "indirect": []}'))
        self._load_memories(sim)
        return sim

    def _load_memories(self, sim):
        """Live memories of living agents (the dead are never recalled), with any vectors."""
        alive = {a.id: a for a in sim.population.agents if a.alive}
        rows = self.conn.execute(
            "SELECT m.*, v.vector, v.model FROM memories m "
            "LEFT JOIN memory_vectors v ON v.memory_id = m.id "
            "WHERE m.forgotten_tick IS NULL ORDER BY m.id"
        )
        model = sim.config.llm.embed_model
        for r in rows:
            agent = alive.get(r["agent_id"])
            if agent is None:
                continue
            agent.stream.add(
                Memory(
                    id=r["id"],
                    tick=r["tick"],
                    kind=r["kind"],
                    text=r["text"],
                    importance=r["importance"],
                    about=tuple(json.loads(r["about_json"])),
                    source_event_id=r["source_event_id"],
                    source_agent_id=r["source_agent_id"],
                )
            )
            if r["vector"] is not None and r["model"] == model:
                sim.memory_vectors[r["id"]] = np.frombuffer(r["vector"], dtype=np.float32)

    def upgrade(self):
        """Bring an older world file up to date. 4 → 5 adds llm_calls; 5 → 6 adds the memory
        tables (agents of an upgraded world start with empty memories); 6 → 7 adds groups."""
        with self.conn:
            self.conn.executescript(SCHEMA)
            columns = {r[1] for r in self.conn.execute("PRAGMA table_info(relationships)")}
            if "grudge_event_id" not in columns:  # 6 -> 7
                self.conn.execute("ALTER TABLE relationships ADD COLUMN grudge_event_id INTEGER")
            self._set_meta(schema_version=SCHEMA_VERSION)

    def start_run(self, tick: int) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs (started_at, from_tick) VALUES (?, ?)", (_now(), tick)
            )
        return cur.lastrowid

    def end_run(self, run_id: int, tick: int, reason: str):
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET ended_at = ?, to_tick = ?, end_reason = ? WHERE id = ?",
                (_now(), tick, reason, run_id),
            )

    def close(self):
        self.conn.close()

    # --------------------------------
    # Queries (for the CLI and, later, the dashboard)
    # --------------------------------

    def meta(self) -> dict:
        return {r["key"]: r["value"] for r in self.conn.execute("SELECT key, value FROM meta")}

    def event(self, event_id: int) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def events(
        self, agent_id: int | None = None, type: str | None = None, limit: int = 50
    ) -> list[Event]:
        """Most recent events first."""
        sql = "SELECT e.* FROM events e"
        where, params = [], []

        if agent_id is not None:
            sql += " JOIN event_agents ea ON ea.event_id = e.id"
            where.append("ea.agent_id = ?")
            params.append(agent_id)
        if type is not None:
            where.append("e.type = ?")
            params.append(type)
        if where:
            sql += " WHERE " + " AND ".join(where)

        sql += " GROUP BY e.id ORDER BY e.id DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_event(r) for r in self.conn.execute(sql, params)]

    def cause_chain(self, event_id: int) -> list[Event]:
        """The event, then its cause, then that cause's cause, ... back to the start."""
        chain, seen = [], set()
        current = self.event(event_id)

        while current is not None and current.id not in seen:
            chain.append(current)
            seen.add(current.id)
            current = self.event(current.cause_id) if current.cause_id else None

        return chain

    def consequences(self, event_id: int) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE cause_event_id = ? ORDER BY id", (event_id,)
        )
        return [self._row_to_event(r) for r in rows]

    def metrics(
        self, names: list[str] | None = None, max_points: int | None = None
    ) -> dict[int, dict[str, float]]:
        """{tick: {name: value}} for the requested metric names (all if None). With
        `max_points`, long series are averaged into at most that many buckets (M8.5: a
        thousand years of daily rows stay fast to chart)."""
        where, params = "", []
        if names:
            where = f" WHERE name IN ({','.join('?' * len(names))})"
            params = list(names)
        size = 1
        if max_points:
            lo, hi = self.conn.execute(
                f"SELECT MIN(tick), MAX(tick) FROM metrics{where}", params
            ).fetchone()
            days = self.conn.execute(
                f"SELECT COUNT(DISTINCT tick) FROM metrics{where}", params
            ).fetchone()[0]
            if lo is not None and days > max_points:
                size = max(1, -(-(hi - lo + 1) // max_points))
        out: dict[int, dict[str, float]] = {}
        if size == 1:
            sql = f"SELECT tick, name, value FROM metrics{where} ORDER BY tick"
        else:
            sql = (
                f"SELECT MIN(tick) AS tick, name, AVG(value) AS value FROM metrics{where} "
                f"GROUP BY tick / {size}, name ORDER BY tick"
            )
        for r in self.conn.execute(sql, params):
            out.setdefault(r["tick"], {})[r["name"]] = r["value"]
        return out

    def messages(self, a: int, b: int | None = None, limit: int = 50) -> list[dict]:
        """Messages to or from agent a (and b, if given), most recent first."""
        if b is None:
            sql = "SELECT * FROM messages WHERE from_id = ? OR to_id = ?"
            params = [a, a]
        else:
            sql = (
                "SELECT * FROM messages WHERE (from_id = ? AND to_id = ?) "
                "OR (from_id = ? AND to_id = ?)"
            )
            params = [a, b, b, a]
        rows = self.conn.execute(sql + " ORDER BY event_id DESC LIMIT ?", [*params, limit])
        return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in rows]

    def relationship_history(self, source: int, target: int, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM relationships_history WHERE source_id = ? AND target_id = ? "
            "ORDER BY rowid DESC LIMIT ?",
            (source, target, limit),
        )
        return [dict(r) for r in rows]

    def llm_calls(
        self, agent_id: int | None = None, kind: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Recorded model calls, most recent first."""
        if not self._has_table("llm_calls"):
            return []
        where, params = [], []
        if agent_id is not None:
            where.append("(agent_id = ? OR other_id = ?)")
            params += [agent_id, agent_id]
        if kind is not None:
            where.append("kind = ?")
            params.append(kind)
        sql = "SELECT * FROM llm_calls"
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self.conn.execute(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
        return [dict(r) for r in rows]

    def llm_records(self) -> dict[tuple[int, int], dict]:
        """{(tick, point): call} for replay."""
        if not self._has_table("llm_calls"):
            return {}
        rows = self.conn.execute(
            "SELECT tick, point, agent_id, kind, outcome, response FROM llm_calls"
        )
        return {(r["tick"], r["point"]): dict(r) for r in rows}

    def llm_stats(self) -> dict:
        if not self._has_table("llm_calls"):
            return {"total": 0, "outcomes": {}, "kinds": {}, "avg_ms": None, "models": {}}
        outcomes = dict(
            self.conn.execute("SELECT outcome, COUNT(*) FROM llm_calls GROUP BY outcome")
        )
        kinds = dict(self.conn.execute("SELECT kind, COUNT(*) FROM llm_calls GROUP BY kind"))
        models = dict(self.conn.execute("SELECT model, COUNT(*) FROM llm_calls GROUP BY model"))
        avg = self.conn.execute(
            "SELECT AVG(latency_ms) FROM llm_calls WHERE outcome IN ('ok', 'rejected')"
        ).fetchone()[0]
        return {
            "total": sum(outcomes.values()),
            "outcomes": outcomes,
            "kinds": kinds,
            "models": models,
            "avg_ms": avg,
        }

    def memories(
        self,
        agent_id: int,
        kind: str | None = None,
        include_forgotten: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        """An agent's memories, most recent first."""
        if not self._has_table("memories"):
            return []
        sql = "SELECT * FROM memories WHERE agent_id = ?"
        params: list = [agent_id]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if not include_forgotten:
            sql += " AND forgotten_tick IS NULL"
        rows = self.conn.execute(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
        return [{**dict(r), "about": json.loads(r["about_json"])} for r in rows]

    def _has_table(self, name: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
            ).fetchone()
            is not None
        )

    def agent_names(self) -> dict[int, str]:
        return {r["id"]: r["name"] for r in self.conn.execute("SELECT id, name FROM agents")}

    # --------------------------------

    def _set_meta(self, **values):
        self.conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, str(v)) for k, v in values.items()],
        )

    @staticmethod
    def _row_to_event(row) -> Event:
        return Event(
            id=row["id"],
            tick=row["tick"],
            type=row["type"],
            actors=tuple(json.loads(row["actors_json"])),
            targets=tuple(json.loads(row["targets_json"])),
            x=row["x"],
            y=row["y"],
            data=json.loads(row["data_json"]),
            cause_id=row["cause_event_id"],
            origin=row["origin"],
        )


class Journal:
    """M8.5: writes each LLM outcome at once, on its own connection (committed per answer)."""

    def __init__(self, path):
        self.conn = sqlite3.connect(str(path), timeout=60, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")

    def write(self, tick: int, point: int, entry: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_journal (tick, point, data_json) VALUES (?, ?, ?)",
            (tick, point, json.dumps(entry)),
        )

    def close(self):
        self.conn.close()


def journal_entries(store: "WorldStore", from_tick: int) -> dict:
    """{(tick, point): entry} written after the last save (what a crash would have lost)."""
    if not store._has_table("llm_journal"):
        return {}
    return {
        (r[0], r[1]): json.loads(r[2])
        for r in store.conn.execute(
            "SELECT tick, point, data_json FROM llm_journal WHERE tick >= ?", (from_tick,)
        )
    }


def attach_journal(sim, path, store: "WorldStore") -> int:
    """Give a live LLM mind its journal, and the answers to replay after a crash."""
    mind = sim.mind
    if mind is None or not hasattr(mind, "recorded"):
        return 0
    mind.recorded = journal_entries(store, sim.tick_count)
    mind.journal = Journal(path)
    return len(mind.recorded)
