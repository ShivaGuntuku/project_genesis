"""Decision points and the minds that answer them.

The simulation asks for a decision at a *point*: one agent, facing one other agent, with a
list of options that are all valid right now (built by the simulation, not the model).

- No mind / rules: every point returns None and the rules decide (M3 behaviour).
- LLMMind: asks the local model within the budget. A failure (offline, timeout, bad JSON,
  unknown option) returns None, so the rules decide; the failure is recorded.
- ReplayMind: answers from the recorded calls of a world, so a replay is identical.

Points are numbered within each tick (`ctx.next_point()`). Since history is deterministic
given the answers, (tick, point) identifies the same decision in a live run and its replay.

Every call goes through `ask(ctx, index, Question)`: the conversation decisions above and,
from M5, the nightly reflection (its own prompt, schema and parser).
"""

import time
from dataclasses import dataclass, field

from genesis.culture.words import leak_words
from genesis.llm import gateway as gw
from genesis.llm import prompts, schemas
from genesis.llm.settings import LLMConfig

# Hard choices go to the bigger model and get budget first.
IMPORTANT = {
    "request",
    "info",
    "threat",
    "reflect",
    "mate",
    "name",
    "join",
    "obey",
    "fight",
    "group_name",
    "trade",
    "plan",
}
CHAT = {"greet", "turn", "reply", "ask"}

# Outcomes recorded in llm_calls.
OK, INVALID, REJECTED = "ok", "invalid", "rejected"


class ReplayDiverged(RuntimeError):
    pass


@dataclass(frozen=True)
class Option:
    key: str  # what the model answers, e.g. "give:water"
    label: str  # what the model reads, e.g. "Give Kael a drink of water (you carry 2)"


@dataclass
class Point:
    kind: str  # greet | turn | reply | ask | request | info | threat
    agent: object  # the one deciding
    other: object  # the one they face
    options: list[Option] = field(default_factory=list)
    heard: str | None = None  # what the other just said or did
    resource: str | None = None

    @property
    def keys(self) -> list[str] | None:
        """Keys the model must choose from (None when there is nothing to choose)."""
        if self.kind in ("greet", "ask") or not self.options:
            return None
        return [o.key for o in self.options]


@dataclass
class Decision:
    choice: str  # an option key (the only option, for greet/ask)
    say: str  # the agent's own words
    thought: str  # their private reason
    call: dict | None = None  # the llm_calls record (outcome may become "rejected")


@dataclass
class Question:
    """One call to the model, whatever its purpose."""

    kind: str
    agent: object
    other: object | None
    prompt: object  # callable() -> str (built only when the model is really asked)
    schema: dict
    parse: object  # callable(text, record | None) -> result | None (None = invalid)
    max_tokens: int = 150
    budgeted: bool = True  # reflection runs at night, outside the per-tick budget


def call_seed(world_seed: int, tick: int, point: int) -> int:
    return (world_seed * 1_000_003 + tick * 1_009 + point) % (2**31)


