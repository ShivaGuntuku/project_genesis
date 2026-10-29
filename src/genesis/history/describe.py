"""Turn events into short human-readable sentences."""

from genesis.history import events as ev
from genesis.history.events import Event

TICKS_PER_DAY = 24
NEED_WORDS = {
    "thirst": ("thirsty", "quenched their thirst", "dehydration"),
    "hunger": ("hungry", "ate their fill", "hunger"),
    "old age": ("old", "", "old age"),
}


def clock(tick: int, calendar) -> str:
    """'Day 12 14:00' (story mode) or 'Year 3, dry season day 40, dusk' (watches).

    `calendar` is a Calendar, or a start hour for hourly worlds."""
    if isinstance(calendar, int):
        total = calendar + tick
        return f"Day {total // TICKS_PER_DAY + 1} {total % TICKS_PER_DAY:02d}:00"
    return calendar.clock(tick)


DISASTER_WORDS = {
    "drought": "a great drought",
    "winter": "a harsh winter",
    "flood": "a flood",
    "sickness": "a sickness",
    "predators": "beasts",
    "wildfire": "a wildfire",
}


def describe(event: Event, names: dict[int, str]) -> str:
    def who(ids):
        return ", ".join(names.get(i, f"#{i}") for i in ids) or "someone"

    actor, target, d = who(event.actors), who(event.targets), event.data
    where = f" at ({event.x},{event.y})" if event.x is not None else ""
    need = NEED_WORDS.get(d.get("need", ""), ("in need", "recovered", "need"))

    match event.type:
        case ev.WORLD_CREATED:
            return f"World '{d.get('name')}' was created (seed {d.get('seed')})"
        case ev.FOUNDER_ARRIVED:
            return f"{actor} arrived in the world{where}"
        case ev.NEED_BECAME_PRESSING:
            return f"{actor} became {need[0]} ({d.get('need')} {d.get('level')})"
        case ev.NEED_SATISFIED:
            return f"{actor} {need[1]}{where}"
        case ev.DISCOVERED:
            if d.get("how") == "told":
                return f"{actor} learned from {d.get('by')} where {d.get('resource')} is{where}"
            return f"{actor} discovered {d.get('resource')}{where}"
        case ev.MET:
            return f"{actor} met {target} for the first time{where}"
        case ev.SUFFERING:
            return f"{actor} began suffering from {need[2]}"
        case ev.DIED:
            return f"{actor} died of {d.get('cause')}{where}"
        case ev.LIFE_STAGE:
            stage = d.get("stage")
            words = {"adult": "came of age", "elder": "became an elder"}
            return (
                f"{actor} {words.get(stage, 'became ' + str(stage))} (age {d.get('age_days')} days)"
            )
        case ev.MESSAGE:
            return describe_message(
                d["act"],
                d.get("payload", {}),
                actor,
                target,
                d.get("truthful", True),
                names,
                line=d.get("line"),
            )
        case ev.RELATION_CHANGED:
            faded = " (feelings faded)" if d.get("faded") else ""
            return f"{actor} now sees {target} as {_a(d.get('to'))} (was {d.get('from')}){faded}"
        case ev.LIE_DISCOVERED:
            return f"{actor} found out {target} lied about {d.get('resource')}{where}"
        case ev.SKILL_IMPROVED:
            return f"{actor} became {d.get('description')}"
        case ev.CONCEIVED:
            return f"{actor.replace(', ', ' and ')} are expecting a child"
        case ev.BIRTH:
            return f"{actor} was born to {target.replace(', ', ' and ')}{where}"
        case ev.PREGNANCY_LOST:
            return f"{actor.replace(', ', ' and ')} lost their unborn child ({d.get('reason')})"
        case ev.GRIEVED:
            widowed = ", and is now widowed" if d.get("widowed") else ""
            return f"{actor} grieves for {target}, their {d.get('relation')}{widowed}"
        case ev.WORLD_CHANGED:
            what = (
                f"lake {d.get('target')}"
                if d.get("kind") == "drought"
                else (f"berry patch {d.get('target')}")
            )
            if d.get("kind") == "drought":
                return (
                    f"A drought: {what} dried up"
                    if d.get("started")
                    else (f"The drought ended: {what} has water again")
                )
            return (
                f"A famine: {what} stopped growing"
                if d.get("started")
                else (f"The famine ended: {what} grows again")
            )
        case ev.TRESPASS:
            dry = " during the drought" if d.get("drought") else ""
            return f"{actor} came into the home area of {d.get('name')}{dry}{where}"
        case ev.FOUNDED_GROUP:
            return f"{actor} and {target} founded a group: {d.get('name')}{where}"
        case ev.JOINED_GROUP:
            how = {"born": " (born into it)", "invited": f" (asked by {target})"}.get(
                d.get("how"), ""
            )
            return f"{actor} joined {d.get('name')}{how}"
        case ev.LEFT_GROUP:
            return f"{actor} left {d.get('name')}: {d.get('reason')}"
        case ev.GROUP_ENDED:
            return f"The group {d.get('name')} is no more"
        case ev.LEADER_CHANGED:
            return f"{actor} now leads {d.get('name')} ({d.get('reason')})"
        case ev.LEADER_CHALLENGED:
            votes = ", ".join(f"{k} {v}" for k, v in (d.get("votes") or {}).items())
            if d.get("won"):
                return f"{actor} won the lead of {d.get('name')} from {target} ({votes})"
            return f"{target} kept the lead of {d.get('name')} against {actor} ({votes})"
        case ev.ORDER_GIVEN:
            answer = "obeyed" if d.get("obeyed") else "refused"
            return f"{target} {answer} {actor}'s order ({d.get('kind')})"
        case ev.ORDER_DONE:
            return (
                f"{actor} {'carried out' if d.get('done') else 'never carried out'} "
                f"{target}'s order ({d.get('kind')})"
            )
        case ev.GROUP_STORE:
            verb = "put a meal into" if d.get("action") == "put in" else "took a meal from"
            return f"{actor} {verb} the store of {d.get('name')} (now {d.get('store')})"
        case ev.FIGHT:
            took = d.get("took")
            loot = f", taking {took['resource']}" if took else ""
            return (
                f"{actor} attacked {target}; {d.get('winner_name')} won{loot} "
                f"({d.get('loser_name')} hurt: -{d.get('damage')} health)"
            )
        case ev.EXPERIMENTED:
            mats, acts = d.get("recipe") or [[], []]
            tried = " and ".join(mats) or "nothing"
            how = ", ".join(acts) or "as it was"
            return f"{actor} tried {tried} ({how}) for {d.get('problem')}; it did not work"
        case ev.INVENTED:
            mats, acts = d.get("recipe") or [[], []]
            return (
                f"{actor} invented {d.get('name')}: a thing that {d.get('function')} "
                f"({' + '.join(mats)}, {', '.join(acts)})"
            )
        case ev.MADE:
            return f"{actor} made a {d.get('name')} ({d.get('function')})"
        case ev.BUILT:
            return f"{actor} built a {d.get('name')}{where}"
        case ev.LEARNED:
            if d.get("learned"):
                return f"{actor} learned how to make {d.get('name')} from {target}"
            return f"{actor} could not yet follow {target}'s way of making {d.get('name')}"
        case ev.CONCEPT_LOST:
            return f"Nobody alive knows how to make {d.get('name')} any more ({d.get('function')})"
        case ev.DISASTER:
            what = DISASTER_WORDS.get(d.get("kind"), d.get("kind"))
            where = "every valley" if d.get("region") == "all" else d.get("region")
            if d.get("started"):
                return f"{what.capitalize()} struck {where} ({d.get('people')} people there)"
            return f"{what.capitalize()} in {where} is over"
        case ev.FELL_SICK:
            src = f", from {d.get('from')}" if d.get("from") else ""
            return f"{actor} fell sick{src}"
        case ev.RECOVERED:
            return f"{actor} got over the sickness"
        case ev.BEAST_ATTACK:
            if d.get("driven_off"):
                return f"{actor} drove off beasts in the night"
            return f"Beasts attacked {actor} in the night (-{d.get('damage')} health)"
        case ev.INTERVENTION:
            return "God: " + _intervention(d, actor)
        case ev.PLANNED:
            why = f" ({d.get('why')})" if d.get("why") else ""
            return f"{actor} set out to: {d.get('label')}{why}"
        case ev.MIGRATED:
            return f"{actor} has moved from {d.get('from')} to {d.get('to')}"
        case ev.PROMISE_BROKEN:
            return f"{actor} broke their promise to bring {target} {d.get('good')}"
        case ev.CURRENCY_EMERGED:
            return (
                f"In {d.get('region')}, people now take {d.get('good')} in trade to pass it on: "
                f"they call it {d.get('name')}"
            )
        case ev.GOAL_SET:
            own = " (in their own words)" if d.get("how") == "own words" else ""
            return f'{actor} set a goal{own}: "{d.get("text")}"'
        case ev.GOAL_REACHED:
            return f'{actor} reached a goal after {d.get("days")} days: "{d.get("text")}"'
        case ev.GOAL_ABANDONED:
            return f'{actor} gave up on "{d.get("text")}" ({d.get("reason")})'
        case ev.REFLECTED:
            beliefs = d.get("beliefs") or []
            if beliefs:
                return f'{actor} reflected on the day and now believes: "{beliefs[0]}"' + (
                    f" (+{len(beliefs) - 1} more)" if len(beliefs) > 1 else ""
                )
            return f"{actor} reflected on the day"
        case _:
            return f"{event.type} {actor} {target} {d}".strip()


