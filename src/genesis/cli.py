"""Command line: create, run, inspect and question Genesis worlds.

genesis new [--config worlds/eden.toml] [--name eden] [--seed 42] [--founders 3]
genesis run eden --days 30            # continues from where it stopped
genesis status eden
genesis inspect eden Mira
genesis events eden [--agent Mira] [--type died] [--limit 30]
genesis why eden 812                  # the chain of causes behind event 812
genesis metrics eden [--genes] [--every 5]
genesis conversations eden Mira [John]
genesis relations eden
genesis view eden                     # live pixel-art viewer in the browser
genesis dashboard eden                # observer dashboard (read-only; safe beside run/view)
genesis worlds

LLM mind (M4):
genesis new --name story --llm        # agents talk and decide with the local model
genesis run story --days 2 [--no-llm]
genesis thoughts story [Mira]         # what they said, chose and privately thought
genesis replay story                  # re-run from day 1 with the recorded answers: identical?

Families (M6):
genesis family story [Mira]           # family tree; inspect shows genes next to each parent's

Memory (M5):
genesis memories story Mira [--kind belief] [--all]
genesis memories story Mira --query "Dax asks for water" --about Dax
genesis bench-llm --models llama3,llama3.2:3b
"""

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import replace
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from genesis.agents.abilities import describe_skill
from genesis.agents.genome import GENES
from genesis.config import ConfigError, WorldConfig
from genesis.history.describe import clock, describe, describe_message
from genesis.llm import mind as llm_mind
from genesis.persistence.db import WorldStore, WorldStoreError
from genesis.simulation.calendar import calendar_of
from genesis.simulation.engine import Simulation
from genesis.society import family

console = Console()


def data_dir() -> Path:
    return Path(os.environ.get("GENESIS_DATA", "data/worlds"))


def world_path(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.suffix == ".sqlite" or p.exists():
        return p
    return data_dir() / f"{name_or_path}.sqlite"


def open_store(name_or_path: str) -> WorldStore:
    path = world_path(name_or_path)
    if not path.exists():
        raise SystemExit(f"No world at {path}. Create one with: genesis new --name {name_or_path}")
    return WorldStore(path)


# =================================================================
# Commands
# =================================================================


def cmd_new(args):
    try:
        config = WorldConfig.from_file(args.config) if args.config else WorldConfig.default()
        changes = {}
        if args.name:
            changes["name"] = args.name
        if args.seed is not None:
            changes["seed"] = args.seed
        if args.founders is not None:
            changes["founders"] = args.founders
        if args.llm:
            changes["llm"] = replace(config.llm, enabled=True)
        if changes:
            source = config.source_toml
            config = config.with_overrides(**changes)
            if source:
                shown = {k: v for k, v in changes.items() if k != "llm"}
                if args.llm:
                    shown["llm.enabled"] = True
                note = "# Created from this file, with overrides: " + json.dumps(shown) + "\n"
                config = config.__class__(**{**config.__dict__, "source_toml": note + source})
    except (ConfigError, OSError) as e:
        raise SystemExit(f"Config error: {e}") from e

    path = world_path(config.name)
    sim = Simulation(config)
    try:
        WorldStore.create(path, sim, overwrite=args.force).close()
    except WorldStoreError as e:
        raise SystemExit(f"{e}. Use --force to replace it.") from e

    console.print(
        f"Created world [bold]{config.name}[/] (seed {config.seed}, "
        f"{config.founders} founders) at {path}"
    )
    if config.llm.enabled:
        console.print(
            f"LLM mind on: {config.llm.chat_model} / {config.llm.mind_model} via Ollama. "
            "Watch what they say with: genesis thoughts " + config.name
        )
    console.print(f"Run it with: [bold]genesis run {config.name} --days 10[/]")


def cmd_run(args):
    from genesis.simulation import service

    try:
        with service.run_lock(world_path(args.world), "genesis run"):
            _cmd_run(args)
    except service.WorldBusy as e:
        raise SystemExit(str(e)) from e


def _cmd_run(args):
    store = open_store(args.world)
    sim = store.load()
    console.print(llm_mind.attach(sim, enabled=False if args.no_llm else None))
    from genesis.persistence.db import attach_journal

    replayed = attach_journal(sim, world_path(args.world), store)
    from genesis.god import interventions as god  # M9: anything queued while it was stopped

    for qid, event, error in god.apply_pending(sim, store):
        console.print(f"God command {qid}: " + (error or f"done (event {event})"))
    if replayed:
        console.print(
            f"Resuming after a stop: {replayed} LLM answers from the journal are replayed."
        )
    ticks = args.ticks if args.ticks is not None else args.days * sim.world.calendar.ticks_per_day
    target = sim.tick_count + ticks
    names = {a.id: a.name for a in sim.population.agents}
    run_id = store.start_run(sim.tick_count)

    stop = {"reason": None}

    def request_stop(signum, _frame):
        stop["reason"] = signal.Signals(signum).name  # finish the current tick, then stop

    old_int = signal.signal(signal.SIGINT, request_stop)
    old_term = signal.signal(signal.SIGTERM, request_stop)

    started = time.monotonic()
    start_tick = sim.tick_count
    reason = "crashed"

    every = sim.config.save_every_ticks

    def loop(live=None):
        while sim.tick_count < target:
            sim.tick()
            if sim.tick_count % every == 0:
                store.save(sim)  # one transaction (per tick, or per day in long runs)

            if live is not None:
                live.update(_live_view(sim, names))
            if args.delay:
                time.sleep(args.delay)
            if stop["reason"]:
                return stop["reason"]
            if not sim.population.get_alive():
                return "everyone died"
        return "finished"

    try:
        if args.quiet:
            reason = loop()
        else:
            with Live(_live_view(sim, names), console=console, refresh_per_second=8) as live:
                reason = loop(live)
        store.save(sim)  # whatever ran since the last save
    finally:
        store.end_run(run_id, sim.tick_count, reason)
        store.close()
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)

    elapsed = time.monotonic() - started
    done = sim.tick_count - start_tick
    console.print(
        f"Stopped at {clock(sim.tick_count, sim.world.calendar)} (tick {sim.tick_count}): "
        f"{reason}. Ran {done} ticks in {elapsed:.1f}s"
        + (f" ({done / elapsed:.0f} ticks/s)." if elapsed > 0 else ".")
    )
    if sim.mind is not None:
        st = sim.mind.stats
        governor = getattr(sim.mind, "governor", None)
        waited = (
            f"Waited {governor.waited:.0f}s for the LLM. " if governor and governor.waited else ""
        )
        console.print(
            f"LLM: {st['ok']} answered, {st['failed']} failed (rules decided), "
            f"{st['skipped_budget']} over budget, {st.get('skipped_rate', 0)} over the governor's "
            f"limit, {st['skipped_offline']} while offline. {waited}"
            f"See: genesis thoughts {args.world}"
        )
    console.print(f"Saved. Continue with: [bold]genesis run {args.world} --days 10[/]")


