"""Long runs that look after themselves (M8.5). See docs/11.

    genesis start WORLD --years 100     run towards a target (saved once a simulated day)
    genesis resume WORLD                continue towards the target after any stop or crash
    genesis pause / continue / stop WORLD
    genesis service install WORLD       a systemd user service: starts at boot, restarts
                                        after a crash, stops cleanly (SIGTERM)

Safety:
- every save is one transaction; LLM answers are journaled the moment they arrive, so a
  resume after `kill -9` gives the same history as a run that never stopped;
- before running, the world file's integrity is checked; if it is damaged, the newest good
  snapshot is restored (the damaged file is kept beside it);
- a snapshot is taken every `snapshot_minutes` of real time (the newest `keep` are kept);
- a status file says what the run is doing (read by `genesis runs` and the dashboard), and a
  control file lets the dashboard or the CLI pause, continue or stop it.
"""

import json
import os
import signal
import sqlite3
import time
from pathlib import Path

RUNNING, PAUSED, FINISHED, STOPPED, CRASHED, EXTINCT = (
    "running",
    "paused",
    "finished",
    "stopped",
    "crashed",
    "everyone died",
)


def run_dir(world_file: Path) -> Path:
    return Path(world_file).resolve().parent.parent / "runs"


def status_file(world_file: Path) -> Path:
    return run_dir(world_file) / f"{Path(world_file).stem}.json"


def control_file(world_file: Path) -> Path:
    return run_dir(world_file) / f"{Path(world_file).stem}.control"


def snapshot_dir(world_file: Path) -> Path:
    return Path(world_file).resolve().parent.parent / "snapshots" / Path(world_file).stem


def read_status(world_file: Path) -> dict:
    """The run's status file; a run whose process is gone shows as stopped (it may have been
    killed): `resume` continues it exactly."""
    try:
        status = json.loads(status_file(world_file).read_text())
    except (OSError, ValueError):
        return {}
    if status.get("state") in (RUNNING, PAUSED) and not is_running(world_file):
        status["state"] = STOPPED
        status["note"] = "the process is gone (killed or crashed); genesis resume continues it"
    return status


# =================================================================
# One writer per world (M10 review): a lock held while a world runs
# =================================================================


class WorldBusy(RuntimeError):
    pass


def lock_file(world_file: Path) -> Path:
    return run_dir(world_file) / f"{Path(world_file).stem}.lock"