def _intervention(d, actor) -> str:
    act = d.get("act")
    where = f" at ({d.get('x', '')},{d.get('y', '')})" if "x" in d else ""
    match act:
        case "add_food":
            return f"a new berry patch (#{d.get('food')})"
        case "remove_food":
            return f"berry patch #{d.get('food')} taken away"
        case "lake":
            return f"a pool of water appeared ({d.get('size')}x{d.get('size')}){where}"
        case "dry":
            return f"water became land ({d.get('size')}x{d.get('size')}){where}"
        case "disaster":
            what = DISASTER_WORDS.get(d.get("kind"), d.get("kind"))
            return f"{what} sent to {d.get('region')} for {d.get('days')} days"
        case "migrants":
            return f"{d.get('count')} newcomers arrived in {d.get('region')}"
        case "strike":
            return f"{d.get('name')} was struck down"
        case "whisper":
            return f'a voice spoke to {actor}: "{d.get("text")}"'
    return str(act)


def _a(label: str | None) -> str:
    if not label:
        return "someone"
    return ("an " if label[0] in "aeiou" else "a ") + label


def describe_message(act, payload, speaker, listener, truthful=True, names=None, line=None) -> str:
    """One speech act as a sentence, with the agent's own words if any (from M4).
    Lies are marked (the observer sees the truth)."""
    text = _message(act, payload, speaker, listener, truthful, names or {})
    if line:
        lie = " [LIE]"
        marked = text.endswith(lie)
        text = text.removesuffix(lie) + f': "{line}"' + (lie if marked else "")
    return text


