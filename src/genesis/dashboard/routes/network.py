"""The social network (M7)."""

import json

from fastapi import FastAPI

from genesis.dashboard.common import (
    World,
    lineage_objects,
)
from genesis.society import family


def register(app: FastAPI, world: World) -> None:
    @app.get("/api/network")
    def network():
        """The living society as a graph: people, feelings, family, groups, communities."""
        import networkx as nx
        from networkx.algorithms import community

        store = world.open()
        conn = store.conn
        try:
            people = {}
            for r in conn.execute(
                "SELECT id, name, generation, stage, state_json FROM agents WHERE alive = 1"
            ):
                state = json.loads(r["state_json"])
                people[r["id"]] = {
                    "id": r["id"],
                    "name": r["name"],
                    "generation": r["generation"],
                    "stage": r["stage"],
                    "group": state.get("group_id"),
                    "partner": state.get("partner"),
                }
            group_names = (
                {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM groups")}
                if store._has_table("groups")
                else {}
            )
            lineage = lineage_objects(conn)
            edges, friend = [], nx.Graph()
            friend.add_nodes_from(people)
            seen = set()
            for r in conn.execute(
                "SELECT source_id, target_id, label, affinity FROM relationships"
            ):
                a, b = r["source_id"], r["target_id"]
                if a not in people or b not in people:
                    continue
                kin = family.relation(lineage[a], lineage[b], lineage)
                if kin in ("partner", "parent", "child", "sibling"):
                    kind = "partner" if kin == "partner" else "family"
                elif r["label"] in ("friend", "close friend"):
                    kind = "friend"
                    friend.add_edge(a, b)
                elif r["label"] in ("rival", "enemy"):
                    kind = "rival"
                else:
                    continue
                key = (min(a, b), max(a, b), kind)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"source": a, "target": b, "kind": kind, "label": r["label"]})
            centrality = nx.degree_centrality(friend) if len(friend) > 1 else {}
            communities = {}
            linked = [n for n in friend.nodes if friend.degree(n) > 0]
            if linked:
                found = community.greedy_modularity_communities(friend.subgraph(linked))
                for i, members in enumerate(found):
                    for m in members:
                        communities[m] = i + 1
            nodes = [
                {
                    **p,
                    "groupName": group_names.get(p["group"]),
                    "centrality": round(centrality.get(p["id"], 0.0), 3),
                    "community": communities.get(p["id"]),
                }
                for p in people.values()
            ]
            tick = int(store.meta()["tick"])
        finally:
            store.close()
        return {"nodes": nodes, "edges": edges, "clock": world.clock(tick)}
