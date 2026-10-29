"""Each agent's memory stream (M5). See docs/05-llm-and-memory.md (5.4).

- Memories form from events by rules: who was involved, how much it mattered (importance
  1–10), written from what *this* agent could know (a listener never knows a lie was a lie).
- Retrieval scores an agent's own memories only:
      0.5 × recency (0.995 per hour) + importance / 10 + relevance (+0.5 if about the person
      faced)
  Relevance comes from embeddings when a model is available, else from shared words.
- Forgetting: small memories older than `forget_after_days` fold into one summary per day.
- Beliefs (from the agent's own reflection) and important memories are kept for life.

Memory formation and forgetting are rules, so they happen identically in every run, with or
without an LLM. Retrieval only shapes prompts, which are recorded with their answers.
"""

import re
from dataclasses import dataclass, field

import numpy as np

from genesis.history import events as ev
from genesis.history.describe import describe

TICKS_PER_DAY = 24
RECENCY_DECAY = 0.995  # per hour: a day ago ≈ 0.89, 20 days ago ≈ 0.09
RECENCY_WEIGHT = 0.5  # the last few exchanges are shown anyway; let what mattered win
ABOUT_BONUS = 0.5  # memories about the person in front of you come to mind more easily

# Kinds of memory.
OBSERVATION = "observation"
CONVERSATION = "conversation"
TOLD = "told"
BELIEF = "belief"
SUMMARY = "summary"


@dataclass
class Memory:
    id: int
    tick: int
    kind: str
    text: str
    importance: int  # 1–10
    about: tuple[int, ...] = ()  # the other agents it concerns
    source_event_id: int | None = None
    source_agent_id: int | None = None  # who told me (told, gossip)

    def to_row(self, agent_id: int) -> tuple:
        return (
            self.id,
            agent_id,
            self.tick,
            self.kind,
            self.text,
            self.importance,
            list(self.about),
            self.source_event_id,
            self.source_agent_id,
        )


@dataclass
class MemoryStream:
    items: list[Memory] = field(default_factory=list)

    def add(self, memory: Memory):
        self.items.append(memory)

    def beliefs(self, n: int = 3) -> list[Memory]:
        return [m for m in self.items if m.kind == BELIEF][-n:]

    def since(self, tick: int, min_importance: int = 1) -> list[Memory]:
        return [m for m in self.items if m.tick >= tick and m.importance >= min_importance]

    def __len__(self):
        return len(self.items)


# =================================================================
# Forming memories from events
# =================================================================

# For a speech act: (importance for the listener, importance for the speaker).
# Greetings and chit-chat only matter when said in the speaker's own words.
MESSAGE_IMPORTANCE = {
    "greet": (2, 1),
    "chat": (2, 2),
    "share_info": (4, 3),
    "request": (3, 3),
    "give": (6, 4),
    "gossip": (4, 3),
    "praise": (4, 2),
    "insult": (6, 3),
    "threaten": (8, 5),
    "propose": (7, 6),
    "found": (6, 6),
    "invite": (5, 4),
    "order": (5, 4),
    "challenge": (8, 8),
    "attack": (8, 7),
    "teach": (6, 5),
    "trade": (4, 3),  # M8d
    "repay": (6, 4),
    "heal": (7, 4),
}
REPLY_IMPORTANCE = {  # answer -> (listener, speaker)
    "no": (6, 4),  # refused to share
    "refuse": (5, 6),  # stood up to a threat
    "won't say": (5, 3),
    "don't know": (3, 2),
    "have none": (3, 2),
    "agree": (9, 9),  # to have a child together
    "decline": (6, 4),
    "join": (7, 6),
    "decline_join": (5, 3),
    "obey": (4, 5),
    "refuse_order": (6, 6),
    "accept_trade": (5, 5),
    "refuse_trade": (4, 3),
}
OTHER_IMPORTANCE = {
    ev.FOUNDER_ARRIVED: 5,
    ev.MET: 3,
    ev.DISCOVERED: 3,
    ev.SUFFERING: 5,
    ev.LIFE_STAGE: 6,
    ev.SKILL_IMPROVED: 4,
    ev.RELATION_CHANGED: 5,
    ev.LIE_DISCOVERED: 8,
    ev.GRIEVED: 9,
    ev.LEFT_GROUP: 7,
    ev.LEADER_CHANGED: 7,
    ev.ORDER_DONE: 3,
    ev.GROUP_STORE: 2,
    ev.EXPERIMENTED: 2,  # M8b: making things
    ev.INVENTED: 9,
    ev.MADE: 3,
    ev.BUILT: 6,
    ev.LEARNED: 6,
    ev.GOAL_SET: 6,  # M8c
    ev.GOAL_REACHED: 9,
    ev.GOAL_ABANDONED: 5,
    ev.FELL_SICK: 7,  # M8e
    ev.RECOVERED: 5,
    ev.BEAST_ATTACK: 8,
    ev.MIGRATED: 8,
    ev.PLANNED: 4,  # M8f
}
# Events every listed agent remembers: type -> (importance for actors, for targets).
SHARED = {
    ev.CONCEIVED: (9, None),
    ev.PREGNANCY_LOST: (9, None),
    ev.BIRTH: (5, 10),  # the newborn's first memory; the parents never forget it
    ev.FOUNDED_GROUP: (8, 8),
    ev.JOINED_GROUP: (7, 5),
    ev.LEADER_CHALLENGED: (8, 8),
    ev.PROMISE_BROKEN: (5, 8),  # the one let down remembers it longer
    ev.CURRENCY_EMERGED: (7, None),
    ev.DISASTER: (9, None),  # everyone in the valley lives through it
}
CLOSE = ("friend", "close friend")


