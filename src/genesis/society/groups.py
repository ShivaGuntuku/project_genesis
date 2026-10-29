"""Groups, leaders and orders (M7). See docs/04-society.md (4.4).

A group exists only because people agreed to it, and every change of membership is an
event with a cause:

    found     an ambitious agent with friends asks one of them to stand together
    invite    a member asks someone to join (someone who resents their own leader may
              switch sides)
    leave     a member with a grudge against the leader walks away (checked nightly)
    born      a child joins the group of the parent who bore them
    died      the dead leave their group; a dead leader is succeeded by the most respected

Leaders give orders that members obey or refuse; ambitious members can challenge the
leader, and the other members back whoever they respect (and, if aggressive, fear) more.
Nobody is labelled "boss" by hand: authority is respect, earned and lost.
"""

import json
from dataclasses import asdict, dataclass, field

from genesis.agents import lifecycle
from genesis.history import events as ev
from genesis.llm.mind import Option, Question
from genesis.world.world import Position

TICKS_PER_DAY = 24
SYLLABLES = "ka ri to mi sa lo ne da vi ru el an or ta ze bo li en ia mo su ha ni ve".split()
FRIENDLY = ("friend", "close friend")


@dataclass
class Group:
    id: int
    name: str
    founder: int
    leader: int
    members: list[int]
    home: tuple[int, int]
    founded_tick: int
    store_food: int = 0
    ended_tick: int | None = None

    @property
    def home_position(self) -> Position:
        return Position(*self.home)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Group":
        return cls(**{**d, "home": tuple(d["home"]), "members": list(d["members"])})


@dataclass
class GroupView:
    """What a member knows about its own group, refreshed every tick (decisions read it)."""

    id: int
    name: str
    home: Position
    store_food: int
    leader: int
    members: tuple[int, ...] = field(default_factory=tuple)


def _emit(ctx, type, actors=(), targets=(), **kwargs) -> int:
    event_id = ctx.log.emit(ctx.tick, type, actors=actors, targets=targets, **kwargs)
    for agent_id in (*actors, *targets):
        agent = ctx.agents.get(agent_id)
        if agent is not None:
            agent.memory.remember_event(event_id)
    return event_id


def group_of(ctx, agent) -> Group | None:
    if agent.group_id is None:
        return None
    return ctx.groups.get(agent.group_id)


def friends(ctx, agent) -> list:
    return [
        ctx.agents[i]
        for i, rel in sorted(agent.relationships.items())
        if rel.label() in FRIENDLY and i in ctx.agents and ctx.agents[i].alive
    ]


def _cooldown(ctx, agent, key) -> bool:
    last = agent.asked.get(key)
    days = ctx.world.rules.group_ask_cooldown_days
    return last is not None and ctx.tick - last < ctx.world.calendar.ticks(days=days)


# =================================================================
# Founding and joining
# =================================================================


def may_found(ctx, a, b) -> bool:
    r = ctx.world.rules
    if a.group_id is not None or b.group_id is not None:
        return False
    if lifecycle.CHILD in (a.stage, b.stage):
        return False
    if a.genome.ambition < r.found_min_ambition or len(friends(ctx, a)) < r.found_min_friends:
        return False
    if _cooldown(ctx, a, f"{b.id}:group"):
        return False
    rel = a.relationships.get(b.id)
    return rel is not None and rel.affinity >= r.invite_min_affinity


def may_invite(ctx, a, b) -> bool:
    """A member asks someone outside their group (possibly from another group) to join."""
    r = ctx.world.rules
    g = group_of(ctx, a)
    if g is None or b.group_id == g.id or b.stage == lifecycle.CHILD:
        return False
    if len(g.members) >= r.max_group_size or _cooldown(ctx, a, f"{b.id}:group"):
        return False
    left = b.asked.get(f"left:{g.id}")
    if left is not None and ctx.tick - left < ctx.world.calendar.ticks(days=r.rejoin_days):
        return False  # walked out recently: not back so soon
    rel = a.relationships.get(b.id)
    return rel is not None and rel.affinity >= r.invite_min_affinity


