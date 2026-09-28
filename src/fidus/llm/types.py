"""Provider-neutral message types. Every adapter translates to and from these."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False


Part = TextPart | ToolCall | ToolResult


@dataclass
class Message:
    role: Role
    parts: list[Part]
    # Provider-native assistant content (thinking blocks, thought signatures, ...). The adapter
    # that produced it re-emits it verbatim on the next turn; others use `parts`.
    raw: Any = None
    raw_provider: str | None = None

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [p for p in self.parts if isinstance(p, ToolCall)]


def user(text: str) -> Message:
    return Message(Role.USER, [TextPart(text)])


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (type: object)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


StopReason = Literal["end_turn", "tool_use", "max_tokens", "error"]


@dataclass
class Completion:
    message: Message
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = "end_turn"
    raw_model: str = ""