class LLMMind:
    """Asks the local model. Never raises: on any failure the rules decide."""

    active = True

    def __init__(
        self,
        config: LLMConfig,
        gateway,
        world_seed: int = 0,
        start_hour: int = 8,
        embedder=None,
    ):
        self.config = config
        self.gateway = gateway
        self.world_seed = world_seed
        self.start_hour = start_hour
        self.embedder = embedder  # memory search by meaning (else shared words)
        self.conversation_turns = config.conversation_turns
        self.reflects = config.reflection
        self._tick, self._tick_calls = -1, 0
        self._day, self._day_calls = -1, 0
        self.stats = {
            "ok": 0,
            "failed": 0,
            "skipped_budget": 0,
            "skipped_offline": 0,
            "skipped_rate": 0,
        }
        self.governor = Governor(config.max_calls_per_minute)
        self.journal = None  # M8.5: where each answer is written the moment it arrives
        self.recorded: dict = {}  # (tick, point) -> journal entry, replayed after a crash

    # --------------------------------

    def decide(self, ctx, index: int, point: Point) -> Decision | None:
        return self.ask(ctx, index, point_question(point, ctx, self))

    def recall(self, agent, ctx, query: str, about_id=None, exclude=None):
        """The memories that come to this agent's mind (its own stream only)."""
        from genesis.mind import stream

        relevance = self.embedder.relevance if self.embedder is not None else None
        return stream.retrieve(
            agent.stream,
            ctx.tick,
            query,
            about_id=about_id,
            k=self.config.memories_in_prompt,
            relevance=relevance,
            exclude=exclude,
            decay=stream.per_tick_decay(ctx.world.rules, ctx.world.calendar),
        )

    def ask(self, ctx, index: int, q: Question):
        return self.ask_many(ctx, [(index, q)])[0]

    def ask_many(self, ctx, items: list) -> list:
        """Several independent questions [(point, question)] at once (M8.5): gated in order,
        sent to up to `llm.workers` model workers together, and finished in order, so the
        result is the same as asking one by one. Each outcome is written to the journal the
        moment it is known, so a crash loses no answer (resume replays them)."""
        results: list = [None] * len(items)
        live = []
        for i, (index, q) in enumerate(items):
            entry = self.recorded.pop((ctx.tick, index), None)
            if entry is not None:  # answered (or skipped) before a crash: exactly the same
                results[i] = self._replayed(ctx, index, q, entry)
                continue
            skip = self._gate(ctx, q)
            if skip:
                self.stats[skip] += 1
                self._journal(ctx, index, q, {"skip": skip})
                continue
            live.append((i, index, q))
        if not live:
            return results
        jobs = [(index, q, q.prompt(), self._model(q)) for _, index, q in live]
        workers = max(1, getattr(self.config, "workers", 1))
        if workers > 1 and len(jobs) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
                replies = list(pool.map(lambda job: self._chat(ctx, *job), jobs))
        else:
            replies = [self._chat(ctx, *job) for job in jobs]
        for (i, _, _), (index, q, prompt, model), reply in zip(live, jobs, replies, strict=True):
            record = self._record(ctx, index, q, model, prompt, reply)
            self._journal(ctx, index, q, {"record": record})  # written before it is parsed
            results[i] = self._finish(ctx, q, record)
        return results

    # -- the steps -----------------------------------------------------------------------

    def _gate(self, ctx, q) -> str | None:
        """Why this call is not made now (the rules decide instead), or None."""
        if q.budgeted and not self._within_budget(ctx, q.kind):
            return "skipped_budget"
        if not self.gateway.available():
            return "skipped_offline"
        if not self.governor.allow(q.kind in IMPORTANT):
            return "skipped_rate"
        if q.budgeted:
            self._tick_calls += 1
            self._day_calls += 1
        return None

    def _model(self, q) -> str:
        return self.config.mind_model if q.kind in IMPORTANT else self.config.chat_model

    def _chat(self, ctx, index, q, prompt, model):
        return self.gateway.chat(
            model,
            prompts.SYSTEM,
            prompt,
            q.schema,
            temperature=self.config.temperature,
            seed=call_seed(self.world_seed, ctx.tick, index),
            max_tokens=q.max_tokens,
        )

    def _record(self, ctx, index, q, model, prompt, reply) -> dict:
        return {
            "tick": ctx.tick,
            "point": index,
            "agent_id": q.agent.id,
            "other_id": q.other.id if q.other is not None else None,
            "kind": q.kind,
            "model": model,
            "prompt": prompt,
            "response": reply.text,
            "outcome": reply.outcome,
            "error": reply.error,
            "latency_ms": reply.latency_ms,
            "choice": None,
            "say": None,
            "thought": None,
            "event_id": None,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def _finish(self, ctx, q, record):
        ctx.llm_calls.append(record)
        result = q.parse(record["response"], record) if record["outcome"] == gw.OK else None
        self.stats["failed" if result is None else "ok"] += 1
        return result

    def _replayed(self, ctx, index, q, entry):
        if entry.get("kind") != q.kind or entry.get("agent_id") != q.agent.id:
            raise ReplayDiverged(
                f"tick {ctx.tick} point {index}: the journal has {entry.get('kind')} by agent "
                f"{entry.get('agent_id')}, but the run asks {q.kind} by agent {q.agent.id}"
            )
        skip = entry.get("skip")
        if skip:
            self.stats[skip] += 1
            return None
        if q.budgeted:  # it counted against the budget the first time too
            self._within_budget(ctx, q.kind)
            self._tick_calls += 1
            self._day_calls += 1
        return self._finish(ctx, q, dict(entry["record"]))  # as written before it was parsed

    def _journal(self, ctx, index, q, entry):
        if self.journal is not None:
            self.journal.write(ctx.tick, index, {"kind": q.kind, "agent_id": q.agent.id, **entry})

    def _within_budget(self, ctx, kind: str) -> bool:
        c = self.config
        if ctx.tick != self._tick:
            self._tick, self._tick_calls = ctx.tick, 0
        if ctx.day != self._day:
            self._day, self._day_calls = ctx.day, 0

        per_tick = c.max_calls_per_tick or float("inf")  # 0: no cap (M8f)
        per_day = c.max_calls_per_day or float("inf")
        if kind in IMPORTANT:
            return self._tick_calls < per_tick and self._day_calls < per_day
        # Chatter keeps a reserve for hard choices.
        tick_cap = per_tick - (1 if per_tick > 1 else 0)
        return self._tick_calls < tick_cap and self._day_calls < 0.8 * per_day


class Governor:
    """Keeps LLM use within ±10% of the machine's maximum and never above it (M8a).

    A sliding one-minute window of real time:
    - routine thoughts (conversation) may fill it up to 90% of `max_per_minute`; above that
      they are skipped and the rules decide, so the world keeps moving;
    - hard choices and reflections may use the last 10%, and when the window is full they
      *wait* for the next free slot (the simulation slows to the LLM's pace for a moment).
    So while there is enough to think about, the LLM runs between 90% and 100% of its maximum.
    With 0, there is no limit. Only real time decides whether a call is made; every call made
    is recorded, so replays are exact."""

    def __init__(self, max_per_minute: int, clock=None, sleep=None):
        import collections

        self.max = max_per_minute
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.calls = collections.deque()
        self.waited = 0.0  # seconds spent waiting for a slot (for the run summary)

    def _expire(self, now):
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()

    def allow(self, important: bool) -> bool:
        if self.max <= 0:
            return True
        now = self.clock()
        self._expire(now)
        if not important:
            if len(self.calls) >= int(self.max * 0.9):
                return False
        else:
            while len(self.calls) >= self.max:  # wait for the oldest call to leave the window
                pause = 60 - (now - self.calls[0]) + 0.01
                self.sleep(pause)
                self.waited += pause
                now = self.clock()
                self._expire(now)
        self.calls.append(now)
        return True

    def load(self) -> float:
        """Calls in the last minute as a share of the maximum."""
        if self.max <= 0:
            return 0.0
        now = self.clock()
        return sum(1 for t in self.calls if now - t < 60) / self.max


class ReplayMind:
    """Answers from recorded calls: {(tick, point): row}. Needs no model and no budget."""

    active = True

    def __init__(self, records: dict, config: LLMConfig):
        self.records = records
        self.config = config
        self.conversation_turns = config.conversation_turns
        self.reflects = config.reflection
        self.used = 0

    def decide(self, ctx, index: int, point: Point) -> Decision | None:
        return self.ask(ctx, index, point_question(point, ctx, self))

    def ask(self, ctx, index: int, q: Question):
        row = self.records.get((ctx.tick, index))
        if row is None:
            return None
        if row["agent_id"] != q.agent.id or row["kind"] != q.kind:
            raise ReplayDiverged(
                f"tick {ctx.tick} point {index}: recorded {row['kind']} by agent "
                f"{row['agent_id']}, but replay asks {q.kind} by agent {q.agent.id}"
            )
        if row["outcome"] not in (OK, REJECTED):
            return None
        self.used += 1
        result = q.parse(row["response"], None)
        if result is None:
            raise ReplayDiverged(f"tick {ctx.tick} point {index}: recorded answer no longer fits")
        return result


class PromptCollector:
    """Builds prompts without asking anyone (the rules decide). Used by the model benchmark."""

    active = True
    conversation_turns = 1
    reflects = True
    embedder = None

    def __init__(self, start_hour: int = 8, memories_in_prompt: int = 6):
        self.start_hour = start_hour
        self.memories_in_prompt = memories_in_prompt
        self.prompts: list[dict] = []

    def decide(self, ctx, index: int, point: Point):
        return self.ask(ctx, index, point_question(point, ctx, self))

    def recall(self, agent, ctx, query, about_id=None, exclude=None):
        from genesis.mind import stream

        return stream.retrieve(
            agent.stream,
            ctx.tick,
            query,
            about_id,
            self.memories_in_prompt,
            exclude=exclude,
            decay=stream.per_tick_decay(ctx.world.rules, ctx.world.calendar),
        )

    def ask(self, ctx, index: int, q: Question):
        keys = q.schema["properties"].get("choice", {}).get("enum")
        self.prompts.append(
            {"kind": q.kind, "prompt": q.prompt(), "keys": keys, "schema": q.schema}
        )
        return None


def point_question(point: Point, ctx, mind) -> Question:
    keys = point.keys
    max_chars = mind.config.max_line_chars if hasattr(mind, "config") else 160
    max_tokens = mind.config.max_output_tokens if hasattr(mind, "config") else 150
    start_hour = getattr(mind, "start_hour", 8)
    return Question(
        kind=point.kind,
        agent=point.agent,
        other=point.other,
        prompt=lambda: prompts.build(point, ctx, start_hour, mind.recall),
        schema=schemas.schema(keys),
        parse=lambda text, record: _decision(text, point, max_chars, record),
        max_tokens=max_tokens,
    )


def _decision(text: str, point: Point, max_chars: int, record: dict | None) -> Decision | None:
    keys = point.keys
    try:
        answer = schemas.parse(text, keys, max_chars)
    except schemas.InvalidAnswer as e:
        if record is not None:
            record["outcome"] = INVALID
            record["error"] = str(e)
        return None

    choice = answer.choice if keys else point.options[0].key if point.options else point.kind
    say = answer.say
    leaked = leak_words(say or "") + leak_words(answer.thought or "")
    if leaked:  # M8f: the choice stands, but words of our world are not spoken
        say = None
        if record is not None:
            record["error"] = "leak dropped: " + ", ".join(sorted(set(leaked)))
    if record is not None:
        record.update(choice=choice, say=say, thought=answer.thought)
    return Decision(choice=choice, say=say, thought=answer.thought, call=record)


def ask_many(mind, ctx, questions: list) -> list:
    """Independent questions, each with its own decision point (in order), answered together
    where the mind can (M8.5 workers) and one by one otherwise: the same either way."""
    items = [(ctx.next_point(), q) for q in questions]
    if hasattr(mind, "ask_many"):
        return mind.ask_many(ctx, items)
    return [mind.ask(ctx, index, q) for index, q in items]


def voiced(ctx, kind: str, a, b) -> bool:
    """M8f: whether the LLM voices this greeting or free-time conversation, or leaves it to the
    rules. Decided from the tick and the pair alone (no chance used, so replays agree)."""
    import zlib

    mind = ctx.mind
    config = getattr(mind, "config", None)
    if config is None:
        return True
    if kind == "greet":
        return config.voice_greetings
    share = config.chatter_share
    if share >= 1:
        return True
    return zlib.crc32(f"{ctx.tick}:{a.id}:{b.id}".encode()) % 1000 < share * 1000


# =================================================================
# Attaching a mind to a simulation (CLI and viewer)
# =================================================================


def attach(sim, enabled: bool | None = None, gateway=None) -> str:
    """Give the simulation an LLM mind if the world (or `enabled`) asks for one.

    Returns a one-line status for the user. The simulation runs either way."""
    import os

    config = sim.config.llm
    use = config.enabled if enabled is None else enabled
    if not use:
        sim.mind = None
        return "LLM mind: off (rules decide everything)"

    if gateway is None and os.environ.get("GENESIS_FAKE_LLM"):
        from genesis.llm.fake import FakeGateway  # checks and demos (M8.5)

        gateway = FakeGateway()
    if gateway is None:
        url = os.environ.get("GENESIS_OLLAMA_URL", config.url)
        gateway = gw.make_gateway(config, url)
    from genesis.llm.embeddings import Embedder

    embedder = Embedder(gateway, config.embed_model, sim) if config.embed_model else None
    sim.mind = LLMMind(config, gateway, sim.config.seed, sim.config.start_hour, embedder)

    ok, info = gateway.check() if hasattr(gateway, "check") else (True, [])
    if not ok:
        return f"LLM mind: on, but {info}. The rules decide until it is back."
    missing = [
        m for m in {config.chat_model, config.mind_model} if info and not _installed(m, info)
    ]
    if missing:
        return (
            f"LLM mind: on, but model(s) {', '.join(sorted(missing))} not installed "
            f"(ollama pull ...). The rules decide meanwhile."
        )
    models = config.chat_model
    if config.mind_model != config.chat_model:
        models += f" (chat) + {config.mind_model} (hard choices)"
    if embedder is not None and info and not _installed(config.embed_model, info):
        sim.mind.embedder = None
        memory = f"memory search by shared words ({config.embed_model} not installed)"
    elif embedder is None:
        memory = "memory search by shared words"
    else:
        memory = f"memory search by {config.embed_model}"
    reflect = ", nightly reflection" if config.reflection else ""
    return f"LLM mind: on, {models}, budget {config.max_calls_per_tick}/tick, {memory}{reflect}"


def _installed(model: str, installed: list[str]) -> bool:
    name = model if ":" in model else model + ":latest"
    return name in installed or model in installed