def memories_from_event(event, agents: dict, names: dict) -> list[tuple[int, dict]]:
    """[(agent_id, memory fields)] for everyone who would remember this event."""
    if event.origin == ev.SYSTEM:
        return []
    if event.type == ev.MESSAGE:
        return _from_message(event, names)
    if event.type == ev.DIED:
        return _from_death(event, agents, names)
    if event.type in SHARED:
        return _shared(event, names)
    if event.type == ev.FIGHT:
        return _from_fight(event, names)

    importance = OTHER_IMPORTANCE.get(event.type)
    if importance is None or not event.actors:
        return []
    d = event.data
    kind, source = OBSERVATION, None
    if event.type == ev.DISCOVERED and d.get("how") == "told":
        kind, importance = TOLD, 4
        source = next((i for i, n in names.items() if n == d.get("by")), None)
    if event.type == ev.RELATION_CHANGED and d.get("faded"):
        importance = 3
    if event.type == ev.SKILL_IMPROVED and "expert" in str(d.get("description")):
        importance = 6
    if event.type == ev.LEARNED and not d.get("learned"):
        return []  # the lesson did not stick (the teach message itself is remembered)
    return [
        (
            event.actors[0],
            {
                "kind": kind,
                "text": describe(event, names),
                "importance": importance,
                "about": tuple(event.targets),
                "source_agent_id": source,
            },
        )
    ]


def _from_message(event, names) -> list:
    d = event.data
    act, payload = d.get("act"), d.get("payload", {})
    voiced = bool(d.get("line"))
    if act in ("greet", "chat") and not voiced:
        return []  # routine rule-made pleasantries carry no content worth keeping

    if act == "reply":
        listener_imp, speaker_imp = REPLY_IMPORTANCE.get(payload.get("answer"), (3, 2))
    else:
        listener_imp, speaker_imp = MESSAGE_IMPORTANCE.get(act, (3, 2))
    if act == "give" and payload.get("under_threat"):
        listener_imp, speaker_imp = 5, 8  # the bully got it; the victim was robbed
    if voiced and act not in ("greet", "chat"):  # words carry more than a bare act
        listener_imp, speaker_imp = min(10, listener_imp + 1), min(10, speaker_imp + 1)

    speaker, listener = event.actors[0], event.targets[0]
    text = describe(event, names)
    told_by_liar = text.endswith(" [LIE]")
    plain = text.removesuffix(" [LIE]")
    about_extra = (payload["about"],) if act == "gossip" and "about" in payload else ()

    kind = TOLD if act in ("share_info", "gossip") else CONVERSATION
    return [
        (
            speaker,
            {
                "kind": CONVERSATION,
                "text": plain + (" (I lied)" if told_by_liar else ""),
                "importance": speaker_imp,
                "about": (listener, *about_extra),
                "source_agent_id": None,
            },
        ),
        (
            listener,
            {
                "kind": kind,
                "text": plain,  # the listener can't know it was a lie
                "importance": listener_imp,
                "about": (speaker, *about_extra),
                "source_agent_id": speaker if kind == TOLD else None,
            },
        ),
    ]


def _shared(event, names) -> list:
    actor_imp, target_imp = SHARED[event.type]
    text = describe(event, names)
    everyone = set(event.actors) | set(event.targets)
    out = []
    for ids, importance in ((event.actors, actor_imp), (event.targets, target_imp)):
        if importance is None:
            continue
        for agent_id in ids:
            out.append(
                (
                    agent_id,
                    {
                        "kind": OBSERVATION,
                        "text": text,
                        "importance": importance,
                        "about": tuple(sorted(everyone - {agent_id})),
                        "source_agent_id": None,
                    },
                )
            )
    return out


def _from_fight(event, names) -> list:
    """Fighters never forget a fight (the loser least of all); witnesses remember it too."""
    d = event.data
    text = describe(event, names)
    fighters = (event.actors[0], event.targets[0])
    out = []
    for agent_id in (*fighters, *d.get("witnesses", [])):
        importance = 9 if agent_id == d.get("loser") else 8 if agent_id in fighters else 7
        out.append(
            (
                agent_id,
                {
                    "kind": OBSERVATION,
                    "text": text,
                    "importance": importance,
                    "about": tuple(i for i in fighters if i != agent_id),
                    "source_agent_id": None,
                },
            )
        )
    return out