def ask_to_join(ctx, a, b, reply_to=None, voice=None):
    """a asks b to found a group together, or to join a's group. Returns b's answer id."""
    from genesis.society.interaction import Act, feel, say, think

    g = group_of(ctx, a)
    a.asked[f"{b.id}:group"] = ctx.tick
    act = Act.INVITE if g is not None else Act.FOUND
    payload = {"group": g.id, "group_name": g.name} if g is not None else {}
    asked = say(ctx, a, b, act, payload, reply_to=reply_to, voice=voice)

    old = group_of(ctx, b)
    what = f"join {g.name}, led by {ctx.agents[g.leader].name}" if g else f"stand with {a.name}"
    leave = f" (and leave {old.name})" if old else ""
    options = [Option("join", f"Agree to {what}{leave}"), Option("decline", "Decline")]
    heard = f'{a.name} said: "{voice.say}"' if voice is not None else None
    decision = think(ctx, "join", b, a, options, heard=heard)
    if decision is not None:
        accept = decision.choice == "join"
    else:
        accept = ctx.rng.random() < _join_chance(ctx, b, a, g, old)

    answer = say(
        ctx,
        b,
        a,
        Act.REPLY,
        {"answer": "join" if accept else "decline_join", "about": "group"},
        reply_to=asked,
        voice=decision,
    )
    if not accept:
        return answer
    if old is not None:
        leave_group(ctx, b, f"joined {g.name if g else 'a new group'}", answer)
    if g is None:
        found(ctx, a, b, answer)
    else:
        join(ctx, b, g, "invited", answer, by=a)
    feel(ctx, b, a, "joined_with", answer)
    feel(ctx, a, b, "joined_with", answer)
    return answer


def _join_chance(ctx, b, a, g, old) -> float:
    rel = b.relationship(a.id, a.name)
    chance = 0.1 + 0.6 * max(0.0, rel.affinity) + 0.2 * rel.respect + 0.2 * b.genome.sociability
    if g is not None and g.leader != a.id and g.leader in b.relationships:
        leader_rel = b.relationships[g.leader]
        chance += 0.3 * leader_rel.respect - 0.6 * leader_rel.grievance
    if old is not None:  # switching sides: only if their own leader has lost them
        own = b.relationships.get(old.leader)
        loyalty = (own.affinity + own.respect - own.grievance) if own and old.leader != b.id else 1
        chance -= 0.5 + 0.5 * loyalty
    return chance


def found(ctx, a, b, cause) -> Group:
    name = choose_name(ctx, a, b)
    gid = max(ctx.groups, default=0) + 1
    g = Group(
        id=gid,
        name=name,
        founder=a.id,
        leader=a.id,
        members=[a.id, b.id],
        home=(a.position.x, a.position.y),
        founded_tick=ctx.tick,
    )
    ctx.groups[gid] = g
    a.group_id = b.group_id = gid
    _accept_leader(ctx, b, g, cause)
    _emit(
        ctx,
        ev.FOUNDED_GROUP,
        actors=(a.id,),
        targets=(b.id,),
        position=a.position,
        data={"group": gid, "name": name},
        cause_id=cause,
    )
    return g


def join(ctx, agent, g, how, cause, by=None):
    g.members.append(agent.id)
    agent.group_id = g.id
    _accept_leader(ctx, agent, g, cause)
    _emit(
        ctx,
        ev.JOINED_GROUP,
        actors=(agent.id,),
        targets=(by.id,) if by is not None else (),
        position=agent.position,
        data={"group": g.id, "name": g.name, "how": how},
        cause_id=cause,
    )


def _accept_leader(ctx, member, g, cause):
    """Joining means accepting the leader: some respect comes with it."""
    from genesis.society.interaction import feel

    leader = ctx.agents.get(g.leader)
    if leader is not None and leader.id != member.id:
        feel(ctx, member, leader, "follows", cause)


def leave_group(ctx, agent, reason, cause):
    g = group_of(ctx, agent)
    if g is None:
        return
    g.members.remove(agent.id)
    agent.group_id = None
    agent.asked[f"left:{g.id}"] = ctx.tick
    agent.order = None
    event_id = _emit(
        ctx,
        ev.LEFT_GROUP,
        actors=(agent.id,),
        targets=(g.leader,) if g.leader != agent.id else (),
        position=agent.position,
        data={"group": g.id, "name": g.name, "reason": reason},
        cause_id=cause,
    )
    if not g.members:
        g.ended_tick = ctx.tick
        _emit(ctx, ev.GROUP_ENDED, data={"group": g.id, "name": g.name}, cause_id=event_id)
    elif g.leader == agent.id:
        succeed(ctx, g, "the leader left", event_id)


def succeed(ctx, g, reason, cause):
    """The member the others respect most takes over."""
    old = g.leader

    def standing(candidate):
        total = 0.0
        for m in g.members:
            if m != candidate:
                rel = ctx.agents[m].relationships.get(candidate)
                if rel is not None:
                    total += rel.respect + 0.5 * rel.affinity
        return (total, -candidate)

    adults = [m for m in g.members if ctx.agents[m].stage != lifecycle.CHILD] or g.members
    g.leader = max(adults, key=standing)
    for m in g.members:
        if m != g.leader:
            _accept_leader(ctx, ctx.agents[m], g, cause)
    _emit(
        ctx,
        ev.LEADER_CHANGED,
        actors=(g.leader,),
        targets=(old,),
        data={"group": g.id, "name": g.name, "reason": reason},
        cause_id=cause,
    )


