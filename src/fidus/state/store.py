"""Read and write .fidus/state.json."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from fidus.errors import FidusError
from fidus.state.models import Pending, State

STATE_PATH = ".fidus/state.json"


def parse_state(text: str | None, *, from_default_branch: bool) -> State | None:
    """Parse state text. State read from the default branch has `pending` cleared,
    because everything on the default branch has, by definition, been merged."""
    if text is None or not text.strip():
        return None
    try:
        state = State.model_validate_json(text)
    except ValidationError as e:
        raise FidusError(f"{STATE_PATH} is corrupt: {e}") from e
    if from_default_branch:
        state.pending = Pending()
    return state


def dump_state(state: State) -> str:
    return (
        json.dumps(state.model_dump(mode="json", exclude_none=False), indent=2, sort_keys=False)
        + "\n"
    )


def write_state(repo_root: Path, state: State) -> Path:
    path = repo_root / STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_state(state), encoding="utf-8")
    return path