def cmd_status(args):
    store = open_store(args.world)
    sim = store.load()
    meta = store.meta()
    store.close()
    names = {a.id: a.name for a in sim.population.agents}
    console.print(_live_view(sim, names))
    console.print(
        f"File: {world_path(args.world)} | created {meta.get('created_at')} "
        f"| last saved {meta.get('saved_at')}"
    )


def cmd_inspect(args):
    store = open_store(args.world)
    sim = store.load()
    agent = sim.population.get_agent(args.agent)
    if agent is None:
        store.close()
        raise SystemExit(
            f"No agent named {args.agent}. "
            f"Known: {', '.join(a.name for a in sim.population.agents)}"
        )

    names = store.agent_names()
    start = sim.world.calendar
    m = agent.memory

    tick = sim.tick_count
    r = sim.world.rules
    tpd = sim.world.calendar.ticks_per_day
    lifespan_days = agent.lifespan(sim.world) / tpd
    age_days = agent.age(agent.died_tick if agent.died_tick is not None else tick) / tpd
    state = (
        f"alive, {agent.stage}"
        if agent.alive
        else f"died {clock(agent.died_tick, start)} of {agent.cause_of_death}"
    )
    g = agent.genome

    body = Table.grid(padding=(0, 2))
    body.add_row("Status", state)
    body.add_row(
        "Age",
        f"{age_days:.0f} days (natural lifespan {lifespan_days:.0f}; "
        f"elder from {lifespan_days * r.elder_at_fraction:.0f})",
    )
    body.add_row("Generation", f"{agent.generation} ({'founder' if not agent.parents else 'born'})")
    body.add_row("Position", f"({agent.position.x}, {agent.position.y})")
    body.add_row(
        "Needs",
        f"hunger {agent.hunger:.0f}  thirst {agent.thirst:.0f}  "
        f"energy {agent.energy:.0f}  health {agent.health:.0f}",
    )
    body.add_row("Doing", agent.current_action.value if agent.current_action else "-")
    body.add_row("Character", ", ".join(g.traits()) or "balanced")
    body.add_row("Strengths", ", ".join(g.strengths()) or "none notable")
    body.add_row("Weaknesses", ", ".join(g.weaknesses()) or "none notable")
    skills = [describe_skill(k, v) + f" ({v:.2f})" for k, v in agent.abilities.trained().items()]
    body.add_row("Skills", ", ".join(skills) or "none yet")
    body.add_row("Vision", f"{agent.vision_radius(sim.world)} tiles")
    body.add_row(
        "Carrying",
        f"{agent.inventory['food']} food units, {agent.inventory['water']} "
        f"drink{'s' if agent.inventory['water'] != 1 else ''} of water",
    )
    food = [f"({k.position.x},{k.position.y})" for k in m.get_known_locations("food")]
    body.add_row("Knows food", ", ".join(food) or "nowhere")
    body.add_row("Knows water", f"{len(m.get_known_locations('water'))} shore tiles")
    body.add_row(
        "Explored", f"{len(m.seen_positions)} of {sim.world.width * sim.world.height} tiles seen"
    )

    by_id = {a.id: a for a in sim.population.agents}
    parents = [by_id[p] for p in agent.parents if p in by_id]
    genes = Table(
        "Gene", "Value", "", *[f"from {p.name}" for p in parents], box=None, padding=(0, 1)
    )
    for name, value in g.describe().items():
        inherited = [f"{getattr(p.genome, name):.2f}" for p in parents]
        genes.add_row(name, f"{value:.2f}", "█" * round(value * 20), *inherited)

    kin = family.family(agent, by_id)
    if kin or agent.pregnant:
        order = ["partner", "parent", "child", "sibling", "grandparent", "grandchild"]
        words = [
            f"{by_id[i].name}{'' if by_id[i].alive else ' †'} ({rel})"
            for i, rel in sorted(kin.items(), key=lambda kv: (order.index(kv[1]), kv[0]))
        ]
        if agent.pregnant:
            words.insert(0, "[bold]expecting a child[/]")
        body.add_row("Family", ", ".join(words))

    people = Table(
        "Person",
        "I see them as",
        "They see me as",
        "Affinity",
        "Trust",
        "Gratitude",
        "Grievance",
        "Fear",
        "Met",
        "Encounters",
        box=None,
    )
    for other_id in sorted(agent.relationships, key=lambda i: -agent.relationships[i].affinity):
        rel = agent.relationships[other_id]
        other = sim.population.get_by_id(other_id)
        back = other.relationships.get(agent.id) if other else None
        social = m.social_memory.get(other_id)
        people.add_row(
            rel.target_name + ("" if other is None or other.alive else " †"),
            _label(rel.label()),
            _label(back.label()) if back else "-",
            f"{rel.affinity:+.2f}",
            f"{rel.trust:.2f}",
            f"{rel.gratitude:.2f}",
            f"{rel.grievance:.2f}",
            f"{rel.fear:.2f}",
            clock(social.first_seen, start) if social else "never (heard of)",
            str(social.encounters) if social else "0",
        )

    events = Table("Id", "When", "What", box=None)
    for e in store.events(agent_id=agent.id, limit=args.limit):
        events.add_row(str(e.id), clock(e.tick, start), describe(e, names))
    beliefs = store.memories(agent.id, kind="belief", limit=5)
    live = len(store.memories(agent.id, limit=100_000))
    store.close()

    mind = Table.grid(padding=(0, 2))
    mind.add_row("Story", agent.story or "[dim](no reflection yet: needs the LLM mind)[/]")
    for i, b in enumerate(reversed(beliefs)):
        mind.add_row(
            "Believes" if i == 0 else "",
            f"{b['text']} [dim]({sim.world.calendar.when(b['tick'])})[/]",
        )
    mind.add_row("Memories", f"{live} live (see: genesis memories {args.world} {agent.name})")

    console.print(Panel(body, title=f"[bold]{agent.name}[/] (id {agent.id})"))
    console.print(Panel(mind, title="Mind (in their own words)"))
    title = "Genes (inherited, 0-1)"
    if parents:
        title += ": each from one parent or their average, plus a small mutation"
    console.print(Panel(genes, title=title))
    console.print(
        Panel(people, title="Relationships (directional)")
        if agent.relationships
        else "Knows nobody yet."
    )
    console.print(
        Panel(events, title=f"Last {args.limit} events (ask why: genesis why {args.world} <id>)")
    )


