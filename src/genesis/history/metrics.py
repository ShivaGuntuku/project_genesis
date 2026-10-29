"""Daily numbers about the society (population, deaths, genes, skills).

Recorded at the end of every simulated day; the basis for evolution charts later.
"""

from statistics import fmean, pstdev

from genesis.agents.abilities import SKILLS
from genesis.agents.genome import GENES

TICKS_PER_DAY = 24
DEATH_CAUSES = ("dehydration", "starvation", "old age")


def daily_metrics(sim) -> list[tuple[int, str, float]]:
    """Metrics for the day that just ended, as (tick, name, value) rows."""
    tick = sim.tick_count
    tpd = sim.world.calendar.ticks_per_day
    day_start = tick - tpd
    agents = sim.population.agents
    alive = [a for a in agents if a.alive]

    rows = {
        "population.alive": len(alive),
        "population.ever": len(agents),
        "births": sum(1 for a in agents if day_start <= a.born_tick < tick),
    }

    for cause in DEATH_CAUSES:
        rows[f"deaths.{cause}"] = sum(
            1
            for a in agents
            if not a.alive and a.cause_of_death == cause and day_start <= a.died_tick < tick
        )

    if alive:
        rows["age.mean_days"] = fmean(a.age(tick) for a in alive) / tpd
        for gene in GENES:
            values = [getattr(a.genome, gene) for a in alive]
            rows[f"gene.{gene}.mean"] = fmean(values)
            rows[f"gene.{gene}.sd"] = pstdev(values)
        for skill in SKILLS:
            rows[f"skill.{skill}.mean"] = fmean(a.abilities.get(skill) for a in alive)

    # Society: directed relationship labels among the living.
    alive_ids = {a.id for a in alive}
    labels = [
        rel.label()
        for a in alive
        for target_id, rel in a.relationships.items()
        if target_id in alive_ids
    ]
    rows["social.friend_links"] = sum(lbl in ("friend", "close friend") for lbl in labels)
    rows["social.rival_links"] = labels.count("rival")
    rows["social.enemy_links"] = labels.count("enemy")

    # What happened today, from the event counters.
    now, start = sim.log.counts, sim.counts_at_day_start
    for key in sorted(k for k in now if k.startswith("message.")) + [
        "lie_discovered",
        "relation_changed",
        "fight",
        "joined_group",
        "left_group",
        "trespass",
        "experimented",
        "invented",
        "made",
        "learned",
        "goal_set",
        "goal_reached",
        "goal_abandoned",
        "promise_broken",
        "fell_sick",
        "beast_attack",
        "migrated",
    ]:
        rows[f"today.{key}"] = now.get(key, 0) - start.get(key, 0)

    rows.update(society(sim))

    return [(tick, name, round(value, 6)) for name, value in rows.items()]


def friend_graph(sim):
    """Living agents, joined where either sees the other as a friend (weight: affinity)."""
    import networkx as nx

    graph = nx.Graph()
    alive = {a.id: a for a in sim.population.get_alive()}
    graph.add_nodes_from(sorted(alive))
    for a in alive.values():
        for other_id, rel in sorted(a.relationships.items()):
            if other_id in alive and rel.label() in ("friend", "close friend"):
                weight = max(rel.affinity, graph.get_edge_data(a.id, other_id, {}).get("weight", 0))
                graph.add_edge(a.id, other_id, weight=weight)
    return graph


def society(sim) -> dict:
    """Networks and groups (M7): communities of friends, the most connected person, groups."""
    from networkx.algorithms import community

    rows = {}
    graph = friend_graph(sim)
    linked = [n for n in graph.nodes if graph.degree(n) > 0]
    if linked:
        sub = graph.subgraph(linked)
        groups_found = community.greedy_modularity_communities(sub, weight="weight")
        rows["social.communities"] = len(groups_found)
        centrality = _centrality(graph)
        top = max(sorted(centrality), key=lambda n: centrality[n])
        rows["social.most_connected_id"] = top
        rows["social.most_connected_score"] = centrality[top]
    else:
        rows["social.communities"] = 0
    rows["social.density"] = _density(graph)

    active = [g for g in sim.groups.values() if g.ended_tick is None]
    alive = len(sim.population.get_alive())
    in_groups = sum(len(g.members) for g in active)
    rows["groups.count"] = len(active)
    rows["groups.share_in_groups"] = in_groups / alive if alive else 0
    rows["groups.largest"] = max((len(g.members) for g in active), default=0)
    return rows


def _centrality(graph) -> dict:
    import networkx as nx

    return nx.degree_centrality(graph)


def _density(graph) -> float:
    import networkx as nx

    return nx.density(graph) if graph.number_of_nodes() > 1 else 0.0
