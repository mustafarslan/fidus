"""The agent loop: one episode = one chapter (or one outline proposal)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from fidus.agent.budget import Budget, estimate_tokens
from fidus.agent.context import EpisodeContext, Mode
from fidus.agent.guards import redact_secrets
from fidus.agent.history import elide_if_needed, message_chars
from fidus.agent.prompts import system_prompt
from fidus.agent.tools.base import ToolSet
from fidus.agent.tools.control_tools import TERMINAL_TOOLS, DoneArgs
from fidus.agent.validate_doc import validate_chapter
from fidus.config.outline import Outline
from fidus.errors import BudgetExceeded, ProviderError
from fidus.fsutil import atomic_write
from fidus.llm.base import Provider
from fidus.llm.types import Message, Role, TextPart, ToolResult, Usage, user
from fidus.log import get_logger
from fidus.state.models import Finding

log = get_logger("agent")

Status = Literal["ok", "no_change", "budget_exhausted", "protocol_error", "failed"]


@dataclass
class EpisodeResult:
    chapter_id: str | None
    mode: Mode
    status: Status
    changed: bool = False
    summary: str = ""
    trigger_reasons: dict[str, str] = field(default_factory=dict)  # trigger key -> reason
    findings: list[Finding] = field(default_factory=list)
    structure_suggestions: list[str] = field(default_factory=list)
    concepts_changed: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    outline: Outline | None = None
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.status in ("failed", "protocol_error")


NUDGE = (
    "You must respond with a tool call. If the chapter needs changes, call write_doc with the "
    "complete chapter; then call done."
)
NUDGE_INIT = "You must respond with a tool call. When ready, call propose_outline."
MUST_WRITE = (
    "This chapter does not exist yet: write it with write_doc (the complete chapter) before "
    "calling done."
)
TRUNCATED = (
    "Your reply hit the output-token limit and was cut off, so this call was NOT executed. "
    "Retry with a shorter reply: tighten the chapter, or cover less material."
)
BUDGET_WARNING = (
    "[Fidus] About 80% of your budget is used. Write the chapter now (write_doc) and call done."
)


def _restore(ctx: EpisodeContext) -> None:
    """Undo this episode's write."""
    path = ctx.chapter_path
    if path is None or not ctx.written:
        return
    if ctx.original_doc is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, ctx.original_doc)
    ctx.written = False


def _redact_done(args: DoneArgs, secrets: list[str]) -> DoneArgs:
    """Everything in `done` ends up in the public PR body and state file: mask secrets."""

    def r(text: str) -> str:
        return redact_secrets(text, secrets)

    return args.model_copy(
        update={
            "summary": r(args.summary),
            "trigger_reasons": [
                t.model_copy(update={"reason": r(t.reason)}) for t in args.trigger_reasons
            ],
            "findings": [f.model_copy(update={"text": r(f.text)}) for f in args.findings],
            "structure_suggestions": [r(x) for x in args.structure_suggestions],
            "concepts_changed": [r(x) for x in args.concepts_changed],
        }
    )


def _finish_done(ctx: EpisodeContext, args: DoneArgs, usage: Usage) -> EpisodeResult:
    args = _redact_done(args, ctx.secret_values)
    reasons: dict[str, str] = {}
    for tr in args.trigger_reasons:
        t = ctx.trigger(tr.repo, tr.number) if tr.number is not None else None
        if t is None and tr.number is None:
            t = next(
                (x for x in ctx.triggers if x.number is None and tr.repo in (x.repo, x.alias)), None
            )
        if t is not None:
            reasons[t.key] = tr.reason.strip()[:300]
    for t in ctx.triggers:
        reasons.setdefault(t.key, args.summary.strip()[:300] or "reviewed")
    chapter_id = ctx.chapter.id if ctx.chapter else None
    findings = [
        Finding(chapter=chapter_id or "?", severity=f.severity, text=f.text) for f in args.findings
    ]
    if ctx.written and ctx.last_written is not None:
        for p in validate_chapter(ctx.last_written, ctx):
            findings.append(
                Finding(chapter=chapter_id or "?", severity="warn", text=f"validation: {p}")
            )
    changed = ctx.written and ctx.last_written != ctx.original_doc
    return EpisodeResult(
        chapter_id=chapter_id,
        mode=ctx.mode,
        status="ok" if changed else "no_change",
        changed=changed,
        summary=args.summary.strip(),
        trigger_reasons=reasons,
        findings=findings,
        structure_suggestions=[s.strip() for s in args.structure_suggestions if s.strip()],
        concepts_changed=[c.strip() for c in args.concepts_changed if c.strip()],
        usage=usage,
    )


