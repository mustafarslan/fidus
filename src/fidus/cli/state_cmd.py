"""`fidus state show|set-cursor`."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from fidus.errors import ConfigError
from fidus.gitops.runner import git
from fidus.state.models import State
from fidus.state.store import STATE_PATH, dump_state, parse_state, write_state


def _read(root: Path, ref: str | None) -> State | None:
    if ref:
        proc = git(["show", f"{ref}:{STATE_PATH}"], root, check=False)
        return parse_state(proc.stdout if proc.returncode == 0 else None, from_default_branch=False)
    p = root / STATE_PATH
    return parse_state(
        p.read_text(encoding="utf-8") if p.exists() else None, from_default_branch=False
    )


def show(config: Path, ref: str | None, console: Console) -> None:
    state = _read(config.resolve().parent, ref)
    if state is None:
        console.print("no state yet (run `fidus bootstrap`)")
        return
    print(dump_state(state), end="")


def set_cursor(config: Path, repo: str, to: str, console: Console) -> None:
    root = config.resolve().parent
    state = _read(root, None) or State()
    when = datetime.now(UTC) if to == "now" else datetime.fromisoformat(to.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    if "/" not in repo and not repo.startswith("local:"):
        raise ConfigError("--repo must be owner/name or local:<alias>")
    cursor = state.cursor(repo)
    cursor.cursor_merged_at = when
    cursor.cursor_pr_numbers = []
    cursor.cursor_sha = None
    write_state(root, state)
    console.print(
        f"cursor for {repo} set to {when.isoformat()}; commit {STATE_PATH} to the default branch"
    )