# =================================================================
# Names
# =================================================================


def choose_name(ctx, founder, other) -> str:
    import re

    taken = {g.name.lower() for g in ctx.groups.values()}
    taken |= {a.name.lower() for a in ctx.agents.values()}
    mind = ctx.mind
    if mind is not None and mind.active:

        def prompt():
            from genesis.llm import persona

            return "\n".join(
                [
                    persona.fact_block(founder, ctx),
                    "",
                    f"You and {other.name} have just agreed to stand together as a group.",
                    "Give your group a name: one or two words, a name the valley has never "
                    'used before. Answer in JSON: "thought", "name", and "say" (what you '
                    f"tell {other.name}).",
                ]
            )

        def parse(text, record):
            try:
                data = json.loads(text)
                name = " ".join(str(data.get("name", "")).split())
            except (ValueError, TypeError, AttributeError):
                name = ""
            ok = re.fullmatch(r"[A-Za-z]{2,14}( [A-Za-z]{2,14})?", name or "")
            if not ok or name.lower() in taken:
                if record is not None:
                    record["outcome"] = "invalid"
                    record["error"] = f"group name {name!r} is not new or not 1-2 words"
                return None
            name = " ".join(w.capitalize() for w in name.split())
            if record is not None:
                record.update(choice=name, say=str(data.get("say", ""))[:160])
            return name

        question = Question(
            kind="group_name",
            agent=founder,
            other=other,
            prompt=prompt,
            schema={
                "type": "object",
                "properties": {
                    "thought": {"type": "string"},
                    "name": {"type": "string"},
                    "say": {"type": "string"},
                },
                "required": ["thought", "name", "say"],
            },
            parse=parse,
            max_tokens=80,
            budgeted=False,
        )
        name = mind.ask(ctx, ctx.next_point(), question)
        if name is not None:
            return name
    while True:
        name = "".join(ctx.rng.choice(SYLLABLES) for _ in range(ctx.rng.choice((2, 3))))
        name = name.capitalize()
        if name.lower() not in taken:
            return name


# =================================================================
# Leadership
# =================================================================


def may_challenge(ctx, a, leader) -> bool:
    r = ctx.world.rules
    g = group_of(ctx, a)
    if g is None or g.leader != leader.id or a.id == leader.id or a.stage != lifecycle.ADULT:
        return False
    if a.genome.ambition < r.challenge_min_ambition or _cooldown(ctx, a, f"{leader.id}:challenge"):
        return False
    adults = [m for m in g.members if ctx.agents[m].stage != lifecycle.CHILD]
    if len(adults) < 3:  # with two voters, a vote is always a tie
        return False
    rel = a.relationships.get(leader.id)
    return rel is not None and (rel.respect < 0.3 or rel.grievance >= 0.3)


def challenge(ctx, a, leader, reply_to=None, voice=None):
    """a challenges the leader; the other members back one of them."""
    from genesis.society.interaction import Act, feel, say

    g = group_of(ctx, a)
    a.asked[f"{leader.id}:challenge"] = ctx.tick
    event_id = say(ctx, a, leader, Act.CHALLENGE, {"group": g.id}, reply_to=reply_to, voice=voice)

    votes = {a.id: [a.id], leader.id: [leader.id]}
    for m in sorted(g.members):
        if m in (a.id, leader.id) or ctx.agents[m].stage == lifecycle.CHILD:
            continue
        member = ctx.agents[m]
        votes[
            max((a.id, leader.id), key=lambda c: _backing(member, ctx.agents[c], leader.id))
        ].append(m)
    won = len(votes[a.id]) > len(votes[leader.id])
    winner, loser = (a, leader) if won else (leader, a)
    if won:
        g.leader = a.id
    _emit(
        ctx,
        ev.LEADER_CHALLENGED,
        actors=(a.id,),
        targets=(leader.id,),
        data={
            "group": g.id,
            "name": g.name,
            "won": won,
            "votes": {ctx.agents[c].name: len(v) for c, v in votes.items()},
        },
        cause_id=event_id,
    )
    feel(ctx, loser, winner, "lost_challenge", event_id)
    for m in votes[winner.id]:
        if m != winner.id:
            feel(ctx, ctx.agents[m], winner, "backed", event_id)
    return event_id