def cmd_events(args):
    store = open_store(args.world)
    names = store.agent_names()
    start = calendar_of(WorldConfig.from_dict(json.loads(store.meta()["config_json"])))
    agent_id = None
    if args.agent:
        ids = [i for i, n in names.items() if n == args.agent]
        if not ids:
            raise SystemExit(f"No agent named {args.agent}")
        agent_id = ids[0]

    table = Table("Id", "When", "Type", "What", "Cause")
    for e in reversed(store.events(agent_id=agent_id, type=args.type, limit=args.limit)):
        table.add_row(
            str(e.id), clock(e.tick, start), e.type, describe(e, names), str(e.cause_id or "")
        )
    store.close()
    console.print(table)


def cmd_why(args):
    store = open_store(args.world)
    names = store.agent_names()
    start = calendar_of(WorldConfig.from_dict(json.loads(store.meta()["config_json"])))
    chain = store.cause_chain(args.event_id)

    if not chain:
        store.close()
        raise SystemExit(f"No event {args.event_id}")

    def label(e):
        origin = "" if e.origin == "natural" else f" [dim]({e.origin})[/]"
        return f"[bold]#{e.id}[/] {clock(e.tick, start)}  {describe(e, names)}{origin}"

    # Show the root cause first, down to the asked-about event.
    tree = Tree(label(chain[-1]))
    node = tree
    for e in reversed(chain[:-1]):
        node = node.add(f"[dim]led to[/] {label(e)}")

    console.print(Panel(tree, title=f"Why did event #{args.event_id} happen?"))
    if len(chain) == 1:
        console.print("No recorded cause: this event started its own chain.")

    after = store.consequences(args.event_id)
    if after:
        console.print("It led to: " + "; ".join(f"#{e.id} {describe(e, names)}" for e in after))
    store.close()


LABEL_COLOURS = {
    "close friend": "bold green",
    "friend": "green",
    "rival": "yellow",
    "enemy": "bold red",
    "stranger": "dim",
}


def _label(label: str) -> str:
    colour = LABEL_COLOURS.get(label)
    return f"[{colour}]{label}[/]" if colour else label


def _agent_id(store, name: str) -> int:
    ids = [i for i, n in store.agent_names().items() if n == name]
    if not ids:
        store.close()
        raise SystemExit(f"No agent named {name}")
    return ids[0]


def cmd_conversations(args):
    store = open_store(args.world)
    names = store.agent_names()
    start = calendar_of(WorldConfig.from_dict(json.loads(store.meta()["config_json"])))
    a = _agent_id(store, args.agent)
    b = _agent_id(store, args.other) if args.other else None
    rows = store.messages(a, b, limit=args.limit)
    store.close()

    if not rows:
        console.print("No messages yet.")
        return

    table = Table("Id", "When", "Message", "Reply to")
    for m in reversed(rows):
        text = describe_message(
            m["act"],
            m["payload"],
            names[m["from_id"]],
            names[m["to_id"]],
            bool(m["truthful"]),
            names,
            line=m["line"],
        )
        if not m["truthful"]:
            text = text.replace("[LIE]", "[bold red]\\[LIE][/]")
        table.add_row(str(m["event_id"]), clock(m["tick"], start), text, str(m["reply_to"] or ""))
    who = f"{args.agent} and {args.other}" if args.other else args.agent
    console.print(Panel(table, title=f"Conversations: {who} (oldest first)"))
    console.print(
        "[dim]LIE marks a false statement: the speaker knew better. "
        "Only you can see this; the listener can't.[/]"
    )


def cmd_relations(args):
    store = open_store(args.world)
    sim = store.load()
    store.close()
    people = [a for a in sim.population.agents if a.alive or args.all]
    table = Table("Sees →", *[a.name for a in people], title="Who sees whom as what")
    for a in people:
        cells = []
        for b in people:
            if a is b:
                cells.append("·")
                continue
            rel = a.relationships.get(b.id)
            cells.append(_label(rel.label()) if rel else "[dim]—[/]")
        table.add_row(a.name + ("" if a.alive else " †"), *cells)
    console.print(table)
    console.print(
        "[dim]Read across: how the row's person sees each column's person. Not symmetric.[/]"
    )


DEFAULT_METRICS = [
    "population.alive",
    "social.friend_links",
    "social.rival_links",
    "today.message.give",
    "today.message.lie",
    "deaths.dehydration",
    "deaths.starvation",
    "deaths.old age",
    "age.mean_days",
    "gene.metabolism.mean",
    "gene.perception.mean",
    "gene.longevity.mean",
    "skill.foraging.mean",
]


def cmd_metrics(args):
    store = open_store(args.world)
    start = calendar_of(WorldConfig.from_dict(json.loads(store.meta()["config_json"])))
    if args.genes:
        names = ["population.alive"] + [f"gene.{g}.mean" for g in GENES]
    else:
        names = args.name or DEFAULT_METRICS
    rows = store.metrics(names)
    store.close()

    if not rows:
        console.print("No daily metrics yet: run the world for at least one full day.")
        return

    def label(name: str) -> str:
        if name.startswith("deaths."):
            cause = name.removeprefix("deaths.")
            return "†" + {"dehydration": "thirst", "starvation": "hunger"}.get(cause, cause)
        for prefix in ("population.", "gene.", "skill.", "age."):
            name = name.removeprefix(prefix)
        return "age" if name == "mean_days" else name.removesuffix(".mean")

    table = Table("Day", *[label(n) for n in names])
    totals = dict.fromkeys(names, 0.0)  # deaths are shown as running totals
    ticks = sorted(rows)
    for i, tick in enumerate(ticks):
        values = rows[tick]
        cells = []
        for n in names:
            v = values.get(n)
            if n.startswith("deaths.") and v is not None:
                totals[n] += v
                v = totals[n]
            cells.append("-" if v is None else f"{v:.0f}" if float(v).is_integer() else f"{v:.2f}")
        if i % args.every == 0 or i == len(ticks) - 1:
            day = int(clock(tick, start).split()[1]) - 1
            table.add_row(str(day), *cells)
    console.print(table)
    console.print("[dim]Day = end of that day. † = total deaths so far by cause.[/]")


