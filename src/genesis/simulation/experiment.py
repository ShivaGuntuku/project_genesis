"""Experiment runner (M8e): the same world over N seeds, under several settings ("arms").

    genesis experiment worlds/millennium.toml --seeds 1-4 --years 25 \\
        --arm "no trade: trade=0" --arm "no inventions: inventions=0"

Every arm runs every seed, rules-only and in parallel. The base settings are always an arm
("base"). With --llm the base arm runs with the LLM mind and a rules-only control of the same
settings is added (the main comparison of the Millennium Experiment, docs/10 and docs/15).
Results go to a JSON file: per seed and arm, a yearly series and an end-of-run summary.
"""

import json
import time
from collections import Counter
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

from genesis.config import WorldConfig
from genesis.history import events as ev


def parse_seeds(text: str) -> list[int]:
    """'1-4' or '1,3,7' or '5'."""
    out = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        out.extend(range(int(a), int(b) + 1) if b else [int(a)])
    return out


def parse_arm(text: str) -> tuple[str, dict]:
    """'no trade: trade=0, goals=0' -> ('no trade', {'trade': 0, 'goals': 0})."""
    name, _, rest = text.partition(":")
    settings = {}
    for part in filter(None, (p.strip() for p in rest.split(","))):
        key, _, value = part.partition("=")
        value = value.strip()
        try:
            settings[key.strip()] = int(value)
        except ValueError:
            settings[key.strip()] = float(value)
    return name.strip(), settings


def run_one(job: dict) -> dict:
    """One seed of one arm (runs in its own process)."""
    from genesis.agents import talents
    from genesis.simulation.engine import Simulation

    config = WorldConfig.from_toml(job["toml"]).with_overrides(seed=job["seed"])
    config = config.with_overrides(rules=replace(config.rules, **job["rules"]))
    llm = job["llm"]
    config = config.with_overrides(llm=replace(config.llm, enabled=bool(llm)))
    sim = Simulation(config)
    if llm:
        from genesis.llm.mind import attach

        attach(sim)
    started = time.time()
    year_ticks = sim.world.calendar.ticks(days=sim.world.calendar.days_per_year)
    years, counts_before = [], {}
    for year in range(1, job["years"] + 1):
        sim.run(year_ticks)
        sim.log.pending.clear()  # counts are kept in log.counts; free the memory
        sim.pending_metrics.clear()
        sim.pending_yearly.clear()
        sim.pending_memories.clear()
        sim.pending_relationship_history.clear()
        sim.pending_llm_calls.clear()
        alive = sim.population.get_alive()
        counts = dict(sim.log.counts)
        delta = {k: counts.get(k, 0) - counts_before.get(k, 0) for k in counts}
        counts_before = counts
        by_valley = Counter(sim.world.region_at.get(a.position, "valley") for a in alive)
        years.append(
            {
                "year": year,
                "alive": len(alive),
                "by_valley": dict(sorted(by_valley.items())),
                "births": delta.get(ev.BIRTH, 0),
                "deaths": delta.get(ev.DIED, 0),
                "invented": delta.get(ev.INVENTED, 0),
                "learned": delta.get(ev.LEARNED, 0),
                "trades": delta.get("message.trade", 0),
                "disasters": sum(
                    1 for d in sim.world.disasters if (year - 1) * 360 <= d.day < year * 360
                ),
                "migrated": delta.get(ev.MIGRATED, 0),
                "goals_reached": delta.get(ev.GOAL_REACHED, 0),
                "fights": delta.get(ev.FIGHT, 0),
            }
        )
        if not alive:
            break
    alive = sim.population.get_alive()
    causes = Counter(a.cause_of_death for a in sim.population.agents if not a.alive)
    valley_ideas: dict[str, set] = {}
    for a in alive:
        valley = sim.world.region_at.get(a.position, "valley")
        valley_ideas.setdefault(valley, set()).update(a.knowledge)
    names = {c.id: c.name for c in sim.world.concepts.values()}
    return {
        "arm": job["arm"],
        "seed": job["seed"],
        "llm": bool(llm),
        "seconds": round(time.time() - started, 1),
        "years": years,
        "summary": {
            "alive": len(alive),
            "peak": sim.world.peak_population,
            "deaths_by_cause": dict(causes),
            "concepts": len(sim.world.concepts),
            "lost": sum(1 for c in sim.world.concepts.values() if c.lost_tick is not None),
            "currencies": [
                {k: c[k] for k in ("name", "good", "region")}
                for c in sim.world.economy["currencies"]
            ],
            "ideas_by_valley": {
                k: sorted(names[i] for i in v if i in names) for k, v in valley_ideas.items()
            },
            "professions": dict(
                Counter(talents.profession(a) for a in alive if sim.world.rules.talents)
            ),
            "disasters": [d.to_dict() for d in sim.world.disasters if d.day < len(years) * 360],
        },
    }


def run(config_path, seeds, years, arms, llm=False, workers=4, out=None, echo=print) -> dict:
    toml = Path(config_path).read_text()
    WorldConfig.from_toml(toml)  # fail early on a bad config
    plan = [("base", {}, llm)]
    if llm:
        plan.append(("rules-only control", {}, False))
    plan += [(name, settings, False) for name, settings in arms]
    jobs = [
        {"toml": toml, "seed": s, "years": years, "arm": name, "rules": rules, "llm": use_llm}
        for name, rules, use_llm in plan
        for s in seeds
    ]
    results = []
    # With the LLM, one process at a time (one local model); otherwise in parallel.
    with Pool(1 if llm else max(1, workers)) as pool:
        for r in pool.imap(run_one, jobs):
            s = r["summary"]
            echo(
                f"{r['arm']:>20} seed {r['seed']:>3}: {s['alive']:>3} alive (peak {s['peak']}), "
                f"{s['concepts']} ideas, {len(s['currencies'])} currencies, {r['seconds']}s"
            )
            results.append(r)
    report = {
        "config": str(config_path),
        "seeds": seeds,
        "years": years,
        "arms": [{"name": n, "rules": r, "llm": bool(u)} for n, r, u in plan],
        "runs": results,
        "by_arm": _by_arm(results),
    }
    if out:
        Path(out).write_text(json.dumps(report, indent=1))
    return report


def _by_arm(results) -> dict:
    """Averages over seeds, per arm (the numbers to compare)."""
    out = {}
    for arm in dict.fromkeys(r["arm"] for r in results):
        runs = [r for r in results if r["arm"] == arm]
        n = len(runs)
        out[arm] = {
            "runs": n,
            "alive": round(sum(r["summary"]["alive"] for r in runs) / n, 2),
            "peak": round(sum(r["summary"]["peak"] for r in runs) / n, 2),
            "ideas": round(sum(r["summary"]["concepts"] for r in runs) / n, 2),
            "with_currency": sum(1 for r in runs if r["summary"]["currencies"]),
            "died_out": sum(1 for r in runs if r["summary"]["alive"] == 0),
        }
    return out