def _from_death(event, agents, names) -> list:
    """The dead don't remember; everyone who knew them does."""
    dead = event.actors[0]
    text = describe(event, names)
    out = []
    for agent in agents.values():
        if not agent.alive or agent.id == dead:
            continue
        rel = agent.relationships.get(dead)
        if rel is None or not rel.met:
            continue
        family = dead in agent.parents or agent.id in agents[dead].parents
        family = (
            family or agent.partner == dead or bool(set(agent.parents) & set(agents[dead].parents))
        )
        importance = 9 if family else 8 if rel.label() in CLOSE else 5
        out.append(
            (
                agent.id,
                {
                    "kind": OBSERVATION,
                    "text": text,
                    "importance": importance,
                    "about": (dead,),
                    "source_agent_id": None,
                },
            )
        )
    return out


# =================================================================
# Retrieval
# =================================================================

WORD = re.compile(r"[a-z]+")
STOPWORDS = frozenset(
    "a an the to of and or in on at for with is are was were be i you me my your it that "
    "this there they them their he she his her we our from as by not no so do did some any "
    "what where who said have has had will would can could just".split()
)


def words(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 2}


def lexical_relevance(query: str, memories: list[Memory]) -> np.ndarray:
    """Shared words between the query and each memory (cosine on word sets), 0–1."""
    q = words(query)
    if not q:
        return np.zeros(len(memories))
    out = np.empty(len(memories))
    for i, m in enumerate(memories):
        w = words(m.text)
        out[i] = len(q & w) / np.sqrt(len(q) * len(w)) if w else 0.0
    return out


def score(
    memories: list[Memory],
    now: int,
    relevance: np.ndarray,
    about_id: int | None = None,
    decay: float = RECENCY_DECAY,
) -> np.ndarray:
    ticks = np.fromiter((m.tick for m in memories), dtype=np.float64, count=len(memories))
    importance = np.fromiter(
        (m.importance for m in memories), dtype=np.float64, count=len(memories)
    )
    recency = decay ** np.maximum(0, now - ticks)
    total = RECENCY_WEIGHT * recency + importance / 10 + relevance
    if about_id is not None:
        about = np.fromiter(
            (about_id in m.about for m in memories), dtype=bool, count=len(memories)
        )
        total = total + ABOUT_BONUS * about
    return total


def retrieve(
    stream: MemoryStream,
    now: int,
    query: str,
    about_id: int | None = None,
    k: int = 6,
    relevance=None,  # callable(query, memories) -> np.ndarray; default: shared words
    exclude: set[int] | None = None,  # event ids already in the prompt
    decay: float = RECENCY_DECAY,  # recency per tick; see per_tick_decay()
) -> list[Memory]:
    """The k memories that come to mind, oldest first. Only ever this agent's own stream."""
    candidates = [
        m
        for m in stream.items
        if m.kind != BELIEF and (not exclude or m.source_event_id not in exclude)
    ]
    if not candidates:
        return []
    rel = None
    if relevance is not None:
        rel = relevance(query, candidates)
    if rel is None:
        rel = lexical_relevance(query, candidates)
    scores = score(candidates, now, rel, about_id, decay)
    top = np.argsort(-scores, kind="stable")[:k]
    return sorted((candidates[i] for i in top), key=lambda m: (m.tick, m.id))


def per_tick_decay(rules, calendar) -> float:
    """Recency fades by `memory_recency_per_day` each day, converted to one tick."""
    return rules.memory_recency_per_day ** (calendar.hours_per_tick / 24)


# =================================================================
# Forgetting
# =================================================================


def forget(stream: MemoryStream, now: int, rules, calendar=None) -> tuple[list[Memory], list[dict]]:
    """Fold small, old memories into one summary per day.

    Returns (the memories forgotten, the summaries to add as memory fields)."""
    tpd = calendar.ticks_per_day if calendar is not None else TICKS_PER_DAY
    cutoff = now - rules.forget_after_days * tpd
    old_small = [
        m
        for m in stream.items
        if m.tick < cutoff
        and m.importance <= rules.forget_max_importance
        and m.kind not in (BELIEF, SUMMARY)
    ]
    if not old_small:
        return [], []

    by_day: dict[int, list[Memory]] = {}
    for m in old_small:
        by_day.setdefault(calendar.day_index(m.tick) if calendar else m.tick // tpd, []).append(m)

    summaries = []
    for day, group in sorted(by_day.items()):
        parts = [_short(m.text) for m in group[:4]]
        more = f"; and {len(group) - 4} more small things" if len(group) > 4 else ""
        about = tuple(sorted({a for m in group for a in m.about}))
        summaries.append(
            {
                "kind": SUMMARY,
                "text": f"{calendar.when(group[0].tick) if calendar else f'Day {day + 1}'}, "
                "small things: " + "; ".join(parts) + more,
                "importance": 2,
                "about": about,
                "source_agent_id": None,
                "tick": group[-1].tick,
            }
        )

    gone = {m.id for m in old_small}
    stream.items = [m for m in stream.items if m.id not in gone]
    return old_small, summaries


def _short(text: str, limit: int = 70) -> str:
    text = text.split(': "')[0]  # drop quoted words; the gist is enough
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