def cmd_view(args):
    from genesis.api.server import SPEEDS, serve

    path = world_path(args.world)
    if not path.exists():
        raise SystemExit(f"No world at {path}. Create one with: genesis new --name {args.world}")
    if args.speed not in SPEEDS:
        raise SystemExit(f"--speed must be one of {', '.join(str(s) for s in SPEEDS)} (0 = max)")
    from genesis.simulation import service

    try:
        with service.run_lock(path, "the viewer (genesis view)"):
            serve(
                path,
                port=args.port,
                speed=args.speed,
                paused=args.paused,
                open_browser=not args.no_browser,
                llm=False if args.no_llm else None,
            )
    except service.WorldBusy as e:
        raise SystemExit(str(e)) from e


def cmd_thoughts(args):
    store = open_store(args.world)
    names = store.agent_names()
    start = calendar_of(WorldConfig.from_dict(json.loads(store.meta()["config_json"])))
    agent = _agent_id(store, args.agent) if args.agent else None
    rows = store.llm_calls(agent_id=agent, kind=args.kind, limit=args.limit)
    stats = store.llm_stats()
    store.close()

    if not stats["total"]:
        console.print(
            "No LLM calls yet. The LLM mind is off for this world, or nobody has talked yet.\n"
            "Create a world with it on: genesis new --name story --llm"
        )
        return

    title = f"Thoughts: {args.agent}" if args.agent else "Thoughts"
    console.print(f"[bold]{title}[/] (oldest first)\n")
    for c in reversed(rows):
        who = names.get(c["agent_id"], "?")
        if c["other_id"] is not None:
            who += f" → {names.get(c['other_id'], '?')}"
        choice = (c["choice"] or "").split(":")[0]
        what = c["kind"] + (f"/{choice}" if choice and choice not in ("greet", "ask") else "")
        head = f"[dim]{clock(c['tick'], start)}[/]  [bold]{who}[/]  [cyan]{what}[/]"
        if c["outcome"] in ("ok", "rejected"):
            console.print(f"{head}  [dim]{c['latency_ms']} ms[/]")
            console.print(f'    "{c["say"]}"')
            if c["thought"]:
                console.print(f"    [dim italic]thinks: {c['thought']}[/]")
            if c["outcome"] == "rejected":
                console.print(f"    [red]rejected by the world: {c['error']}[/]")
        else:
            error = (c["error"] or "")[:80]
            console.print(f"{head}  [red]{c['outcome']}[/] [dim]{error}[/] → the rules decided")

    outcomes = ", ".join(f"{k} {v}" for k, v in sorted(stats["outcomes"].items()))
    avg = f"{stats['avg_ms']:.0f} ms average" if stats["avg_ms"] else ""
    console.print(f"[dim]{stats['total']} calls: {outcomes}. {avg}[/]")


def cmd_replay(args):
    """Re-run the world from its first tick, answering every decision point from the recorded
    LLM calls (no model needed), and compare with the saved world."""
    store = open_store(args.world)
    try:
        saved = store.load()
    except WorldStoreError as e:
        raise SystemExit(str(e)) from e
    records = store.llm_records()
    store.close()

    config = saved.config
    target = saved.tick_count

    sim = Simulation(config)
    replayer = llm_mind.ReplayMind(records, config.llm)
    sim.mind = replayer
    from genesis.god import interventions as god  # God's changes, at the ticks they were made

    sim.god_schedule = god.schedule_of(getattr(saved.world, "god", {}) or {})
    started = time.monotonic()
    try:
        with console.status(f"Replaying {target} ticks with {len(records)} recorded calls..."):
            sim.run(target)
    except llm_mind.ReplayDiverged as e:
        raise SystemExit(f"Replay diverged: {e}") from e

    elapsed = time.monotonic() - started
    if sim.state() == saved.state():
        console.print(
            f"[green]Identical.[/] Replayed {target} ticks in {elapsed:.1f}s from "
            f"{replayer.used} recorded answers: every agent, feeling, message and random draw "
            "matches the saved world."
        )
    else:
        console.print(
            "[red]Different.[/] The replay does not match the saved world. "
            "(Was the world created with an older Genesis, or its code changed since?)"
        )
        raise SystemExit(1)


def cmd_bench_llm(args):
    from types import SimpleNamespace

    from genesis.llm import bench
    from genesis.llm.gateway import make_gateway

    default = "http://127.0.0.1:8000" if args.provider == "openai" else "http://127.0.0.1:11434"
    url = args.url or os.environ.get("GENESIS_OLLAMA_URL", default)
    gateway = make_gateway(SimpleNamespace(provider=args.provider, timeout_seconds=60, url=url))
    ok, info = gateway.check()
    if not ok:
        raise SystemExit(info)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    missing = [m for m in models if not llm_mind._installed(m, info)]
    if missing:
        raise SystemExit(f"Not installed: {', '.join(missing)} (ollama pull <model>)")

    with console.status("Collecting real decision situations from a rules-only run..."):
        situations = bench.collect_situations(args.situations, seed=args.seed)
    kinds: dict[str, int] = {}
    for s in situations:
        kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
    console.print(
        f"{len(situations)} situations: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
    )

    with console.status("") as status:

        def progress(model, i, n):
            status.update(f"{model}: {i}/{n}")

        results = bench.run(models, situations, gateway, on_progress=progress)

    table = Table("Model", "Valid", "Mean ms", "p95 ms", "Thoughts/s*", "Earth words", "Failures")
    for r in results:
        mean_ms = bench.mean(r.latencies)
        table.add_row(
            r.model,
            f"{r.valid_pct:.0f}%",
            f"{mean_ms:.0f}",
            f"{r.latency(0.95):.0f}",
            f"{1000 / mean_ms:.1f}" if mean_ms else "-",
            str(r.leaks),
            ", ".join(f"{k} {v}" for k, v in r.errors.items()) or "-",
        )
    console.print(table)
    console.print(
        "[dim]* one request at a time; parallel slots multiply this (doc 13).[/]\n"
        "[dim]Earth words: answers mentioning things outside the valley (money, kings, ...).[/]"
    )
    for r in results:
        mean_ms = bench.mean(r.latencies)
        if mean_ms:  # calls run one after another within a tick
            suggested = int(60_000 / mean_ms * 0.95)
            console.print(
                f"Suggested governor for {r.model}: [bold]llm.max_calls_per_minute = "
                f"{suggested}[/] (one call at a time, 5% headroom)"
            )

    if args.workers > 1:  # M8.5: several requests at once (llm.workers)
        for r in results:
            with console.status(f"{r.model}: {args.workers} at a time..."):
                per_minute = bench.throughput(r.model, situations, gateway, args.workers)
            console.print(
                f"{r.model} with {args.workers} workers: {per_minute:.0f} calls a minute. "
                f"Suggested: [bold]llm.workers = {args.workers}, llm.max_calls_per_minute = "
                f"{int(per_minute * 0.95)}[/]"
            )

    for r in results:
        choices = ", ".join(f"{k} {v}" for k, v in sorted(r.choices.items()))
        console.print(f"\n[bold]{r.model}[/] choices: {choices}")
        for sample in r.samples[: args.samples]:
            choice = f"[{sample['choice']}] " if sample["choice"] else ""
            console.print(
                f'  {sample["kind"]:<8} {choice}"{sample["say"]}"  [dim]({sample["thought"]})[/]'
            )


