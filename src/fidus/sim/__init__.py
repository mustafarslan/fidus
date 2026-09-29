"""Simulation harness: the real `fidus` CLI against a local fake GitHub over scripted days."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fidus.sim.runner import SimResult, Simulation


def run_scenario(scenario: Path, root: Path, llm: dict[str, Any] | None = None) -> SimResult:
    return Simulation(scenario, root, llm).run()


__all__ = ["SimResult", "Simulation", "run_scenario"]