def run_episode(
    ctx: EpisodeContext,
    provider: Provider,
    budget: Budget,
    *,
    brief: str,
    max_tokens: int,
    temperature: float | None,
    context_window: int,
) -> EpisodeResult:
    toolset = ToolSet.for_mode(ctx.mode)
    system = system_prompt(ctx)
    messages: list[Message] = [user(brief)]
    chapter_id = ctx.chapter.id if ctx.chapter else None
    nudged = False
    warned = False
    done_refused = False

    def result(status: Status, error: str | None = None) -> EpisodeResult:
        changed = ctx.written and ctx.last_written != ctx.original_doc
        return EpisodeResult(
            chapter_id, ctx.mode, status, changed=changed, usage=budget.usage, error=error
        )

    while True:
        if provider.allows_history_edits:
            elide_if_needed(messages, context_window, system_chars=len(system))
        try:
            context_tokens = estimate_tokens(message_chars(messages) + len(system))
            if context_tokens > context_window * 0.9:
                raise BudgetExceeded(f"context window nearly full (~{context_tokens} tokens)")
            budget.check_before_call(context_tokens)
            completion = provider.complete(
                system=system,
                messages=messages,
                tools=toolset.specs,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except BudgetExceeded as e:
            # A partial write is kept (and flagged in the PR); nothing written means nothing to undo.
            log.warning("[%s] budget exhausted: %s", chapter_id or ctx.mode, e)
            return result("budget_exhausted", str(e))
        except ProviderError as e:
            log.error("[%s] provider error: %s", chapter_id or ctx.mode, e)
            _restore(ctx)
            return result("failed", str(e))

        budget.record(completion.usage)
        messages.append(completion.message)
        calls = completion.message.tool_calls

        if calls and completion.stop_reason == "max_tokens":
            # The reply was cut off: arguments (e.g. a whole chapter) may be truncated. Refuse
            # to run anything and ask for a shorter reply.
            messages.append(
                Message(
                    Role.USER,
                    [ToolResult(c.id, c.name, TRUNCATED, True) for c in calls],
                )
            )
            continue

        if not calls:
            if nudged:
                if ctx.written:
                    return result("ok")
                return result("protocol_error", "model stopped without calling a tool")
            nudged = True
            messages.append(user(NUDGE_INIT if ctx.mode == "init" else NUDGE))
            continue

        results: list[TextPart | ToolResult] = []
        for call in calls:
            if call.name in TERMINAL_TOOLS and call.name in toolset.names():
                if call.name == "done":
                    try:
                        args = DoneArgs.model_validate(call.arguments or {})
                    except ValidationError as e:
                        results.append(
                            ToolResult(call.id, call.name, f"Invalid arguments: {e}", True)
                        )
                        continue
                    if ctx.mode == "bootstrap" and not ctx.written:
                        if done_refused:
                            return result("protocol_error", "finished without writing the chapter")
                        done_refused = True
                        results.append(ToolResult(call.id, call.name, MUST_WRITE, True))
                        continue
                    return _finish_done(ctx, args, budget.usage)
                try:
                    outline = Outline.model_validate(call.arguments or {})
                    outline.check_against_aliases(list(ctx.workspaces), ctx.cfg.docs.index_file)
                except (ValidationError, ValueError) as e:
                    results.append(
                        ToolResult(
                            call.id, call.name, f"Outline rejected, fix and resubmit: {e}", True
                        )
                    )
                    continue
                res = result("ok")
                res.outline = outline
                res.changed = True
                return res
            results.append(toolset.run(call, ctx))

        if not warned and budget.near_limit():
            warned = True
            results.append(TextPart(BUDGET_WARNING))
        messages.append(Message(Role.USER, list(results)))