def cmd_memories(args):
    """An agent's memory stream, or what comes to their mind for a question."""
    from genesis.mind import stream as memory

    store = open_store(args.world)
    agent_id = _agent_id(store, args.agent)
    start = calendar_of(WorldConfig.from_dict(json.loads(store.meta()["config_json"])))

    if args.query:
        sim = store.load()
        store.close()
        agent = sim.population.get_by_id(agent_id)
        if not agent.alive:
            raise SystemExit(f"{agent.name} is dead: the dead don't recall anything.")
        about = sim.population.get_agent(args.about) if args.about else None
        relevance, how = None, "shared words"
        if sim.config.llm.embed_model:
            from genesis.llm.embeddings import Embedder
            from genesis.llm.gateway import OllamaGateway

            url = os.environ.get("GENESIS_OLLAMA_URL", sim.config.llm.url)
            embedder = Embedder(OllamaGateway(url, timeout=30), sim.config.llm.embed_model, sim)
            if embedder.relevance(args.query, agent.stream.items[:1]) is not None:
                relevance, how = embedder.relevance, sim.config.llm.embed_model
        started = time.perf_counter()
        found = memory.retrieve(
            agent.stream,
            sim.tick_count,
            args.query,
            about_id=about.id if about else None,
            k=args.limit,
            relevance=relevance,
            decay=memory.per_tick_decay(sim.world.rules, sim.world.calendar),
        )
        ms = (time.perf_counter() - started) * 1000
        console.print(
            f"[bold]{agent.name}[/], asked [italic]{args.query!r}[/]"
            + (f" while facing {about.name}" if about else "")
            + f", recalls ({how}, {ms:.0f} ms, from {len(agent.stream)} memories):"
        )
        for m in found:
            console.print(f"  [dim]{clock(m.tick, start)}[/]  [{m.importance}] {m.text}")
        return

    rows = store.memories(agent_id, kind=args.kind, include_forgotten=args.all, limit=args.limit)
    store.close()
    if not rows:
        console.print("No memories.")
        return
    table = Table("When", "Imp", "Kind", "Memory", box=None)
    for r in reversed(rows):
        text = r["text"]
        if r["forgotten_tick"] is not None:
            text = f"[dim strike]{text}[/] [dim](forgotten {clock(r['forgotten_tick'], start)})[/]"
        elif r["kind"] == "belief":
            text = f"[bold]{text}[/]"
        table.add_row(clock(r["tick"], start), str(r["importance"]), r["kind"], text)
    console.print(Panel(table, title=f"Memories of {args.agent} (oldest first)"))


def cmd_family(args):
    """Family tree: everyone's descendants from the founders (or one agent's line)."""
    store = open_store(args.world)
    sim = store.load()
    store.close()
    by_id = {a.id: a for a in sim.population.agents}
    start = sim.world.calendar

    def label(a):
        state = (
            f"[green]{a.stage}[/]"
            if a.alive
            else f"[dim]† {a.cause_of_death}, {start.when(a.died_tick)}[/]"
        )
        partner = ""
        if a.partner is not None and a.partner in by_id:
            partner = f" ♥ {by_id[a.partner].name}"
        expecting = " [magenta](expecting)[/]" if a.pregnant else ""
        return f"[bold]{a.name}[/] [dim]gen {a.generation}[/] {state}{partner}{expecting}"

    def grow(node, a, depth=0):
        for child_id in a.children:
            child = by_id.get(child_id)
            if child is None or child.parents[0] != a.id:
                continue  # each child is listed once, under their first parent
            other = by_id.get(child.parents[1]) if len(child.parents) > 1 else None
            with_ = f" [dim](with {other.name})[/]" if other else ""
            grow(node.add(label(child) + with_), child, depth + 1)

    if args.agent:
        root = sim.population.get_agent(args.agent)
        if root is None:
            raise SystemExit(f"No agent named {args.agent}")
        ancestors = []
        frontier = [root]
        while frontier:
            nxt = []
            for a in frontier:
                for p in a.parents:
                    if p in by_id:
                        ancestors.append(by_id[p])
                        nxt.append(by_id[p])
            frontier = nxt
        if ancestors:
            console.print(
                "Ancestors: " + ", ".join(f"{a.name} (gen {a.generation})" for a in ancestors)
            )
        tree = Tree(label(root))
        for child_id in root.children:
            child = by_id[child_id]
            other = [by_id[p].name for p in child.parents if p != root.id and p in by_id]
            grow(tree.add(label(child) + (f" [dim](with {other[0]})[/]" if other else "")), child)
        console.print(tree)
        return

    tree = Tree(f"[bold]{sim.config.name}[/]: family tree")
    for founder in [a for a in sim.population.agents if a.generation == 0]:
        node = tree.add(label(founder))
        grow(node, founder)
        for c in founder.children:
            child = by_id[c]
            if child.parents[0] != founder.id:
                first = by_id[child.parents[0]].name
                node.add(f"[dim]also parent of {child.name} (listed under {first})[/]")
    console.print(tree)
    gens = {}
    for a in sim.population.agents:
        gens.setdefault(a.generation, [0, 0])[0 if a.alive else 1] += 1
    console.print(
        "Generations: "
        + ", ".join(
            f"gen {g}: {alive} alive, {dead} dead" for g, (alive, dead) in sorted(gens.items())
        )
    )


