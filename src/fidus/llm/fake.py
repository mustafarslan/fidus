"""Providers that need no network: a scripted one for tests and a heuristic one for dry runs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from fidus.llm.types import (
    Completion,
    Message,
    Role,
    TextPart,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)

Step = Completion | Callable[[list[Message], list[ToolSpec]], Completion]


def fake_tool_call(name: str, call_id: str | None = None, **arguments: Any) -> Completion:
    return Completion(
        Message(Role.ASSISTANT, [ToolCall(call_id or f"call_{name}", name, arguments)]),
        Usage(100, 20),
        "tool_use",
        "fake",
    )


def fake_text(text: str) -> Completion:
    return Completion(Message(Role.ASSISTANT, [TextPart(text)]), Usage(100, 20), "end_turn", "fake")


class FakeProvider:
    """Pops one scripted step per call and records every request in `.calls`."""

    name = "fake"
    model = "fake-scripted"
    supports_native_tools = True
    allows_history_edits = True

    def __init__(self, script: list[Step]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        temperature: float | None,
    ) -> Completion:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if not self.script:
            raise AssertionError("FakeProvider script exhausted")
        step = self.script.pop(0)
        return step(messages, tools) if callable(step) else step


def _last_tool_results(messages: list[Message]) -> list[ToolResult]:
    if not messages:
        return []
    return [p for p in messages[-1].parts if isinstance(p, ToolResult)]


def _brief(messages: list[Message]) -> str:
    return messages[0].text if messages else ""


def _field(brief: str, pattern: str) -> str | None:
    m = re.search(pattern, brief)
    return m.group(1) if m else None


class HeuristicFakeProvider:
    """Deterministic offline stand-in used by `--provider fake` (dry runs, CI, demos).

    It exercises the full pipeline (reading, writing, validation, state, PR body) without
    judging anything. It writes a stub chapter with an "Updates" log of triggers.
    """

    name = "fake"
    model = "heuristic-fake"
    supports_native_tools = True
    allows_history_edits = True

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        temperature: float | None,
    ) -> Completion:
        names = {t.name for t in tools}
        if not tools:
            return self._triage(messages)
        if "propose_outline" in names:
            return self._outline(messages)
        return self._chapter(messages)

    # -- chapter episodes -------------------------------------------------------------------
    def _chapter(self, messages: list[Message]) -> Completion:
        brief = _brief(messages)
        turn = sum(1 for m in messages if m.role == Role.ASSISTANT)
        chapter_id = _field(brief, r"\(`([a-z0-9-]+)`\)") or "chapter"
        path = _field(brief, r"Chapter file: `([^`]+)`") or f"{chapter_id}.md"
        if turn == 0:
            return fake_tool_call("read_doc", "h1", path=path)
        if turn == 1:
            current = ""
            for r in _last_tool_results(messages):
                if r.name == "read_doc" and not r.is_error:
                    current = r.content
            return fake_tool_call(
                "write_doc", "h2", content=self._render(brief, chapter_id, current)
            )
        results = _last_tool_results(messages)
        if (
            turn == 2
            and results
            and results[0].is_error is False
            and "Validation passed" not in results[0].content
        ):
            # Validation failed once: rewrite without citations/links to converge.
            return fake_tool_call(
                "write_doc", "h3", content=self._render(brief, chapter_id, "", minimal=True)
            )
        reasons = [
            {
                "repo": m.group(1),
                "number": int(m.group(2)),
                "reason": "Recorded in the chapter's update log.",
            }
            for m in re.finditer(r"get_pr\(repo='([^']+)', number=(\d+)\)", brief)
        ]
        return fake_tool_call(
            "done",
            "h4",
            summary=f"Heuristic fake update of {chapter_id}.",
            changed=True,
            trigger_reasons=reasons,
            findings=[{"severity": "info", "text": "Heuristic provider: no real verification."}]
            if "audit" in brief.lower()
            else [],
        )

    @staticmethod
    def _render(brief: str, chapter_id: str, current: str, minimal: bool = False) -> str:
        title = _field(brief, r'chapter [\d.]+ "([^"]+)"') or chapter_id
        if current.strip() and not current.startswith("Error"):
            body = current.rstrip()
        else:
            body = (
                f"---\ntitle: {title}\nfidus:\n  chapter: {chapter_id}\n---\n\n# {title}\n\n"
                "> **Learning objectives**\n> - Placeholder generated by the heuristic fake provider\n\n"
                "## Summary\n\nThis chapter was generated offline without an LLM.\n"
            )
        files = (
            []
            if minimal
            else re.findall(r"`([a-z0-9_-]+:[^`*?\s]+)`", brief.split("files:", 1)[-1])
        )
        lines = [f"- touched `{f}`" for f in dict.fromkeys(files)][:20]
        if "## Updates" not in body:
            body += "\n\n## Updates\n"
        if lines:
            body += "\n" + "\n".join(lines) + "\n"
        return body + ("\n" if not body.endswith("\n") else "")

    # -- init -------------------------------------------------------------------------------
    def _outline(self, messages: list[Message]) -> Completion:
        brief = _brief(messages)
        aliases = re.findall(r"^### Repository `([a-z0-9_-]+)`", brief, re.M) or ["src"]
        parts: list[dict[str, Any]] = []
        prev: list[str] = []
        for i, alias in enumerate(aliases, start=1):
            dirs = re.findall(rf"^- {re.escape(alias)}:([A-Za-z0-9_.-]+)/(?:\s|$)", brief, re.M)[
                :6
            ] or ["."]
            chapters = []
            for j, d in enumerate(dirs, start=1):
                cid = re.sub(r"[^a-z0-9-]", "-", f"{alias}-{d}".lower()).strip("-")
                chapters.append(
                    {
                        "id": cid,
                        "title": f"{alias}: {d}",
                        "path": f"part-{i}-{alias}/{j:02d}-{cid}.md",
                        "level": "fundamentals" if j == 1 else "intermediate",
                        "summary": f"The {d} area of {alias}.",
                        "sources": [f"{alias}:{d}/**" if d != "." else f"{alias}:**"],
                        "prerequisites": prev[-1:],
                    }
                )
                prev.append(cid)
            parts.append(
                {"id": f"part-{i}-{alias}", "title": f"Part {i}: {alias}", "chapters": chapters}
            )
        return fake_tool_call(
            "propose_outline", "o1", version=1, title="Project Handbook", parts=parts
        )

    # -- triage (no tools: JSON reply) -------------------------------------------------------
    def _triage(self, messages: list[Message]) -> Completion:
        brief = _brief(messages)
        chapters = re.findall(r"^- ([a-z0-9-]+): .*? globs: (.*)$", brief, re.M)
        files = re.findall(r"^- ([a-z0-9_-]+:\S+)$", brief, re.M)
        out = []
        for f in files:
            alias, path = f.split(":", 1)
            best, score = None, 0
            for cid, globs in chapters:
                for g in re.findall(rf"{re.escape(alias)}:([^,\s]+)", globs):
                    prefix = g.split("*", 1)[0].rstrip("/").split("/")
                    common = len(_common_prefix(prefix, path.split("/")[:-1]))
                    if common > score:
                        best, score = cid, common
            out.append({"file": f, "chapter": best, "confidence": 0.7 if best else 0.0})
        return fake_text(json.dumps(out))


def _common_prefix(a: list[str], b: list[str]) -> list[str]:
    """Longest common run of leading path segments."""
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y or not x:
            break
        n += 1
    return a[:n]
