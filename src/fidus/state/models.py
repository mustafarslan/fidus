"""Run state persisted as .fidus/state.json on the sync branch.

`base` is what becomes true once the sync PR is merged (cursors, last audit).
`pending` describes everything accumulated in the currently open sync PR; it is
cleared whenever state is read from the default branch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TriggerStatus = Literal["applied", "no_impact", "unmapped", "failed", "pending"]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @field_validator("*", mode="after")
    @classmethod
    def _utc(cls, v: Any) -> Any:
        if isinstance(v, datetime):
            return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
        return v


class RepoCursor(_M):
    cursor_merged_at: datetime | None = None
    cursor_pr_numbers: list[int] = Field(default_factory=list)
    cursor_sha: str | None = None  # local sources: last processed commit
    head_sha_seen: str | None = None


class AuditBase(_M):
    last_run_at: datetime | None = None
    rotation_index: int = 0


class PendingTrigger(_M):
    repo: str
    number: int | None = None
    sha: str | None = None
    title: str
    url: str | None = None
    merged_at: datetime | None = None
    status: TriggerStatus = "pending"
    chapters: dict[str, str] = Field(default_factory=dict)  # chapter id -> reason
    unmapped_files: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return (
            f"{self.repo}#{self.number}" if self.number is not None else f"{self.repo}@{self.sha}"
        )


class Finding(_M):
    chapter: str
    severity: Literal["info", "warn", "error"] = "info"
    text: str


class PendingAudit(_M):
    ran_at: datetime | None = None
    chapters_checked: list[str] = Field(default_factory=list)
    changed: dict[str, str] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    structure_suggestions: list[str] = Field(default_factory=list)


class IncompleteEpisode(_M):
    """A chapter that still owes an update. Lives in `base`, so it survives the PR being
    merged: the cursor has moved past its triggers, and this is what keeps them from being lost."""

    chapter: str
    reason: str
    triggers: list[PendingTrigger] = Field(default_factory=list)


class BaseState(_M):
    bootstrapped_at: datetime | None = None
    repos: dict[str, RepoCursor] = Field(default_factory=dict)
    audit: AuditBase = Field(default_factory=AuditBase)
    retry: list[IncompleteEpisode] = Field(default_factory=list)


class RunUsage(_M):
    run_at: datetime
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float | None = None


class Pending(_M):
    opened_at: datetime | None = None
    runs: int = 0
    triggers: list[PendingTrigger] = Field(default_factory=list)
    audit: PendingAudit | None = None
    consistency: dict[str, str] = Field(default_factory=dict)  # chapter id -> reason
    new_chapters: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(
        default_factory=list
    )  # changes skipped: previous PR closed unmerged
    orphans: list[str] = Field(default_factory=list)
    usage: list[RunUsage] = Field(default_factory=list)
    paused_reason: str | None = None

    def touched_chapters(self) -> set[str]:
        out: set[str] = set(self.new_chapters) | set(self.consistency)
        for t in self.triggers:
            out.update(t.chapters)
        if self.audit:
            out.update(self.audit.changed)
        return out


class State(_M):
    version: Literal[1] = 1
    base: BaseState = Field(default_factory=BaseState)
    pending: Pending = Field(default_factory=Pending)

    def cursor(self, repo_key: str) -> RepoCursor:
        return self.base.repos.setdefault(repo_key, RepoCursor())
