"""Everything an agent episode can see and touch."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fidus.config.models import FidusConfig
from fidus.config.outline import Chapter, Outline
from fidus.sources.trigger import Trigger

Mode = Literal["sync", "audit", "bootstrap", "consistency", "init"]


@dataclass
class EpisodeContext:
    mode: Mode
    cfg: FidusConfig
    repo_root: Path  # docs repository root
    workspaces: dict[str, Path]  # source alias -> clone root
    outline: Outline | None = None
    chapter: Chapter | None = None
    triggers: list[Trigger] = field(default_factory=list)
    original_doc: str | None = None  # chapter content before this episode
    human_lines: list[str] = field(
        default_factory=list
    )  # added by humans since Fidus last edited it
    protected_paths: list[Path] = field(default_factory=list)
    secret_values: list[str] = field(default_factory=list)
    # mutable episode state
    written: bool = False
    last_written: str | None = None
    read_files: set[str] = field(default_factory=set)  # "alias:path" read this episode

    @property
    def docs_root(self) -> Path:
        return self.repo_root / self.cfg.docs.dir

    @property
    def chapter_path(self) -> Path | None:
        if self.chapter is None:
            return None
        return self.docs_root / self.chapter.path

    def trigger(self, repo: str, number: int) -> Trigger | None:
        for t in self.triggers:
            if t.number == number and (t.repo == repo or t.alias == repo):
                return t
        return None