def cmd_dashboard(args):
    from genesis.dashboard.server import serve

    path = world_path(args.world)
    if not path.exists():
        raise SystemExit(f"No world at {path}. Create one with: genesis new --name {args.world}")
    serve(path, port=args.port, open_browser=not args.no_browser)


def cmd_experiment(args):
    from genesis.config import ConfigError
    from genesis.simulation import experiment

    try:
        seeds = experiment.parse_seeds(args.seeds)
        arms = [experiment.parse_arm(a) for a in args.arm]
        report = experiment.run(
            args.config,
            seeds,
            args.years,
            arms,
            llm=args.llm,
            workers=args.workers,
            out=args.out,
        )
    except (ConfigError, ValueError, TypeError) as e:
        raise SystemExit(f"Experiment error: {e}") from e
    print()
    print(f"{'arm':>20}  alive  peak  ideas  currency  died out")
    for name, a in report["by_arm"].items():
        print(
            f"{name:>20}  {a['alive']:>5}  {a['peak']:>4}  {a['ideas']:>5}  "
            f"{a['with_currency']:>3}/{a['runs']:<4}  {a['died_out']:>3}"
        )
    if args.out:
        print(f"\nFull results: {args.out}")


def cmd_start(args):
    from genesis.simulation import service

    path = world_path(args.world)
    if not path.exists():
        raise SystemExit(f"No world at {path}. Create one with: genesis new")
    store = open_store(args.world)
    sim = store.load()
    store.close()
    cal = sim.world.calendar
    days = args.days if args.days is not None else round(args.years * cal.days_per_year)
    target = sim.tick_count + cal.ticks(days=days)
    service.control(path, "run")
    if args.set_only:  # just the target, for `genesis service install` to run it
        service._write_status(
            path,
            world=path.stem,
            state="stopped",
            target_tick=target,
            target=cal.clock(target),
            tick=sim.tick_count,
            clock=cal.clock(sim.tick_count),
        )
        console.print(f"{path.stem}: target set to {cal.clock(target)}.")
        return
    _long_run(args, path, target)


def cmd_resume(args):
    from genesis.simulation import service

    path = world_path(args.world)
    status = service.read_status(path)
    if not status.get("target_tick"):
        raise SystemExit(
            f"{args.world} has no run to resume. Start one: genesis start {args.world} --years N"
        )
    if status.get("state") == service.FINISHED:
        console.print(f"{args.world} already reached its target ({status.get('target')}).")
        return
    _long_run(args, path, None)


def _long_run(args, path, target):
    from genesis.simulation import service

    llm = False if args.no_llm else None
    echo = (lambda *_: None) if args.quiet else console.print
    try:
        reason = service.run(
            path, target, llm=llm, snapshot_minutes=args.snapshot_minutes, echo=echo
        )
    except service.WorldBusy as e:
        raise SystemExit(str(e)) from e
    status = service.read_status(path)
    console.print(f"{path.stem}: {reason} at {status.get('clock')} ({status.get('alive')} alive).")


def cmd_control(args):
    from genesis.simulation import service

    path = world_path(args.world)
    service.control(path, {"pause": "pause", "continue": "run", "stop": "stop"}[args.command])
    console.print(f"Asked {args.world} to {args.command} (at its next daily save).")


def cmd_runs(_args):
    from genesis.simulation import service

    folder = service.run_dir(data_dir() / "x.sqlite")
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    if not files:
        console.print("No long runs yet. Start one: genesis start WORLD --years N")
    for f in files:
        st = json.loads(f.read_text())
        eta = (
            f", about {st['eta_hours']} h to go"
            if st.get("eta_hours") and st.get("state") == "running"
            else ""
        )
        console.print(
            f"[bold]{f.stem}[/] {st.get('state')}: {st.get('clock')} -> {st.get('target')}; "
            f"{st.get('alive')} alive; {st.get('ticks_per_second', '-')} watches/s{eta} "
            f"[dim](updated {st.get('updated')})[/]"
        )


def cmd_service(args):
    import shutil
    import subprocess

    from genesis.simulation import service

    unit = service.unit_path(args.world)
    name = unit.name
    if args.action == "install":
        if args.world != "pilots" and not service.read_status(world_path(args.world)).get(
            "target_tick"
        ):
            raise SystemExit(
                f"Set a target first: genesis start {args.world} --years N (then stop it)"
            )
        uv = shutil.which("uv") or "uv"
        project = Path(__file__).resolve().parents[2]
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(service.unit_text(args.world, project, uv))
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", name], check=False)
        console.print(
            f"Installed and started {name}. It restarts after a crash and starts when you log "
            f"in; to start at boot without logging in: [bold]loginctl enable-linger $USER[/]. "
            f"Stop it: genesis service stop {args.world}"
        )
    elif args.action == "remove":
        subprocess.run(["systemctl", "--user", "disable", "--now", name], check=False)
        unit.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        console.print(f"Removed {name}.")
    elif args.action == "stop":
        subprocess.run(["systemctl", "--user", "stop", name], check=False)
    else:
        subprocess.run(["systemctl", "--user", "status", name, "--no-pager"], check=False)


