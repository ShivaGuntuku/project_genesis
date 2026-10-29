"""The pilots of the Millennium Experiment (roadmap, after M8.5), one after another:

    P1  worlds/pilot_p1.toml   10 years, 10 people, LLM      real cost per call; bugs
    P2  worlds/pilot_p2.toml  100 years, 15 people, LLM      stable? ideas spread? leaks?
    P3  worlds/pilot_p3.toml  100 years, isolated valleys    do they diverge? (its rules-only
                                                             control runs as its own service)

Each world is created if missing, given its target, and run with `genesis.simulation.service`
(daily saves, LLM journal, snapshots). Stopped or crashed at any point, running this again
continues where it was; finished pilots are skipped. Meant to run as a systemd user service:

    uv run python scripts/pilots.py                 (or: genesis service for the unit)
"""

import subprocess
import sys
from pathlib import Path

from genesis.simulation import service

ROOT = Path(__file__).resolve().parents[1]
WORLDS = ROOT / "data" / "worlds"
PILOTS = [("pilot_p1", 10), ("pilot_p2", 100), ("pilot_p3", 100)]


def main():
    for name, years in PILOTS:
        path = WORLDS / f"{name}.sqlite"
        if not path.exists():
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "genesis.cli",
                    "new",
                    "--config",
                    str(ROOT / "worlds" / f"{name}.toml"),
                    "--llm",
                ],
                check=True,
                cwd=ROOT,
            )
        status = service.read_status(path)
        if status.get("state") in (service.FINISHED, service.EXTINCT):
            print(f"{name}: already {status['state']} ({status.get('clock')})", flush=True)
            continue
        target = None
        if not status.get("target_tick"):
            from genesis.persistence.db import WorldStore

            store = WorldStore(path)
            sim = store.load()
            store.close()
            cal = sim.world.calendar
            target = sim.tick_count + cal.ticks(days=years * cal.days_per_year)
        print(f"{name}: running towards year {years + 1}", flush=True)
        reason = service.run(path, target, llm=True, echo=lambda m: print(m, flush=True))
        print(f"{name}: {reason}", flush=True)
        if reason == service.STOPPED:
            sys.exit(0)  # asked to stop (CLI, dashboard or systemd): stay stopped
        if reason not in (service.FINISHED, service.EXTINCT):
            sys.exit(1)  # crashed: systemd restarts us, and we continue


if __name__ == "__main__":
    main()