def is_running(world_file: Path) -> bool:
    """True while some process holds the world's run lock (it is freed even by kill -9)."""
    import fcntl

    path = lock_file(world_file)
    if not path.exists():
        return False
    with open(path, "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False


class run_lock:
    """Held by whatever simulates a world (long run, `genesis run`, the viewer). A second one
    is refused, so two processes never write the same world file."""

    def __init__(self, world_file: Path, who: str):
        self.path, self.who, self.file = lock_file(world_file), who, None

    def __enter__(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a+")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.seek(0)
            holder = self.file.read().strip() or "another process"
            self.file.close()
            raise WorldBusy(
                f"{self.path.stem} is already being run by {holder}. Watch it with "
                f"`genesis dashboard {self.path.stem}`, or stop it first "
                f"(`genesis stop {self.path.stem}`)."
            ) from None
        self.file.seek(0)
        self.file.truncate()
        self.file.write(f"{self.who} (pid {os.getpid()})")
        self.file.flush()
        return self

    def __exit__(self, *exc):
        import fcntl

        if self.file is not None:
            self.file.truncate(0)
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()


def _write_status(world_file: Path, **fields):
    path = status_file(world_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    status = {**read_status(world_file), **fields, "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=1))
    os.replace(tmp, path)  # atomic: readers never see half a file
    return status


def control(world_file: Path, command: str):
    """Ask a running world to pause, continue or stop (it reads this at each save)."""
    if command not in ("pause", "run", "stop", "snapshot"):
        raise ValueError(command)
    path = control_file(world_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(command)


def _read_control(world_file: Path) -> str:
    try:
        return control_file(world_file).read_text().strip()
    except OSError:
        return "run"


# =================================================================
# Integrity and snapshots
# =================================================================


def intact(world_file: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{world_file}?mode=ro", uri=True, timeout=30)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            conn.execute("SELECT value FROM meta WHERE key = 'tick'").fetchone()
        finally:
            conn.close()
        return row is not None and row[0] == "ok"
    except sqlite3.Error:
        return False


def snapshots(world_file: Path) -> list[Path]:
    folder = snapshot_dir(world_file)
    return (
        sorted(folder.glob("*.sqlite"), key=lambda p: p.stat().st_mtime) if folder.exists() else []
    )


def snapshot(store, world_file: Path, tick: int, keep: int = 5) -> Path:
    """A consistent copy of the world file (SQLite's online backup), newest `keep` kept."""
    folder = snapshot_dir(world_file)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"tick-{tick:09d}.sqlite"
    dest = sqlite3.connect(str(target))
    try:
        store.conn.backup(dest)
    finally:
        dest.close()
    for old in snapshots(world_file)[:-keep]:
        old.unlink(missing_ok=True)
    return target


def restore_latest(world_file: Path) -> Path | None:
    """Put the newest good snapshot in place of a damaged world file (kept as .damaged)."""
    for snap in reversed(snapshots(world_file)):
        if intact(snap):
            world_file = Path(world_file)
            if world_file.exists():
                os.replace(world_file, world_file.with_suffix(".damaged"))
            for extra in ("-wal", "-shm"):
                Path(str(world_file) + extra).unlink(missing_ok=True)
            dest = sqlite3.connect(str(world_file))
            src = sqlite3.connect(str(snap))
            try:
                src.backup(dest)
            finally:
                src.close()
                dest.close()
            return snap
    return None


# =================================================================
# The long run
# =================================================================


def run(
    world_file: Path,
    target_tick: int | None = None,
    llm: bool | None = None,
    snapshot_minutes: float = 60,
    keep: int = 5,
    max_ticks: int | None = None,
    echo=print,
) -> str:
    """Run a world towards its target, saving, journaling, snapshotting and obeying the
    control file. Returns why it stopped. Safe to kill at any moment."""
    world_file = Path(world_file)
    with run_lock(world_file, "a long run (genesis start/resume)"):
        return _run(world_file, target_tick, llm, snapshot_minutes, keep, max_ticks, echo)


def _run(world_file, target_tick, llm, snapshot_minutes, keep, max_ticks, echo) -> str:
    from genesis.llm import mind as llm_mind
    from genesis.persistence.db import WorldStore, attach_journal

    if not intact(world_file):
        restored = restore_latest(world_file)
        if restored is None:
            _write_status(world_file, state=CRASHED, error="damaged and no snapshot to restore")
            raise RuntimeError(f"{world_file} is damaged and there is no snapshot to restore")
        echo(f"The world file was damaged; restored the snapshot {restored.name}.")

    store = WorldStore(world_file)
    sim = store.load()
    echo(llm_mind.attach(sim, enabled=llm))
    replayed = attach_journal(sim, world_file, store)
    if replayed:
        echo(f"Resuming: {replayed} LLM answers from the journal are replayed.")
    status = read_status(world_file)
    if target_tick is None:
        target_tick = status.get("target_tick")
    if target_tick is None:
        raise ValueError("no target: start the run with a number of years or days first")
    cal = sim.world.calendar
    every = sim.config.save_every_ticks
    stop = {"reason": None}

    def request_stop(signum, _frame):
        stop["reason"] = STOPPED  # finish this tick, save, then stop

    import threading

    old = {}
    if threading.current_thread() is threading.main_thread():  # signals: main thread only
        old = {s: signal.signal(s, request_stop) for s in (signal.SIGINT, signal.SIGTERM)}
    from genesis.god import interventions as god

    for qid, event, error in god.apply_pending(sim, store):  # queued while it was stopped
        echo(f"God command {qid}: " + (error or f"done (event {event})"))
    run_id = store.start_run(sim.tick_count)
    started, start_tick = time.monotonic(), sim.tick_count
    last_snapshot = time.monotonic()
    if _read_control(world_file) == "stop":  # a stop asked of an earlier run is done
        control(world_file, "run")
    _write_status(
        world_file,
        state=RUNNING,
        world=world_file.stem,
        pid=os.getpid(),
        target_tick=target_tick,
        target=cal.clock(target_tick),
        tick=sim.tick_count,
        clock=cal.clock(sim.tick_count),
        alive=len(sim.population.get_alive()),
        llm=sim.mind is not None,
        resumed=replayed,
    )
    reason = CRASHED
    try:
        while True:
            if sim.tick_count >= target_tick:
                reason = FINISHED
                break
            if max_ticks is not None and sim.tick_count - start_tick >= max_ticks:
                reason = STOPPED
                break
            sim.tick()
            if sim.tick_count % every and not stop["reason"]:
                continue  # saved once a simulated day (or on a stop)
            from genesis.god import interventions as god  # M9: queued God commands

            for qid, event, error in god.apply_pending(sim, store):
                echo(f"God command {qid}: " + (error or f"done (event {event})"))
            store.save(sim)
            alive = len(sim.population.get_alive())
            spent = time.monotonic() - started
            rate = (sim.tick_count - start_tick) / spent if spent else 0
            _write_status(
                world_file,
                state=RUNNING,
                tick=sim.tick_count,
                clock=cal.clock(sim.tick_count),
                alive=alive,
                ticks_per_second=round(rate, 2),
                eta_hours=round((target_tick - sim.tick_count) / rate / 3600, 1) if rate else None,
            )
            if time.monotonic() - last_snapshot >= snapshot_minutes * 60:
                snapshot(store, world_file, sim.tick_count, keep)
                last_snapshot = time.monotonic()
            if stop["reason"]:
                reason = stop["reason"]
                break
            if not alive:
                reason = EXTINCT
                break
            command = _read_control(world_file)
            if command == "snapshot":  # M10: "snapshot now" from the dashboard
                snapshot(store, world_file, sim.tick_count, keep)
                control(world_file, "run")
                command = "run"
            if command == "stop":
                reason = STOPPED
                break
            if command == "pause":
                _write_status(world_file, state=PAUSED)
                while _read_control(world_file) == "pause" and not stop["reason"]:
                    time.sleep(1)
                if stop["reason"] or _read_control(world_file) == "stop":
                    reason = STOPPED
                    break
                _write_status(world_file, state=RUNNING)
        store.save(sim)
    finally:
        store.end_run(run_id, sim.tick_count, reason)
        if reason in (FINISHED, EXTINCT):
            snapshot(store, world_file, sim.tick_count, keep)
        store.close()
        journal = getattr(sim.mind, "journal", None)
        if journal is not None:
            journal.close()
        for s, handler in old.items():
            signal.signal(s, handler)
        _write_status(
            world_file,
            state=reason,
            tick=sim.tick_count,
            clock=cal.clock(sim.tick_count),
            alive=len(sim.population.get_alive()),
            pid=None,
        )
    return reason


# =================================================================
# systemd (Linux): start at boot, restart after a crash
# =================================================================

UNIT = """[Unit]
Description=Genesis world {world} (the Millennium Experiment)
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={project}
ExecStart={uv} run --project {project} genesis resume {world} --quiet
Restart=on-failure
RestartSec=30
KillSignal=SIGTERM
TimeoutStopSec=300
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def unit_path(world: str) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"genesis-{world}.service"


def unit_text(world: str, project: Path, uv: str) -> str:
    if world == "pilots":  # the pilot pipeline: P1, then P2, then P3 (scripts/pilots.py)
        return (
            UNIT.format(world=world, project=project, uv=uv)
            .replace(f"genesis resume {world} --quiet", "python scripts/pilots.py")
            .replace("Genesis world pilots", "Genesis pilots P1, P2, P3")
        )
    return UNIT.format(world=world, project=project, uv=uv)
