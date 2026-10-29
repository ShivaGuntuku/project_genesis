"""Kill a long run at random moments, resume it each time, and check the history is the same
as a run that never stopped (M8.5 check). With --llm (a stand-in model), the resumed world is
compared with a replay of every recorded answer from the start.

    uv run python scripts/kill_test.py --kills 100 --days 60
    uv run python scripts/kill_test.py --kills 20 --days 30 --llm
"""

import argparse
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from genesis.llm.mind import ReplayMind
from genesis.persistence.db import WorldStore


def genesis(*args, env=None):
    return [sys.executable, "-m", "genesis.cli", *args]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="worlds/two_valleys.toml")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--kills", type=int, default=20)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    root = Path(tempfile.mkdtemp(prefix="genesis-kill-"))
    env = {**os.environ, "GENESIS_DATA": str(root / "worlds")}
    if args.llm:
        env["GENESIS_FAKE_LLM"] = "1"
    (root / "worlds").mkdir()
    new = genesis("new", "--config", args.config, "--name", "k", "--force")
    if args.llm:
        new.append("--llm")
    subprocess.run(new, env=env, check=True, capture_output=True)
    path = root / "worlds" / "k.sqlite"
    shutil.copy(path, root / "start.sqlite")
    started = time.monotonic()
    proc = subprocess.Popen(
        genesis("start", "k", "--days", str(args.days), "--quiet", "--snapshot-minutes", "0.05"),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    kills = 0
    while True:
        try:
            proc.wait(timeout=rng.uniform(0.3, 2.5))
            break  # finished
        except subprocess.TimeoutExpired:
            if kills >= args.kills:
                continue
            proc.send_signal(signal.SIGKILL)
            proc.wait()
            kills += 1
            proc = subprocess.Popen(
                genesis("resume", "k", "--quiet", "--snapshot-minutes", "0.05"),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    store = WorldStore(path)
    resumed = store.load()
    records = store.llm_records()
    store.close()

    store = WorldStore(root / "start.sqlite")
    reference = store.load()
    store.close()
    if args.llm:
        reference.mind = ReplayMind(records, reference.config.llm)
    reference.run(resumed.tick_count - reference.tick_count)
    same = reference.state() == resumed.state()
    print(
        f"{kills} kills over {time.monotonic() - started:.0f}s; reached tick {resumed.tick_count}; "
        f"{'LLM answers replayed: ' + str(len(records)) + '; ' if args.llm else ''}"
        f"history {'IDENTICAL' if same else 'DIFFERENT'} to "
        f"{'a replay of the recorded answers' if args.llm else 'an uninterrupted run'}"
    )
    shutil.rmtree(root, ignore_errors=True)
    sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()
