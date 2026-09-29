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


def clear_retry(
    config: Path, chapter: str | None, all_: bool, unquarantine: bool, console: Console
) -> None:
    root = config.resolve().parent
    state = _read(root, None)
    if state is None or not state.base.retry:
        console.print("no retry entries")
        return
    if not chapter and not all_:
        raise ConfigError("pass --chapter ID or --all")
    targets = [e for e in state.base.retry if all_ or e.chapter == chapter]
    if not targets:
        raise ConfigError(f"no retry entry for chapter {chapter!r}")
    if unquarantine:
        for e in targets:
            e.quarantined = False
            e.attempts = 0
        action = "will be retried next run"
    else:
        state.base.retry = [e for e in state.base.retry if e not in targets]
        action = "removed"
    write_state(root, state)
    names = ", ".join(e.chapter for e in targets)
    console.print(f"{names}: {action}; commit {STATE_PATH} to the default branch")