def _backing(member, candidate, incumbent) -> float:
    rel = member.relationships.get(candidate.id)
    if rel is None:
        return -1.0 if candidate.id != incumbent else 0.0
    score = rel.respect + 0.5 * rel.affinity - 0.5 * rel.grievance
    if candidate.genome.aggression >= 0.6:
        score += 0.5 * rel.fear
    if candidate.id == incumbent:
        score += 0.1  # habit: people stick with the leader they have
    return score


# =================================================================
# Orders
# =================================================================


def order_options(ctx, leader, member) -> list[Option]:
    """What a leader may tell a member to do right now."""
    g = group_of(ctx, leader)
    if g is None or g.leader != leader.id or member.group_id != g.id or member.id == leader.id:
        return []
    if member.order is not None or member.stage == lifecycle.CHILD:
        return []
    if member.asked.get("ordered_day") == ctx.day:
        return []
    name = member.name
    options = [Option("order:gather", f"Tell {name} to bring food to the group's store")]
    r = ctx.world.rules
    for other_id in sorted(g.members):
        other = ctx.agents.get(other_id)
        if other is None or other.id in (leader.id, member.id) or not other.alive:
            continue
        if other.hunger >= r.seek_threshold or other.thirst >= r.seek_threshold:
            options.append(Option(f"order:help:{other.id}", f"Tell {name} to help {other.name}"))
            break
    for other_id, rel in sorted(leader.relationships.items()):
        other = ctx.agents.get(other_id)
        if (
            other is not None
            and other.alive
            and other.id != member.id
            and rel.label() in ("rival", "enemy")
        ):
            options.append(
                Option(f"order:shun:{other.id}", f"Tell {name} to keep away from {other.name}")
            )
            break
    return options


def give_order(ctx, leader, member, key, reply_to=None, voice=None):
    """The leader orders; the member obeys or refuses. Returns the answer's event id."""
    from genesis.society.interaction import Act, feel, say, think

    _, kind, *rest = key.split(":")
    target = int(rest[0]) if rest else None
    target_name = ctx.agents[target].name if target else None
    member.asked["ordered_day"] = ctx.day
    asked = say(
        ctx,
        leader,
        member,
        Act.ORDER,
        {"kind": kind, "target": target, "target_name": target_name},
        reply_to=reply_to,
        voice=voice,
    )
    words = {
        "gather": "bring food to the group's store",
        "help": f"help {target_name}",
        "shun": f"keep away from {target_name}",
    }[kind]
    options = [Option("obey", f"Do it: {words}"), Option("refuse", f"Refuse {leader.name}")]
    heard = f'{leader.name}, your leader, said: "{voice.say}"' if voice else None
    heard = heard or f"{leader.name}, your leader, tells you to {words}."
    decision = think(ctx, "obey", member, leader, options, heard=heard)
    if decision is not None:
        obey = decision.choice == "obey"
    else:
        obey = ctx.rng.random() < _obey_chance(member, leader, kind, target)

    answer = say(
        ctx,
        member,
        leader,
        Act.REPLY,
        {"answer": "obey" if obey else "refuse_order", "about": kind},
        reply_to=asked,
        voice=decision,
    )
    _emit(
        ctx,
        ev.ORDER_GIVEN,
        actors=(leader.id,),
        targets=(member.id,),
        data={"kind": kind, "target": target, "obeyed": obey, "group": member.group_id},
        cause_id=answer,
    )
    if not obey:
        feel(ctx, leader, member, "disobeyed", answer)
        return answer
    feel(ctx, leader, member, "obeyed", answer)
    if kind == "shun":  # taking the leader's side: done at once
        other = ctx.agents[target]
        feel(ctx, member, other, {"affinity": -0.15, "trust": -0.1}, answer, met=True)
        _done(ctx, member, leader, kind, True, answer)
    else:
        member.order = {
            "kind": kind,
            "target": target,
            "leader": leader.id,
            "tick": ctx.tick,
            "event": answer,
        }
    return answer


def _obey_chance(member, leader, kind, target) -> float:
    rel = member.relationship(leader.id, leader.name)
    chance = (
        0.25
        + 0.5 * rel.respect
        + 0.4 * rel.fear
        + 0.2 * rel.affinity
        - 0.3 * rel.grievance
        - 0.3 * member.genome.ambition
    )
    if kind == "shun" and target in member.relationships:
        chance -= 0.4 * max(0.0, member.relationships[target].affinity)
    return chance


