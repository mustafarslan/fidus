"""Tool abstraction: a pydantic args model gives both the JSON schema and validation."""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from fidus.agent.context import EpisodeContext, Mode
from fidus.errors import GuardViolation
from fidus.llm.types import ToolCall, ToolResult, ToolSpec


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $defs/$ref so every provider (notably Gemini) accepts the schema."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return walk(copy.deepcopy(defs[name]))
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out: dict[str, Any] = walk(schema)
    return out


def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    schema = _inline_refs(model.model_json_schema())
    schema.pop("title", None)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]]

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, schema_for(self.Args))

    @abstractmethod
    def run(self, ctx: EpisodeContext, args: Any) -> str: ...


class ToolSet:
    def __init__(self, tools: list[Tool]) -> None:
        self.tools = {t.name: t for t in tools}

    @property
    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self.tools.values()]

    def names(self) -> list[str]:
        return list(self.tools)

    def run(self, call: ToolCall, ctx: EpisodeContext) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None:
            return ToolResult(
                call.id,
                call.name,
                f"Unknown tool {call.name!r}. Available: {', '.join(self.tools)}",
                True,
            )
        try:
            args = tool.Args.model_validate(call.arguments or {})
        except ValidationError as e:
            return ToolResult(call.id, call.name, f"Invalid arguments: {e}", True)
        try:
            return ToolResult(call.id, call.name, tool.run(ctx, args))
        except (GuardViolation, FileNotFoundError, ValueError, KeyError) as e:
            return ToolResult(call.id, call.name, f"Error: {e}", True)

    @classmethod
    def for_mode(cls, mode: Mode) -> ToolSet:
        from fidus.agent.tools.control_tools import DoneTool, ProposeOutlineTool
        from fidus.agent.tools.doc_tools import (
            ListDocsTool,
            ReadDocTool,
            ReadOutlineTool,
            WriteDocTool,
        )
        from fidus.agent.tools.source_tools import (
            GetPRTool,
            ListFilesTool,
            ReadFileTool,
            SearchCodeTool,
        )

        source: list[Tool] = [ListFilesTool(), ReadFileTool(), SearchCodeTool()]
        docs: list[Tool] = [ListDocsTool(), ReadDocTool(), ReadOutlineTool(), WriteDocTool()]
        if mode == "init":
            return cls([*source, ProposeOutlineTool()])
        if mode == "sync":
            return cls([*source, GetPRTool(), *docs, DoneTool()])
        return cls([*source, *docs, DoneTool()])