def cmd_god(args):
    """God Mode (M9): queue a command for a running world, or apply it to a stopped one."""
    from genesis.god import interventions as god
    from genesis.simulation import service

    path = world_path(args.world)
    if not path.exists():
        raise SystemExit(f"No world at {path}.")
    command = {"act": args.act}
    a = args.args
    try:
        match args.act:
            case "add_food":
                command.update(x=int(a[0]), y=int(a[1]), regen=int(a[2]) if len(a) > 2 else 3)
            case "remove_food":
                command.update(food_id=int(a[0]))
            case "lake" | "dry":
                command.update(x=int(a[0]), y=int(a[1]), size=int(a[2]) if len(a) > 2 else 3)
            case "disaster":
                command.update(kind=a[0], region=a[1] if len(a) > 1 else "all")
                if len(a) > 2:
                    command["days"] = int(a[2])
            case "migrants":
                command.update(count=int(a[0]), region=a[1] if len(a) > 1 else None)
            case "strike":
                command.update(agent_id=int(a[0]))
            case "whisper":
                command.update(agent_id=int(a[0]), text=" ".join(a[1:]))
            case _:
                raise SystemExit(f"Commands: {', '.join(god.COMMANDS)}")
    except (IndexError, ValueError) as e:
        raise SystemExit(f"Bad arguments for {args.act}: {e}. See: genesis god --help") from e

    status = service.read_status(path)
    if status.get("state") in ("running", "paused") and status.get("pid"):
        store = WorldStore(path)
        qid = god.enqueue(store.conn, command)
        store.close()
        console.print(f"Queued as God command {qid}: applied at the run's next daily save.")
        return
    store = WorldStore(path)
    sim = store.load()
    try:
        event = god.apply(sim, command)
    except god.GodError as e:
        store.close()
        raise SystemExit(f"Not done: {e}") from e
    store.save(sim)
    store.close()
    from genesis.history.describe import describe

    e = next(x for x in sim.log.recent if x.id == event) if sim.log.recent else None
    names = {x.id: x.name for x in sim.population.agents}
    console.print(f"Done (event {event}): " + (describe(e, names) if e else args.act))


def cmd_branch(args):
    from genesis.god.compare import branch

    source = world_path(args.world)
    target = world_path(args.new)
    if not source.exists():
        raise SystemExit(f"No world at {source}.")
    try:
        branch(source, target, args.new)
    except FileExistsError as e:
        raise SystemExit(str(e)) from e
    console.print(
        f"{args.new} is a copy of {args.world} as last saved. Change it with "
        f"[bold]genesis god {args.new} ...[/], then run both and [bold]genesis compare "
        f"{args.world} {args.new}[/]."
    )


def cmd_compare(args):
    from genesis.god.compare import ROWS, summary

    worlds = [summary(world_path(w)) for w in (args.a, args.b)]
    table = Table("", *[w["world"] for w in worlds], "Difference")
    for key, label in ROWS:
        values = [w[key] for w in worlds]
        diff = ""
        if all(isinstance(v, int | float) for v in values):
            d = values[1] - values[0]
            diff = f"{d:+.3f}" if isinstance(d, float) else f"{d:+d}"
        table.add_row(label, *[str(v) for v in values], diff)
    for w in worlds:
        if w["branched_from"]:
            b = w["branched_from"]
            table.caption = f"{w['world']} branched from {b['world']} at tick {b['tick']}"
    console.print(table)
    console.print(
        "Deaths by cause: "
        + " | ".join(f"{w['world']}: {w['deaths_by_cause'] or '-'}" for w in worlds)
    )
    console.print(
        "Currencies: " + " | ".join(f"{w['world']}: {w['currencies'] or '-'}" for w in worlds)
    )


def cmd_check(args):
    from genesis.history.check import check

    path = world_path(args.world)
    if not path.exists():
        raise SystemExit(f"No world at {path}.")
    info, rows = check(path)
    marks = {"ok": "✅", "weak": "⚠️ ", "no": "❌", "wait": "⏳", "n/a": "–"}
    print(
        f"{info['world']} (seed {info['seed']}): day {info['days']} (year {info['years']}), "
        f"{info['alive']} alive, LLM {'on' if info['llm'] else 'off'}\n"
    )
    for r in rows:
        print(f"{marks.get(r.mark, r.mark)} {r.requirement}")
        print(f"     {r.shows}")


def cmd_worlds(_args):
    files = sorted(data_dir().glob("*.sqlite"))
    if not files:
        console.print(f"No worlds in {data_dir()}. Create one with: genesis new")
        return
    table = Table("World", "Tick", "Saved", "File")
    for f in files:
        s = WorldStore(f)
        meta = s.meta()
        s.close()
        table.add_row(f.stem, meta.get("tick", "?"), meta.get("saved_at", "?"), str(f))
    console.print(table)


# =================================================================
# Live view
# =================================================================


def _live_view(sim: Simulation, names: dict[int, str]):
    agents = Table(
        title=f"{sim.config.name} | {clock(sim.tick_count, sim.world.calendar)} "
        f"| tick {sim.tick_count}",
        expand=True,
    )
    for col in ("Name", "State", "Age", "Pos", "Health", "Hunger", "Thirst", "Energy", "Doing"):
        agents.add_column(col)

    for a in sim.population.agents:
        state = f"[green]{a.stage}[/]" if a.alive else f"[red]dead ({a.cause_of_death})[/]"
        age_at = a.died_tick if a.died_tick is not None else sim.tick_count
        agents.add_row(
            a.name,
            state,
            f"{sim.world.calendar.age_days(a.age(age_at))}d",
            f"{a.position.x},{a.position.y}",
            _bar(a.health, good_high=True),
            _bar(a.hunger),
            _bar(a.thirst),
            _bar(a.energy, good_high=True),
            a.current_action.value if a.current_action else "-",
        )

    feed = Table.grid()
    for e in sim.log.recent[-12:]:
        feed.add_row(f"[dim]#{e.id} {clock(e.tick, sim.world.calendar)}[/]  {describe(e, names)}")

    return Group(agents, Panel(feed, title="Recent events"))


def _bar(value: float, good_high: bool = False) -> str:
    bad = value < 30 if good_high else value > 70
    warn = value < 60 if good_high else value > 50
    color = "red" if bad else "yellow" if warn else "green"
    return f"[{color}]{value:3.0f}[/]"


