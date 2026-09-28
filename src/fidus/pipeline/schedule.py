"""Decide whether an audit is due and which chapters it covers."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fidus.config.models import FidusConfig
from fidus.config.outline import Outline
from fidus.state.models import State

RunMode = Literal["auto", "sync", "audit", "full"]


def audit_due(state: State, cfg: FidusConfig, now: datetime) -> bool:
    audit = cfg.schedule.audit
    if not audit.enabled:
        return False
    last = state.pending.audit.ran_at if state.pending.audit else None
    last = last or state.base.audit.last_run_at or state.base.bootstrapped_at
    if last is None:
        return True
    return now - last >= timedelta(days=audit.every_days) - timedelta(minutes=30)


def resolve_modes(
    mode: RunMode, state: State, cfg: FidusConfig, now: datetime
) -> tuple[bool, bool]:
    """Return (do_sync, do_audit)."""
    if mode == "sync":
        return True, False
    if mode == "audit":
        return False, True
    if mode == "full":
        return True, True
    return True, audit_due(state, cfg, now)


def audit_selection(outline: Outline, state: State, cfg: FidusConfig) -> tuple[list[str], int]:
    """Return (chapter ids to audit, next rotation index)."""
    ids = [c.id for c in outline.chapters()]
    n = cfg.schedule.audit.chapters_per_run
    if not n or n >= len(ids):
        return ids, 0
    start = state.base.audit.rotation_index % len(ids)
    picked = [ids[(start + i) % len(ids)] for i in range(n)]
    return picked, (start + n) % len(ids)
