"""Keep the conversation inside the context window by eliding old tool results."""

from __future__ import annotations

from fidus.llm.types import Message, TextPart, ToolCall, ToolResult

ELIDED = "[elided to save context; call the tool again if you still need this]"


def message_chars(messages: list[Message]) -> int:
    total = 0
    for m in messages:
        for p in m.parts:
            if isinstance(p, TextPart):
                total += len(p.text)
            elif isinstance(p, ToolResult):
                total += len(p.content)
            elif isinstance(p, ToolCall):
                total += len(str(p.arguments)) + len(p.name)
    return total


def elide_if_needed(
    messages: list[Message],
    context_window_tokens: int,
    *,
    system_chars: int = 0,
    ratio: float = 0.7,
) -> int:
    """Elide oldest tool results until under `ratio` of the window. Keeps the first message
    (the brief) and the last two messages intact. Returns the number of results elided."""
    limit = int(context_window_tokens * 4 * ratio)
    if message_chars(messages) + system_chars <= limit:
        return 0
    elided = 0
    for i in range(1, max(1, len(messages) - 2)):
        m = messages[i]
        changed = False
        new_parts: list[TextPart | ToolCall | ToolResult] = []
        for p in m.parts:
            if isinstance(p, ToolResult) and p.content != ELIDED and len(p.content) > 400:
                new_parts.append(ToolResult(p.call_id, p.name, ELIDED, p.is_error))
                changed = True
                elided += 1
            else:
                new_parts.append(p)
        if changed:
            messages[i] = Message(m.role, new_parts)
            if message_chars(messages) + system_chars <= limit:
                break
    return elided
