"""Culture beyond tools (S2.4): shared ideas that spread, change and die.

Kinds of ideas (techniques and lore have their own registries: concepts and research):

    belief   what someone came to think (from a night's reflection, LLM)
    value    a belief about how one should live ("we must ...", LLM)
    story    a tale of something that happened: a death, a disaster, a first making (rules)
    custom   a shared way of doing something at a death (rules, once seen often enough)
    norm     a group's way: "here we share food with those who ask" (rules)

Every idea knows the events it grew from (`grounded`) and the ideas it grew out of
(`parents`: the family tree). People hold ideas with a strength; ideas spread in talk (trust
and fit with what the listener holds), parents pass them to children, held ideas fade unless
heard again, and an idea nobody holds is gone. An idea held by three or more gets a word of
its valley's own, and the LLM is told the ideas people hold and their words.

Effects: shared beliefs, values and stories are in every prompt (they shape choices); a
sharing norm makes its holders more willing to give and they feel bad breaking it; a custom of
mourning together softens grief and draws mourners closer.

On with `rules.culture = 1`. See docs/08 (S2.4).
"""

import re

from genesis.history import events as ev

KINDS = ("belief", "value", "story", "custom", "norm")
SAME = 0.5  # word overlap: the same idea (held again, not new)
KIN = 0.25  # word overlap: a variant (a new idea with this one as its parent)
SPREAD = 0.1  # + 0.3 trust + 0.2 fit
FADE = 0.0007  # a day (unheard, a story is forgotten in a few years)
DROP = 0.05
NAMED_AT = 3  # holders before an idea gets a valley word
CHILD_TAKES = 0.2  # a day beside a parent
NORM_DAYS = 30
NORM_SHARE = 0.5  # of a group's members doing it lately
MOURNINGS = 3  # deaths mourned together before it is a custom
STOP = set(
    "the and that this with have from they them their there what when your will would "
    "into about been were more some only just like than then also because which while "
    "even ever very much many such each other others who whom".split()
)
VALUE = re.compile(r"\b(should|must|ought|always|never|we need)\b", re.I)


def enabled(rules) -> bool:
    return bool(getattr(rules, "culture", 0))


def state(world) -> dict:
    return world.culture


def ideas(world) -> dict:
    return world.culture["ideas"]


def content(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3 and w not in STOP}


