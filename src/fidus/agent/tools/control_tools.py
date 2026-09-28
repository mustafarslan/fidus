"""Terminal tools. The episode loop intercepts these; `run` only acknowledges."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from fidus.agent.context import EpisodeContext
from fidus.agent.tools.base import Tool, ToolArgs
from fidus.config.outline import Outline


class TriggerReason(ToolArgs):
    repo: str = Field(description="owner/name (or alias) of the triggering PR's repo")
    number: int | None = Field(None, description="PR number; null for commit triggers")
    reason: str = Field(
        description="One line (<200 chars): what you changed because of it, or why nothing"
    )


class FindingArg(ToolArgs):
    severity: Literal["info", "warn", "error"] = "info"
    text: str


class DoneArgs(ToolArgs):
    summary: str = Field(description="One or two sentences describing what you did to the chapter")
    changed: bool = Field(description="True if you wrote a changed chapter")
    trigger_reasons: list[TriggerReason] = Field(default_factory=list)
    findings: list[FindingArg] = Field(
        default_factory=list,
        description="Audit findings: drift you found (and fixed or could not fix)",
    )
    structure_suggestions: list[str] = Field(
        default_factory=list,
        description="Ideas for outline changes (new/merged/split chapters). Never restructure yourself.",
    )
    concepts_changed: list[str] = Field(
        default_factory=list,
        description=(
            "Terms, APIs or concepts you renamed/removed/redefined that LATER chapters may reference. "
            "Leave empty for additive or local changes."
        ),
    )


class DoneTool(Tool):
    name = "done"
    description = (
        "Finish the episode. Call exactly once, after write_doc (if you changed the chapter)."
    )
    Args = DoneArgs

    def run(self, ctx: EpisodeContext, args: DoneArgs) -> str:  # pragma: no cover - intercepted
        return "ok"


class ProposeOutlineTool(Tool):
    name = "propose_outline"
    description = (
        "Submit the proposed textbook outline (Parts -> Chapters, fundamentals first). "
        "This ends the episode."
    )
    Args = Outline

    def run(self, ctx: EpisodeContext, args: Outline) -> str:  # pragma: no cover - intercepted
        return "ok"


TERMINAL_TOOLS = {"done", "propose_outline"}
