"""The realistic 8-day scenario: the real `fidus` CLI (subprocess) against the fake GitHub."""

from __future__ import annotations

from pathlib import Path

from fidus.sim import run_scenario
from fidus.sim.report import render

SCENARIO = Path(__file__).resolve().parents[1] / "sim" / "realistic.yaml"


def test_realistic_scenario_meets_every_expectation(tmp_path: Path) -> None:
    result = run_scenario(SCENARIO, tmp_path / "world")
    report = render(result)
    assert not result.failed_checks, report
    assert result.unhandled == [], f"fake GitHub missed endpoints: {result.unhandled}"
    by_day = {d.day: d for d in result.days}
    assert by_day[4].paused and by_day[6].noop and by_day[7].audit
    assert by_day[6].changed_chapters == []  # a no-op night changes nothing
    assert "caching" in by_day[7].changed_chapters  # the outline addition was written
    assert "docs/part-3/01-caching.md" in result.final_docs
    assert "| 8 |" in report


def test_bot_commits_use_the_simulated_clock(tmp_path: Path) -> None:
    import subprocess

    run_scenario(Path(__file__).resolve().parents[1] / "sim" / "mvp.yaml", tmp_path / "w")
    bare = tmp_path / "w" / "git" / "acme" / "docs.git"
    dates = subprocess.run(
        ["git", "log", "--all", "--format=%an %ad", "--date=short"],
        cwd=bare,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    bot = [d for d in dates if d.startswith("fidus-sim[bot]")]
    assert bot and all(
        d.endswith(("2026-03-03", "2026-03-04")) for d in bot
    )  # nights run at 00:00 next day, dates