def similar(a: str, b: str) -> float:
    x, y = content(a), content(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def valley_of(ctx, agent) -> str:
    return ctx.world.region_at.get(agent.position, "")


# =================================================================
# Holding and new ideas
# =================================================================


def hold(agent, idea_id: int, strength: float):
    """Hold an idea (or hold it more strongly)."""
    old = agent.ideas.get(idea_id, 0.0)
    agent.ideas[idea_id] = round(min(1.0, max(old, strength) if not old else old + 0.2), 3)


def new_idea(ctx, kind, text, by, grounded=(), parents=(), valley="") -> int:
    culture = state(ctx.world)
    iid = culture["next"]
    culture["next"] = iid + 1
    ideas(ctx.world)[iid] = {
        "id": iid,
        "kind": kind,
        "text": text,
        "name": None,
        "valley": valley,
        "by": by,
        "tick": ctx.tick,
        "grounded": list(grounded),
        "parents": list(parents),
        "lost": None,
    }
    ctx.log.emit(
        ctx.tick,
        ev.IDEA_BORN,
        actors=(by,) if by else (),
        data={"idea": iid, "kind": kind, "text": text, "parents": list(parents)},
        cause_id=grounded[0] if grounded else None,
    )
    return iid


def from_belief(ctx, agent, text: str, event_id: int):
    """A belief from a night's reflection joins the valley's ideas: held again if the valley
    already has it, a variant of the closest one, or new."""
    if not enabled(ctx.world.rules):
        return
    kind = "value" if VALUE.search(text) else "belief"
    valley = valley_of(ctx, agent)
    best, score = None, 0.0
    for idea in ideas(ctx.world).values():
        if idea["kind"] in ("belief", "value") and idea["valley"] == valley and not idea["lost"]:
            s = similar(text, idea["text"])
            if s > score:
                best, score = idea, s
    if best is not None and score >= SAME:
        hold(agent, best["id"], 0.6)
        return
    parents = [best["id"]] if best is not None and score >= KIN else []
    iid = new_idea(ctx, kind, text, agent.id, [event_id], parents, valley)
    hold(agent, iid, 0.7)


def story(ctx, text: str, grounded: int, holders, by=None):
    """A tale of something that happened, held by those who saw it."""
    if not enabled(ctx.world.rules):
        return None
    holders = [h for h in holders if h.alive]
    if not holders:
        return None
    iid = new_idea(ctx, "story", text, by, [grounded], (), valley_of(ctx, holders[0]))
    for h in holders:
        hold(h, iid, 0.6)
    return iid


# =================================================================
# What happens in the world (rules)
# =================================================================


def on_death(ctx, dead, died_event, mourners: list):
    """A death: a story if they were someone (a founder, a leader, much mourned), and once
    deaths are mourned together often, a custom."""
    if not enabled(ctx.world.rules):
        return
    founder = dead.generation == 0
    leader = any(
        g.leader == dead.id for g in ctx.groups.values() if getattr(g, "leader", None) is not None
    )
    if founder or leader or len(mourners) >= 3:
        who = "one of the first in the valley" if founder else "who led their people"
        who = who if (founder or leader) else "whom many mourned"
        story(ctx, f"The story of {dead.name}, {who}", died_event.id, mourners)
    if len(mourners) < 2:
        return
    culture = state(ctx.world)
    valley = valley_of(ctx, mourners[0])
    key = f"mourn:{valley}"
    culture["acts"][key] = culture["acts"].get(key, 0) + 1
    custom = _custom(ctx.world, valley)
    if custom is None and culture["acts"][key] >= MOURNINGS:
        iid = new_idea(
            ctx,
            "custom",
            "When one of us dies, we gather and mourn together",
            mourners[0].id,
            [died_event.id],
            (),
            valley,
        )
        custom = ideas(ctx.world)[iid]
    if custom is not None:
        for m in mourners:
            hold(m, custom["id"], 0.5)


def _custom(world, valley):
    return next(
        (
            i
            for i in ideas(world).values()
            if i["kind"] == "custom" and i["valley"] == valley and not i["lost"]
        ),
        None,
    )


def mourns_together(agent, world) -> bool:
    """Holds the custom of mourning together (grief is softer, mourners grow closer)."""
    return any(
        ideas(world).get(i, {}).get("kind") == "custom" and s >= 0.3 for i, s in agent.ideas.items()
    )


def on_disaster(ctx, kind: str, event_id: int, people: list):
    """Each valley it struck tells its own tale of it."""
    if not enabled(ctx.world.rules) or not people:
        return
    year = ctx.world.calendar.day_index(ctx.tick) // ctx.world.calendar.days_per_year + 1
    by_valley: dict[str, list] = {}
    for a in people:
        by_valley.setdefault(valley_of(ctx, a), []).append(a)
    for valley in sorted(by_valley):
        story(ctx, f"The great {kind} of year {year}", event_id, by_valley[valley])


def on_first_making(ctx, inventor, concept, event_id: int):
    if enabled(ctx.world.rules):
        story(
            ctx,
            f"How {inventor.name} first made the {concept.name}",
            event_id,
            [inventor],
            by=inventor.id,
        )


def did(ctx, agent, act: str):
    """Someone did something a norm can grow from (shared food with one who asked)."""
    if enabled(ctx.world.rules):
        state(ctx.world)["acts"][f"{act}:{agent.id}"] = ctx.tick


NORMS = {"share": "Here we share food and water with those who ask"}


def sharing_norm(agent, world) -> float:
    """How strongly they hold their group's norm of sharing (0 if not)."""
    return max(
        (
            s
            for i, s in agent.ideas.items()
            if ideas(world).get(i, {}).get("kind") == "norm"
            and ideas(world)[i]["text"].startswith("Here we share")
        ),
        default=0.0,
    )


def broke_norm(ctx, agent, other, resource):
    """Refused to share while holding the norm: they feel it (it weakens), and it is known."""
    for i, s in list(agent.ideas.items()):
        idea = ideas(ctx.world).get(i)
        if idea and idea["kind"] == "norm" and idea["text"].startswith("Here we share"):
            agent.ideas[i] = round(max(0.0, s - 0.2), 3)
            ctx.log.emit(
                ctx.tick,
                ev.NORM_BROKEN,
                actors=(agent.id,),
                targets=(other.id,),
                data={"idea": i, "norm": idea["text"], "resource": resource},
            )
            return


# =================================================================
# Spreading, fading, naming (talk and once a day)
# =================================================================


def share(ctx, speaker, listener):
    """After a free-time chat: maybe pass on the idea the speaker holds most strongly that the
    listener lacks. Trust, and fit with what the listener already holds, make it stick."""
    if not enabled(ctx.world.rules) or not speaker.ideas:
        return
    pool = ideas(ctx.world)
    new = sorted(
        (i for i in speaker.ideas if i not in listener.ideas and not pool.get(i, {}).get("lost")),
        key=lambda i: (-speaker.ideas[i], i),
    )
    if not new:
        return
    idea = pool[new[0]]
    rel = listener.relationships.get(speaker.id)
    trust = max(0.0, rel.trust) if rel is not None else 0.0
    held = [pool[i]["text"] for i in listener.ideas if i in pool]
    fit = max((similar(idea["text"], t) for t in held), default=0.5) if held else 0.5
    if ctx.rng.random() < SPREAD + 0.3 * trust + 0.2 * fit:
        hold(listener, idea["id"], 0.4)


def daily(ctx, sim):
    """Once a day: ideas fade unless heard again; children take their parents' ideas; norms
    are noticed; ideas nobody holds are gone; widely held ideas get a valley word."""
    if not enabled(ctx.world.rules):
        return
    world = ctx.world
    pool = ideas(world)
    alive = [a for a in sorted(ctx.agents.values(), key=lambda a: a.id) if a.alive]
    for a in alive:
        for i in list(a.ideas):
            a.ideas[i] = round(a.ideas[i] - FADE, 3)
            if a.ideas[i] < DROP or i not in pool:
                del a.ideas[i]
        if a.stage == "child":
            for pid in a.parents:
                parent = ctx.agents.get(pid)
                if parent is None or not parent.alive or not parent.ideas:
                    continue
                lack = sorted(i for i in parent.ideas if i not in a.ideas)
                if lack and ctx.rng.random() < CHILD_TAKES:
                    hold(a, lack[0], 0.7)
    _norms(ctx, alive)
    holders: dict[int, int] = {}
    for a in alive:
        for i in a.ideas:
            holders[i] = holders.get(i, 0) + 1
    for iid in sorted(pool):
        idea = pool[iid]
        if idea["lost"]:
            continue
        n = holders.get(iid, 0)
        if n == 0:
            idea["lost"] = ctx.tick
            ctx.log.emit(ctx.tick, ev.IDEA_LOST, data={"idea": iid, "text": idea["text"]})
        elif n >= NAMED_AT and idea["name"] is None:
            idea["name"] = _word(ctx, idea)


def _norms(ctx, alive):
    """A group where half the members shared food lately has a sharing norm."""
    acts = state(ctx.world)["acts"]
    window = ctx.world.calendar.ticks(days=NORM_DAYS)
    for g in sorted(ctx.groups.values(), key=lambda g: g.id):
        if g.ended_tick is not None or len(g.members) < 3:
            continue
        members = [ctx.agents[m] for m in g.members if m in ctx.agents and ctx.agents[m].alive]
        sharers = [m for m in members if ctx.tick - acts.get(f"share:{m.id}", -(10**9)) <= window]
        if len(sharers) < NORM_SHARE * len(members):
            continue
        key = f"norm:share:{g.id}"
        if key not in acts:
            acts[key] = new_idea(
                ctx, "norm", NORMS["share"], sharers[0].id, (), (), valley_of(ctx, sharers[0])
            )
        iid = acts[key]
        if ideas(ctx.world)[iid]["lost"]:
            continue
        for m in members:
            if iid not in m.ideas:
                hold(m, iid, 0.5)


def _word(ctx, idea) -> str:
    """A word of the valley for a widely held idea (the valley's own sounds, not taken)."""
    from genesis.culture import inventions as inv
    from genesis.culture.words import is_valley_word

    taken = {c.name.lower() for c in ctx.world.concepts.values()}
    taken |= {(i["name"] or "").lower() for i in ideas(ctx.world).values()}
    taken |= {a.name.lower() for a in ctx.agents.values()}
    holder = next((a for a in ctx.agents.values() if idea["id"] in a.ideas), None)
    sounds = inv._valley_sounds(ctx, holder) if holder is not None else inv.SYLLABLES
    for _ in range(100):
        w = "".join(ctx.rng.choice(sounds) for _ in range(ctx.rng.choice((2, 3))))
        if w not in taken and is_valley_word(w, taken):
            return w.capitalize()
    return f"Idea{idea['id']}"


def prompt_lines(agent, world, n: int = 3) -> list[str]:
    """For the LLM: the ideas they hold most strongly, by their valley words."""
    pool = ideas(world)
    held = sorted((i for i in agent.ideas if i in pool), key=lambda i: (-agent.ideas[i], i))[:n]
    lines = []
    for i in held:
        idea = pool[i]
        word = f"{idea['name']}: " if idea["name"] else ""
        lines.append(f"- {word}{idea['text']} ({idea['kind']})")
    if not lines:
        return []
    named = any(pool[i]["name"] for i in held)
    tail = ["When you speak of these, use your people's words for them."] if named else []
    return ["What your people hold, in your own words:", *lines, *tail]
