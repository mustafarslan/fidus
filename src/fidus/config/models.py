"""Schema for fidus.yaml. Every model forbids unknown keys so typos fail loudly."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocsConfig(_Strict):
    dir: str = "docs"
    outline: str = "fidus.outline.yaml"
    default_branch: str | None = None
    index_file: str | None = "README.md"
    language: str = "en"
    style_guide_extra: str | None = None

    @field_validator("dir")
    @classmethod
    def _dir_relative(cls, v: str) -> str:
        v = v.strip().strip("/")
        if not v or v.startswith("..") or "/../" in f"/{v}/":
            raise ValueError("docs.dir must be a relative path inside the docs repo")
        return v


class SourceConfig(_Strict):
    repo: str | None = None
    path: str | None = None
    alias: str | None = None
    branch: str | None = None
    include: list[str] = Field(default_factory=lambda: ["**"])
    exclude: list[str] = Field(default_factory=list)
    sparse: bool = False
    token_env: str | None = None

    @model_validator(mode="after")
    def _one_location(self) -> SourceConfig:
        if (self.repo is None) == (self.path is None):
            raise ValueError("each source needs exactly one of `repo` (owner/name) or `path`")
        if self.repo is not None and not re.fullmatch(r"[\w.-]+/[\w.-]+", self.repo):
            raise ValueError(f"source repo must look like owner/name, got {self.repo!r}")
        if self.alias is None:
            base = self.repo.split("/")[1] if self.repo else self.path.rstrip("/").split("/")[-1]  # type: ignore[union-attr]
            self.alias = re.sub(r"[^a-z0-9_-]", "-", base.lower()).strip("-") or "src"
        if not ALIAS_RE.fullmatch(self.alias):
            raise ValueError(f"alias {self.alias!r} must match {ALIAS_RE.pattern}")
        return self

    @property
    def key(self) -> str:
        """Identifier used in state: owner/repo for GitHub sources, alias for local ones."""
        return self.repo if self.repo else f"local:{self.alias}"

    @property
    def name(self) -> str:
        assert self.alias is not None
        return self.alias


class Pricing(_Strict):
    input_per_mtok: float | None = None
    output_per_mtok: float | None = None


class LLMConfig(_Strict):
    provider: Literal["anthropic", "gemini", "openai", "fake"] = "anthropic"
    model: str = "claude-opus-5"
    api_key_env: str | None = None
    base_url: str | None = None
    # auto = native tool calls, retrying a turn through the JSON protocol when the server
    # returns an empty reply (some OpenAI-compatible servers drop tool calls they can't parse).
    tool_protocol: Literal["auto", "native", "json"] = "auto"
    temperature: float | None = None  # None = provider default (current Claude models reject it)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = (
        None  # Anthropic output effort
    )
    fallbacks: bool = True  # Anthropic server-side refusal fallbacks on models that support them
    max_output_tokens: int = 16_000
    context_window: int = 200_000
    prompt_cache: bool = True
    triage_model: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    pricing: Pricing = Field(default_factory=Pricing)

    @property
    def resolved_api_key_env(self) -> str | None:
        if self.api_key_env:
            return self.api_key_env
        return {
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(self.provider)


class AuditSchedule(_Strict):
    enabled: bool = True
    every_days: int = Field(7, ge=1)
    chapters_per_run: int | None = Field(None, ge=1)
    structure_proposals: Literal["note", "pr", "off"] = "note"


class ScheduleConfig(_Strict):
    audit: AuditSchedule = Field(default_factory=AuditSchedule)


class SyncConfig(_Strict):
    branch: str = "fidus/sync"
    outline_branch: str = "fidus/outline"
    bootstrap_branch: str = "fidus/bootstrap"
    pr_title: str = "docs: Fidus nightly sync"
    labels: list[str] = Field(default_factory=lambda: ["documentation", "fidus"])
    reviewers: list[str] = Field(default_factory=list)
    team_reviewers: list[str] = Field(default_factory=list)
    draft: bool = False
    max_prs_per_run: int = Field(50, ge=1)
    noop_policy: Literal["defer", "state_pr"] = "defer"
    include_pr_body: bool = True


class GitConfig(_Strict):
    author_name: str = "Fidus"
    author_email: str = "fidus-bot@users.noreply.github.com"


class EpisodeBudget(_Strict):
    max_turns: int = 30
    max_input_tokens: int = 600_000
    max_output_tokens: int = 40_000


class CallBudget(_Strict):
    max_input_tokens: int = 60_000
    max_output_tokens: int = 4_000


class RunBudget(_Strict):
    max_total_tokens: int = 4_000_000
    max_episodes: int = 40


class Budgets(_Strict):
    episode: EpisodeBudget = Field(default_factory=EpisodeBudget)
    consistency: EpisodeBudget = Field(
        default_factory=lambda: EpisodeBudget(
            max_turns=12, max_input_tokens=150_000, max_output_tokens=16_000
        )
    )
    triage: CallBudget = Field(default_factory=CallBudget)
    outline: EpisodeBudget = Field(
        default_factory=lambda: EpisodeBudget(
            max_turns=40, max_input_tokens=800_000, max_output_tokens=20_000
        )
    )
    run: RunBudget = Field(default_factory=RunBudget)
    max_parallel_episodes: int = Field(2, ge=1, le=16)


class Limits(_Strict):
    diff_max_bytes_per_file: int = 16_000
    diff_max_bytes_per_pr: int = 96_000
    pr_body_max_chars: int = 4_000
    read_file_max_bytes: int = 60_000
    list_files_max_entries: int = 500
    search_max_results: int = 50
    triage_confidence_threshold: float = 0.6
    max_consistency_episodes: int = 6


class GitHubConfig(_Strict):
    api_url: str = "https://api.github.com"
    server_url: str = "https://github.com"


class FidusConfig(_Strict):
    version: Literal[1] = 1
    docs: DocsConfig = Field(default_factory=DocsConfig)
    sources: list[SourceConfig] = Field(min_length=1)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    budgets: Budgets = Field(default_factory=Budgets)
    limits: Limits = Field(default_factory=Limits)
    github: GitHubConfig = Field(default_factory=GitHubConfig)

    @model_validator(mode="after")
    def _unique_aliases(self) -> FidusConfig:
        seen: set[str] = set()
        for s in self.sources:
            if s.name in seen:
                raise ValueError(f"duplicate source alias {s.name!r}; set `alias:` explicitly")
            seen.add(s.name)
        return self

    def source(self, alias: str) -> SourceConfig:
        for s in self.sources:
            if s.name == alias:
                return s
        raise KeyError(alias)

    @property
    def aliases(self) -> list[str]:
        return [s.name for s in self.sources]