def _done(ctx, member, leader, kind, done, cause):
    from genesis.society.interaction import feel

    _emit(
        ctx,
        ev.ORDER_DONE,
        actors=(member.id,),
        targets=(leader.id,),
        data={"kind": kind, "done": done},
        cause_id=cause,
    )
    if leader.alive:
        feel(
            ctx, leader, member, "obeyed" if done else {"affinity": -0.05, "grievance": 0.05}, cause
        )
    member.order = None


def order_fulfilled(ctx, member, kind, cause):
    """Called when the member does what was ordered (contributing, helping)."""
    o = member.order
    if o is None or o["kind"] != kind:
        return
    leader = ctx.agents.get(o["leader"])
    if leader is not None:
        _done(ctx, member, leader, kind, True, cause)


# =================================================================
# The store, every night, and births/deaths
# =================================================================


def contribute(ctx, member):
    g = group_of(ctx, member)
    r = ctx.world.rules
    if g is None or member.inventory.get("food", 0) < r.food_per_meal:
        return None
    member.inventory["food"] -= r.food_per_meal
    g.store_food += r.food_per_meal
    event_id = _store_event(ctx, member, g, "put in")
    order_fulfilled(ctx, member, "gather", event_id)
    return event_id


def take(ctx, member) -> bool:
    g = group_of(ctx, member)
    r = ctx.world.rules
    if g is None or g.store_food < r.food_per_meal:
        return False
    room = member.carry_capacity("food", r) - member.inventory.get("food", 0)
    if room < r.food_per_meal:
        return False
    g.store_food -= r.food_per_meal
    member.inventory["food"] += r.food_per_meal
    _store_event(ctx, member, g, "took")
    return True


def _store_event(ctx, member, g, action) -> int:
    return _emit(
        ctx,
        ev.GROUP_STORE,
        actors=(member.id,),
        position=member.position,
        data={"group": g.id, "name": g.name, "action": action, "store": g.store_food},
    )


def nightly(ctx):
    """Members who resent their leader leave; orders not carried out in time lapse."""
    r = ctx.world.rules
    for g in sorted(ctx.groups.values(), key=lambda g: g.id):
        if g.ended_tick is not None:
            continue
        for m in list(g.members):
            member = ctx.agents[m]
            if m == g.leader or member.stage == lifecycle.CHILD:
                continue
            rel = member.relationships.get(g.leader)
            if rel is not None and (
                rel.grievance >= r.leave_grievance or rel.affinity <= -r.leave_grievance
            ):
                leave_group(
                    ctx, member, f"a grudge against {ctx.agents[g.leader].name}", rel.last_event_id
                )
        for m in list(g.members):
            member = ctx.agents[m]
            o = member.order
            if o is not None and ctx.tick - o["tick"] > ctx.world.calendar.ticks(days=r.order_days):
                leader = ctx.agents.get(o["leader"])
                if leader is not None:
                    _done(ctx, member, leader, o["kind"], False, o["event"])
                else:
                    member.order = None


def notice(ctx, agent, other_id):
    """Territory: a member resents outsiders from another group in their home area, a
    little each day (it is how a drought that sends one group to the other's lake turns
    into friction)."""
    g = group_of(ctx, agent)
    other = ctx.agents.get(other_id)
    if g is None or other is None or other.group_id in (None, g.id):
        return
    if other.position.distance(g.home_position) > ctx.world.rules.home_radius:
        return
    key = f"{other_id}:trespass"
    if agent.asked.get(key) == ctx.day:
        return
    agent.asked[key] = ctx.day
    from genesis.society.interaction import feel

    # One event per outsider, group and day; during a drought, the drought is its cause.
    mark = f"trespass:{g.id}"
    if other.asked.get(mark) != ctx.day:
        drought = next(
            (i for k, i in sorted(ctx.world.condition_events.items()) if k.startswith("drought")),
            None,
        )
        other.asked[mark] = ctx.day
        other.asked[f"trespass_event:{g.id}"] = _emit(
            ctx,
            ev.TRESPASS,
            actors=(other.id,),
            position=other.position,
            data={"group": g.id, "name": g.name, "drought": drought is not None},
            cause_id=drought,
        )
    feel(ctx, agent, other, "trespass", other.asked.get(f"trespass_event:{g.id}"))


def on_birth(ctx, child, carrier, birth_event):
    g = group_of(ctx, carrier)
    if g is not None:
        join(ctx, child, g, "born", birth_event)


def on_death(ctx, dead, died_event):
    g = group_of(ctx, dead)
    if g is not None:
        leave_group(ctx, dead, "died", died_event)


def view(g: Group) -> GroupView:
    return GroupView(g.id, g.name, g.home_position, g.store_food, g.leader, tuple(g.members))
