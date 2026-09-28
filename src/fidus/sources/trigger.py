"""A trigger is one unit of upstream change: a merged PR, or a commit for local sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ChangedFile:
    path: str
    status: str = "modified"  # added | modified | removed | renamed
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    previous_path: str | None = None


@dataclass
class Trigger:
    repo: str  # state key: owner/repo or local:<alias>
    alias: str
    title: str
    number: int | None = None
    sha: str | None = None
    url: str | None = None
    body: str = ""
    merged_at: datetime | None = None
    files: list[ChangedFile] = field(default_factory=list)
    omitted_files: int = 0  # files dropped by filters or API caps
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return (
            f"{self.repo}#{self.number}" if self.number is not None else f"{self.repo}@{self.sha}"
        )

    @property
    def label(self) -> str:
        if self.number is not None:
            return f"{self.repo}#{self.number}"
        return f"{self.alias}@{(self.sha or '')[:8]}"
