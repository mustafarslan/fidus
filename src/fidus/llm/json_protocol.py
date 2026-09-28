"""JSON-envelope tool protocol for models without (reliable) native tool calling.

The model replies with exactly one JSON object `{"tool": ..., "arguments": {...}}`; tool
results come back as text. Works with any chat model, including small open-weight ones.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fidus.agent.prompts import render
from fidus.llm.base import Provider
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
from fidus.log import get_logger

MAX_REPAIRS = 2
log = get_logger("llm.json")
FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*(.*?)```", re.DOTALL)


def _first_object(text: str) -> str | None:
    """Return the first balanced {...} in text, respecting strings."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def _candidates(text: str) -> list[str]:
    """The first JSON object in each code fence, then the first one in the raw text."""
    out = [obj for m in FENCE.finditer(text) if (obj := _first_object(m.group(1)))]
    raw = _first_object(text)
    if raw:
        out.append(raw)
    return out


def parse_envelope(text: str, tool_names: set[str]) -> tuple[str, dict[str, Any]]:
    data: Any = None
    for candidate in _candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "tool" in parsed:
            data = parsed
            break
    if data is None:
        raise ValueError('no JSON object with a "tool" key found')
    name = data["tool"]
    if name not in tool_names:
        raise ValueError(f"unknown tool {name!r}; choose one of {sorted(tool_names)}")
    args = data.get("arguments", {})
    if not isinstance(args, dict):
        raise ValueError('"arguments" must be an object')
    return name, args


def _as_text_messages(messages: list[Message]) -> list[Message]:
    """Rewrite tool calls/results as plain text so any chat endpoint accepts them."""
    out: list[Message] = []
    for m in messages:
        chunks: list[str] = []
        for p in m.parts:
            if isinstance(p, TextPart):
                chunks.append(p.text)
            elif isinstance(p, ToolCall):
                chunks.append(json.dumps({"tool": p.name, "arguments": p.arguments}))
            elif isinstance(p, ToolResult):
                status = ' error="true"' if p.is_error else ""
                chunks.append(
                    f'<tool_result name="{p.name}" id="{p.call_id}"{status}>\n{p.content}\n</tool_result>'
                )
        out.append(Message(m.role, [TextPart("\n\n".join(chunks) or "(continue)")]))
    return out


class JsonToolProvider:
    def __init__(self, inner: Provider) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.supports_native_tools = True
        self.allows_history_edits = inner.allows_history_edits
        self._turn = 0

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        temperature: float | None,
    ) -> Completion:
        if not tools:
            return self.inner.complete(
                system=system,
                messages=_as_text_messages(messages),
                tools=[],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        catalog = "\n\n".join(
            f"### {t.name}\n{t.description}\n```json\n{json.dumps(t.parameters)}\n```"
            for t in tools
        )
        full_system = system + "\n\n" + render("json_tool_protocol", tools=catalog)
        convo = _as_text_messages(messages)
        names = {t.name for t in tools}
        usage = Usage()
        last_text = ""
        for _ in range(MAX_REPAIRS + 1):
            c = self.inner.complete(
                system=full_system,
                messages=convo,
                tools=[],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            usage = usage + c.usage
            last_text = c.message.text
            try:
                name, args = parse_envelope(last_text, names)
            except (ValueError, json.JSONDecodeError) as e:
                convo = [
                    *convo,
                    Message(Role.ASSISTANT, [TextPart(last_text or "(empty)")]),
                    Message(
                        Role.USER,
                        [
                            TextPart(
                                f"Invalid tool call: {e}. Reply with ONLY a JSON object: "
                                '{"tool": "<name>", "arguments": {...}}'
                            )
                        ],
                    ),
                ]
                continue
            self._turn += 1
            call = ToolCall(f"j{self._turn}", name, args)
            return Completion(Message(Role.ASSISTANT, [call]), usage, "tool_use", c.raw_model)
        # Give up: return plain text; the episode loop treats it as "no tool call".
        return Completion(
            Message(Role.ASSISTANT, [TextPart(last_text)]), usage, "end_turn", self.model
        )


class AutoToolProvider:
    """Native tool calling with a per-turn JSON-protocol fallback.

    Some OpenAI-compatible servers silently drop a tool call they fail to parse (typically a
    large, deeply nested one) and return an empty message. Instead of failing the episode,
    retry that same turn through the JSON protocol; after it rescues a turn, stay on JSON for
    the rest of the session.
    """

    def __init__(self, inner: Provider) -> None:
        self.inner = inner
        self.json = JsonToolProvider(inner)
        self.name = inner.name
        self.model = inner.model
        self.supports_native_tools = True
        self.allows_history_edits = inner.allows_history_edits
        self.use_json = False

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        temperature: float | None,
    ) -> Completion:
        kw: dict[str, Any] = {
            "system": system,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self.use_json and tools:
            return self.json.complete(**kw)
        c = self.inner.complete(**kw)
        empty = not c.message.tool_calls and not c.message.text.strip()
        if tools and empty and c.stop_reason != "max_tokens":
            log.warning(
                "empty reply from %s/%s; retrying this turn with the JSON tool protocol",
                self.name,
                self.model,
            )
            rescued = self.json.complete(**kw)
            rescued.usage = c.usage + rescued.usage
            if rescued.message.tool_calls:
                self.use_json = True
            return rescued
        return c
