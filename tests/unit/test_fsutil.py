from __future__ import annotations

import os
from pathlib import Path

import pytest

from fidus.fsutil import atomic_write


def test_atomic_write_replaces_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "docs" / "ch.md"
    atomic_write(target, "one")
    atomic_write(target, "two")
    assert target.read_text() == "two"
    assert [p.name for p in target.parent.iterdir()] == ["ch.md"]


def test_interrupted_write_keeps_old_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "ch.md"
    target.write_text("old")

    def boom(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write(target, "new, half written")
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["ch.md"]  # temp file cleaned up
