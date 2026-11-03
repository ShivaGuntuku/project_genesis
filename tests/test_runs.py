"""S2.0: runs you can trust: who runs what, at most N worlds at once, stop any owner."""

import subprocess
import sys
import time

import pytest

from genesis.config import WorldConfig
from genesis.persistence.db import WorldStore
from genesis.simulation import service
from genesis.simulation.engine import Simulation

TWO = "worlds/two_valleys.toml"


def worlds(tmp_path, *names):
    folder = tmp_path / "worlds"
    folder.mkdir(exist_ok=True)
    out = []
    for name in names:
        path = folder / f"{name}.sqlite"
        WorldStore.create(path, Simulation(WorldConfig.from_file(TWO))).close()
        out.append(path)
    return out


def test_at_most_two_worlds_run_at_once(tmp_path, monkeypatch):
    monkeypatch.delenv("GENESIS_MAX_RUNNING", raising=False)
    a, b, c = worlds(tmp_path, "a", "b", "c")
    with service.run_lock(a, "test a", kind="viewer"), service.run_lock(b, "test b"):
        with pytest.raises(service.WorldBusy, match="2 worlds are already running") as e:
            with service.run_lock(c, "test c"):
                pass
        assert "a (test a" in str(e.value) and "b (test b" in str(e.value)
        assert " c (" not in str(e.value)  # not itself
        assert not service.is_running(c)  # its world lock was given back
        listed = {o["world"]: o for o in service.owners(service.run_dir(a))}
        assert set(listed) == {"a", "b"} and listed["a"]["kind"] == "viewer"
        assert listed["a"]["pid"] > 0 and service.holder(a)["who"] == "test a"
    with service.run_lock(c, "test c"):  # slots are free again
        pass


def test_the_limit_can_be_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("GENESIS_MAX_RUNNING", "1")
    a, b = worlds(tmp_path, "a", "b")
    with service.run_lock(a, "test a"):
        with pytest.raises(service.WorldBusy, match="1 worlds are already running"):
            with service.run_lock(b, "test b"):
                pass


def _start(args, env):
    return subprocess.Popen(
        [sys.executable, "-m", "genesis.cli", *args],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait(cond, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("command", ["run", "start"])
def test_stop_works_on_any_owner_and_pause_reaches_genesis_run(tmp_path, command):
    import os

    (path,) = worlds(tmp_path, "w")
    env = {**os.environ, "GENESIS_DATA": str(path.parent)}
    args = (
        ["run", "w", "--days", "5000", "--quiet"]
        if command == "run"
        else ["start", "w", "--days", "5000", "--quiet"]
    )
    proc = _start(args, env)
    try:
        assert _wait(lambda: service.holder(path) is not None)
        if command == "run":
            service.control(path, "pause")
            time.sleep(1.5)
            store = WorldStore(path)
            tick = int(store.meta()["tick"])
            store.close()
            time.sleep(1.5)
            store = WorldStore(path)
            assert int(store.meta()["tick"]) == tick  # paused at a save
            store.close()
            service.control(path, "run")
        result = service.stop_world(path, wait=60)
        assert result.startswith("stopped"), result
        assert proc.wait(30) is not None
        assert service.holder(path) is None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_runs_clean_tidies_what_is_gone(tmp_path):
    (path,) = worlds(tmp_path, "w")
    folder = service.run_dir(path)
    service._write_status(path, state="running", pid=99999999, target_tick=40)
    (folder / "gone.json").write_text('{"state": "stopped"}')
    (folder / "gone.control").write_text("stop")
    (folder / "x.tmp").write_text("")
    done = service.clean(folder, path.parent)
    assert any("gone.json" in d for d in done) and any("marked stopped" in d for d in done)
    assert not (folder / "gone.json").exists() and not (folder / "x.tmp").exists()
    assert service.read_status(path)["state"] == "stopped"


def test_stopping_what_is_not_running_says_so(tmp_path):
    (path,) = worlds(tmp_path, "w")
    assert service.stop_world(path) == "not running"