def _message(act, payload, speaker, listener, truthful, names) -> str:
    lie = " [LIE]" if not truthful else ""
    p = payload
    match act:
        case "greet":
            return f"{speaker} greeted {listener}"
        case "chat":
            return f"{speaker} chatted with {listener}"
        case "share_info":
            where = f"({p.get('x')},{p.get('y')})"
            return f"{speaker} told {listener} there is {p.get('resource')} at {where}{lie}"
        case "request":
            if "info" in p:
                return f"{speaker} asked {listener} where to find {p['info']}"
            return f"{speaker} asked {listener} to spare some {p.get('item')}"
        case "give":
            threat = " (under threat)" if p.get("under_threat") else ""
            what = f"{p.get('amount')} food" if p.get("resource") == "food" else "a drink of water"
            return f"{speaker} gave {listener} {what}{threat}"
        case "gossip":
            about = names.get(p.get("about"), p.get("about_name", "someone"))
            return f"{speaker} told {listener} that {about} {p.get('claim')}"
        case "praise":
            return f"{speaker} praised {listener}"
        case "insult":
            return f"{speaker} insulted {listener}"
        case "threaten":
            return f"{speaker} threatened {listener}, demanding {p.get('demand')}"
        case "propose":
            return f"{speaker} asked {listener} to have a child together"
        case "found":
            return f"{speaker} asked {listener} to stand together as a group"
        case "invite":
            return f"{speaker} asked {listener} to join {p.get('group_name', 'their group')}"
        case "order":
            what = {
                "gather": "bring food to the store",
                "help": f"help {p.get('target_name')}",
                "shun": f"keep away from {p.get('target_name')}",
            }.get(p.get("kind"), "obey")
            return f"{speaker} ordered {listener} to {what}"
        case "challenge":
            return f"{speaker} challenged {listener} for the lead"
        case "attack":
            return f"{speaker} attacked {listener}"
        case "teach":
            return f"{speaker} showed {listener} how to make {p.get('name')} ({p.get('function')})"
        case "trade":
            if p.get("promise"):
                return (
                    f"{speaker} asked {listener} for {p.get('gets')}, promising to bring "
                    "the same back"
                )
            return f"{speaker} offered {listener} {p.get('gives')} for {p.get('gets')}"
        case "repay":
            return f"{speaker} brought {listener} {p.get('what')}, as promised"
        case "heal":
            return f"{speaker} tended {listener}'s wounds (+{p.get('amount')} health)"
        case "reply":
            about = p.get("about", "it")
            match p.get("answer"):
                case "don't know":
                    return f"{speaker} told {listener} they don't know where {about} is"
                case "have none":
                    return f"{speaker} told {listener} they have no {about} to spare"
                case "no":
                    return f"{speaker} refused to share {about} with {listener}"
                case "refuse":
                    return f"{speaker} refused to hand over {about} to {listener}"
                case "join":
                    return f"{speaker} agreed to join {listener}"
                case "accept_trade":
                    if p.get("promise"):
                        return f"{speaker} gave {listener} {p.get('gets')} on a promise"
                    extra = " (to pass on)" if p.get("indirect") else ""
                    return (
                        f"{speaker} took {p.get('gives')} from {listener} for {p.get('gets')}"
                        f"{extra}"
                    )
                case "refuse_trade":
                    return f"{speaker} turned down {listener}'s offer"
                case "decline_join":
                    return f"{speaker} declined to join {listener}"
                case "obey":
                    return f"{speaker} agreed to do as {listener} said"
                case "refuse_order":
                    return f"{speaker} refused {listener}'s order"
                case "agree":
                    return f"{speaker} agreed to have a child with {listener}"
                case "decline":
                    return f"{speaker} declined to have a child with {listener}"
                case "won't say":
                    return f"{speaker} refused to tell {listener} where {about} is"
            return f"{speaker} replied to {listener}: {p.get('answer')}"
    return f"{speaker} → {listener}: {act} {p}"
