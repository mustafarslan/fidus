"""The workflow users copy must stay in sync with the repo's own (Dependabot-bumped) workflows."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USES = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)@v(\d+)")
TEMPLATE = ROOT / "src/fidus/templates/workflow.yml"
USER_FACING = [
    TEMPLATE,
    ROOT / "examples/workflows/fidus.yml",
    ROOT / "README.md",
    ROOT / "docs/github-app.md",
]


def _majors(paths: list[Path]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in paths:
        for action, major in USES.findall(p.read_text(encoding="utf-8")):
            out.setdefault(action, set()).add(major)
    return out


def test_example_matches_packaged_template() -> None:
    assert (ROOT / "examples/workflows/fidus.yml").read_text() == TEMPLATE.read_text()


def test_user_facing_action_versions_match_repo_workflows() -> None:
    repo = _majors([*sorted((ROOT / ".github/workflows").glob("*.yml")), ROOT / "action.yml"])
    for path in USER_FACING:
        for action, majors in _majors([path]).items():
            if action in repo:
                assert majors <= repo[action], (
                    f"{path.relative_to(ROOT)} uses {action}@v{sorted(majors)} but the repo's "
                    f"workflows use v{sorted(repo[action])}; update the user-facing copy"
                )
