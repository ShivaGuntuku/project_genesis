"""M5 demo: an agent refuses a request and explains it with a memory from 20 days earlier.

Staged: only the old event (day 1: Sarah threatens John and takes his water).
Real: the 20 days in between (rules), the memories, the retrieval, and John's decision,
which the local model (llama3 + nomic-embed-text) makes from his persona and memories.

    uv run python tools/demos/m5_grudge.py [--seed 5] [--tries 2]
"""

import argparse

from genesis.config import WorldConfig
from genesis.llm.mind import attach
from genesis.llm.settings import LLMConfig
from genesis.simulation.context import TickContext
from genesis.simulation.engine import Simulation
from genesis.society import interaction
from genesis.world.world import Position


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--tries", type=int, default=2)  # 2 calls each; 4 per hour budget
    args = ap.parse_args()

    config = WorldConfig.default().with_overrides(seed=args.seed, founders=2)
    config = config.with_overrides(llm=LLMConfig(enabled=True))
    sim = Simulation(config)
    john, sarah = sim.population.agents

    def ctx(mind=None):
        return TickContext(
            world=sim.world,
            tick=sim.tick_count,
            log=sim.log,
            rng=sim.rng,
            agents={a.id: a for a in sim.population.agents},
            mind=mind,
        )

    # Day 1 (staged): Sarah threatens John and takes his water.
    sim.run(4)
    sarah.position = john.position
    john.inventory["water"] = 2
    first = sim.log.next_id
    c = ctx()
    threat = interaction.say(c, sarah, john, interaction.Act.THREATEN, {"demand": "water"})
    interaction.feel(c, john, sarah, "threatened", threat)
    interaction.give(c, john, sarah, "water", reply_to=threat, under_threat=True)
    sim._remember(first)

    # 20 ordinary days (rules only).
    sim.run(20 * 24)
    if not (john.alive and sarah.alive):
        raise SystemExit("Someone died in the 20 days; try another --seed.")

    # Day 21: thirsty Sarah asks John for water. John decides with the real model.
    print(attach(sim))
    sarah.position = Position(john.position.x, john.position.y)
    sarah.thirst = 80
    john.inventory["water"] = 2
    for i in range(args.tries):
        c = ctx(sim.mind)
        before = len(c.llm_calls)
        interaction.request_item(c, sarah, john, "water")
        for call in c.llm_calls[before:]:
            if call["kind"] != "request":
                continue
            block = call["prompt"].split("What comes to mind:")[1].split("\n\n")[0]
            if i == 0:
                print("\nWhat comes to John's mind:" + block)
            print(f"\nTry {i + 1}: John chooses {call['choice']!r}")
            print(f'  says:   "{call["say"]}"')
            print(f"  thinks: {call['thought']}")
        john.inventory["water"] = 2  # reset for the next try
        sarah.inventory["water"] = 0


if __name__ == "__main__":
    main()