# =================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="genesis", description="Run and explore Genesis worlds.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="create a world file from a config")
    p.add_argument("--config", help="TOML config (default: built-in eden)")
    p.add_argument("--name", help="override world.name (also the file name)")
    p.add_argument("--seed", type=int)
    p.add_argument("--founders", type=int)
    p.add_argument("--force", action="store_true", help="replace an existing world file")
    p.add_argument("--llm", action="store_true", help="turn the local LLM mind on (Ollama)")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("run", help="run a world forward (saved after every tick)")
    p.add_argument("world")
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--ticks", type=int, help="run this many hours instead of --days")
    p.add_argument("--delay", type=float, default=0.0, help="seconds to pause per tick (to watch)")
    p.add_argument("--quiet", action="store_true", help="no live view")
    p.add_argument("--no-llm", action="store_true", help="rules only for this run")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="show the current state")
    p.add_argument("world")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("inspect", help="everything about one agent")
    p.add_argument("world")
    p.add_argument("agent")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("events", help="list events")
    p.add_argument("world")
    p.add_argument("--agent")
    p.add_argument("--type")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("why", help="show the chain of causes behind an event")
    p.add_argument("world")
    p.add_argument("event_id", type=int)
    p.set_defaults(func=cmd_why)

    p = sub.add_parser("conversations", help="every message one agent sent or received")
    p.add_argument("world")
    p.add_argument("agent")
    p.add_argument("other", nargs="?", help="only messages with this agent")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_conversations)

    p = sub.add_parser("relations", help="who sees whom as friend, rival, ...")
    p.add_argument("world")
    p.add_argument("--all", action="store_true", help="include the dead")
    p.set_defaults(func=cmd_relations)

    p = sub.add_parser("metrics", help="daily society numbers (population, deaths, genes)")
    p.add_argument("world")
    p.add_argument("--name", action="append", help="metric to show (repeatable)")
    p.add_argument("--genes", action="store_true", help="average of every gene")
    p.add_argument("--every", type=int, default=1, help="show every Nth day")
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("view", help="watch the world live in your browser (pixel-art viewer)")
    p.add_argument("world")
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--speed", type=float, default=1, help="ticks per second: 0.5 1 2 5 20, 0=max")
    p.add_argument("--paused", action="store_true", help="start paused")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    p.add_argument("--no-llm", action="store_true", help="rules only for this run")
    p.set_defaults(func=cmd_view)

    p = sub.add_parser("thoughts", help="LLM calls: what agents chose, said and thought")
    p.add_argument("world")
    p.add_argument("agent", nargs="?")
    p.add_argument("--kind", help="greet, turn, reply, ask, request, info or threat")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_thoughts)

    p = sub.add_parser("family", help="family tree (everyone, or one agent's line)")
    p.add_argument("world")
    p.add_argument("agent", nargs="?")
    p.set_defaults(func=cmd_family)

    p = sub.add_parser("memories", help="an agent's memories, or what a question brings to mind")
    p.add_argument("world")
    p.add_argument("agent")
    p.add_argument("--query", help="what comes to mind for this (e.g. 'Dax asks for water')")
    p.add_argument("--about", help="...while facing this person")
    p.add_argument("--kind", help="observation, conversation, told, belief or summary")
    p.add_argument("--all", action="store_true", help="include forgotten memories")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_memories)

    p = sub.add_parser("replay", help="re-run from tick 0 with recorded LLM answers; compare")
    p.add_argument("world")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("bench-llm", help="compare local models on real decision prompts")
    p.add_argument("--models", default="llama3,llama3.2:3b")
    p.add_argument("--situations", type=int, default=50)
    p.add_argument("--samples", type=int, default=8, help="answers to print per model")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--workers", type=int, default=4, help="also measure N requests at once")
    p.add_argument(
        "--provider",
        choices=["ollama", "openai"],
        default="ollama",
        help="openai: vLLM, llama.cpp llama-server, SGLang, LM Studio",
    )
    p.add_argument("--url", default=None, help="the model server (default: the provider's)")
    p.set_defaults(func=cmd_bench_llm)

    p = sub.add_parser("dashboard", help="observer dashboard: conversations, language, agents")
    p.add_argument("world")
    p.add_argument("--port", type=int, default=8810)
    p.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser(
        "experiment", help="run a config over many seeds and settings, and compare (M8e)"
    )
    p.add_argument("config", help="TOML config, e.g. worlds/millennium.toml")
    p.add_argument("--seeds", default="1-4", help="e.g. 1-8 or 1,3,7")
    p.add_argument("--years", type=int, default=10)
    p.add_argument(
        "--arm",
        action="append",
        default=[],
        help='another setting to compare, e.g. "no trade: trade=0" (repeatable)',
    )
    p.add_argument("--llm", action="store_true", help="base arm with the LLM (+ a rules control)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out", default="experiment.json", help="where to write the results")
    p.set_defaults(func=cmd_experiment)

    for name, helptext in (
        ("start", "run a world towards a target, safely (saves, journal, snapshots) (M8.5)"),
        ("resume", "continue a started run after any stop, crash or reboot (M8.5)"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("world")
        if name == "start":
            g = p.add_mutually_exclusive_group(required=True)
            g.add_argument("--years", type=float)
            g.add_argument("--days", type=int)
        p.add_argument("--no-llm", action="store_true")
        p.add_argument("--quiet", action="store_true")
        if name == "start":
            p.add_argument("--set-only", action="store_true", help="only set the target")
        p.add_argument("--snapshot-minutes", type=float, default=60)
        p.set_defaults(func=cmd_start if name == "start" else cmd_resume)
    for name in ("pause", "continue", "stop"):
        p = sub.add_parser(name, help=f"{name} a running world (at its next daily save)")
        p.add_argument("world")
        p.set_defaults(func=cmd_control, command=name)
    p = sub.add_parser("runs", help="what every long run is doing")
    p.set_defaults(func=cmd_runs)
    p = sub.add_parser("service", help="systemd service for a world: install|remove|status|stop")
    p.add_argument("action", choices=["install", "remove", "status", "stop"])
    p.add_argument("world")
    p.set_defaults(func=cmd_service)

    p = sub.add_parser(
        "god",
        help="God Mode (M9): change a world (queued if it is running)",
        description=(
            "add_food X Y [REGEN] | remove_food ID | lake X Y [SIZE] | dry X Y [SIZE] | "
            "disaster KIND [VALLEY|all] [DAYS] (drought, winter/weather, flood, sickness/plague, "
            "predators/beasts, wildfire/fire) | migrants N [VALLEY] | strike AGENT | "
            "whisper AGENT TEXT..."
        ),
    )
    p.add_argument("world")
    p.add_argument("act")
    p.add_argument("args", nargs="*")
    p.set_defaults(func=cmd_god)
    p = sub.add_parser("branch", help="copy a world under a new name, to change and compare (M9)")
    p.add_argument("world")
    p.add_argument("new")
    p.set_defaults(func=cmd_branch)
    p = sub.add_parser("compare", help="two worlds side by side (M9)")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("check", help="does a world behave as designed? (M8f)")
    p.add_argument("world")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("worlds", help="list world files")
    p.set_defaults(func=cmd_worlds)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
