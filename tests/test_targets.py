"""S2.0.8: `genesis check` grades the Stage 2 targets; `genesis report` writes one page."""

import subprocess
import sys

from genesis.config import WorldConfig
from genesis.history.targets import _decades, _depth, stage2
from genesis.persistence.db import WorldStore
from genesis.simulation import service
from genesis.simulation.engine import Simulation


def _world(tmp_path, days=60):
    path = tmp_path / "worlds" / "w.sqlite"
    path.parent.mkdir()
    WorldStore.create(path, Simulation(WorldConfig.from_file("worlds/two_valleys.toml"))).close()
    service.run(path, 4 * days, llm=False, snapshot_minutes=0, echo=lambda *_: None)
    return path


def test_depth_counts_made_from_made():
    recipes = {
        "a": [["clay"], ["shape"]],
        "b": [["a", "reed"], ["weave"]],
        "c": [["b"], ["hollow"]],
        "loop": [["loop"], []],  # a recipe can't make itself deeper
    }
    assert _depth(recipes) == 3
    assert _depth({}) == 0


def test_inventions_are_counted_per_complete_decade():
    tpy = 4 * 360
    assert _decades([0, 5 * tpy, 15 * tpy, 25 * tpy], tpy, years=24.5) == [2, 1]


def test_every_stage_2_target_has_a_row(tmp_path):
    rows = stage2(_world(tmp_path))
    text = " ".join(r.requirement for r in rows)
    for target in ("every decade", "15+ uses", "lost", "Research", "Culture", "Land", "People:"):
        assert target in text, target
    assert {r.mark for r in rows} <= {"ok", "weak", "no", "wait", "n/a"}
    land = next(r for r in rows if r.requirement.startswith("Land"))
    assert land.mark == "n/a" and "living_land = 0" in land.shows  # off in this world


def test_check_and_report_from_the_command_line(tmp_path):
    path = _world(tmp_path)
    env = {"GENESIS_DATA": str(path.parent), "PATH": "/usr/bin:/bin"}
    run = [sys.executable, "-m", "genesis.cli"]
    out = subprocess.run([*run, "check", "w"], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "Stage 2 targets" in out.stdout and "Invention keeps going" in out.stdout
    page = tmp_path / "r.md"
    out = subprocess.run(
        [*run, "report", "w", "--out", str(page)], env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    text = page.read_text()
    for heading in ("## Stage 1 requirements", "## Stage 2 targets", "## Decade by decade"):
        assert heading in text
    assert "_Comes with S2.7.5._" in text  # the Chronicle, still to come
