"""Run-level report, serialised to run.json and the step summary."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from fidus.agent.episode import EpisodeResult


@dataclass
class RunReport:
    started_at: datetime
    kind: str = "sync"  # sync | bootstrap
    mode: str = "auto"
    did_sync: bool = False
    did_audit: bool = False
    triggers_collected: int = 0
    episodes: list[EpisodeResult] = field(default_factory=list)
    deferred_chapters: list[str] = field(default_factory=list)
    rebuilt: bool = False
    published: str | None = None  # PR URL or output dir
    noop: bool = False
    paused: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def changed_chapters(self) -> list[str]:
        return [e.chapter_id for e in self.episodes if e.changed and e.chapter_id]

    @property
    def failed(self) -> list[EpisodeResult]:
        return [e for e in self.episodes if e.failed]

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        for ep in data["episodes"]:
            ep.pop("outline", None)
        return data

    def summary_markdown(self) -> str:
        if self.noop:
            return "### Fidus\nNothing to do: no merged PRs, no audit due."
        if self.paused:
            return f"### Fidus paused\n{self.paused}"
        lines = [
            "### Fidus run",
            f"- mode: `{self.mode}` (sync={self.did_sync}, audit={self.did_audit})",
            f"- triggers collected: {self.triggers_collected}",
            f"- episodes: {len(self.episodes)}; chapters changed: {len(self.changed_chapters)}; "
            f"failed: {len(self.failed)}",
        ]
        if self.deferred_chapters:
            lines.append(f"- deferred to next run: {', '.join(self.deferred_chapters)}")
        if self.published:
            lines.append(f"- published: {self.published}")
        lines += [f"- {n}" for n in self.notes]
        return "\n".join(lines)
