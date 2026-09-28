"""Google Gemini adapter over the `google-genai` SDK."""

from __future__ import annotations

from typing import Any

from fidus.config.models import LLMConfig
from fidus.errors import ProviderError
from fidus.llm.retry import is_retryable_status, with_retry
from fidus.llm.types import (
    Completion,
    Message,
    Role,
    StopReason,
    TextPart,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
)

SYNTHETIC_ID = "fidus-syn-"  # ids we invent when Gemini returns none; never sent back


def _wire_id(call_id: str) -> str | None:
    return None if call_id.startswith(SYNTHETIC_ID) else call_id


def _merge_same_role(contents: list[Any], types: Any) -> list[Any]:
    """Gemini wants alternating turns: fold consecutive same-role contents together."""
    merged: list[Any] = []
    for c in contents:
        if merged and merged[-1].role == c.role:
            merged[-1] = types.Content(
                role=c.role, parts=[*(merged[-1].parts or []), *(c.parts or [])]
            )
        else:
            merged.append(c)
    return merged


def to_contents(messages: list[Message], types: Any) -> list[Any]:
    contents: list[Any] = []
    for m in messages:
        if m.role == Role.ASSISTANT and m.raw_provider == "gemini" and m.raw is not None:
            contents.append(m.raw)  # keeps thought signatures intact
            continue
        parts: list[Any] = []
        for p in m.parts:
            if isinstance(p, TextPart):
                if p.text:
                    parts.append(types.Part(text=p.text))
            elif isinstance(p, ToolCall):
                call = types.FunctionCall(id=_wire_id(p.id), name=p.name, args=p.arguments)
                parts.append(types.Part(function_call=call))
            elif isinstance(p, ToolResult):
                payload = {"error": p.content} if p.is_error else {"result": p.content}
                resp = types.FunctionResponse(id=_wire_id(p.call_id), name=p.name, response=payload)
                parts.append(types.Part(function_response=resp))
        if not parts:
            parts = [types.Part(text="(continue)")]
        contents.append(
            types.Content(role="model" if m.role == Role.ASSISTANT else "user", parts=parts)
        )
    return _merge_same_role(contents, types)


def from_response(resp: Any, turn: int) -> Completion:
    candidates = getattr(resp, "candidates", None) or []
    if not candidates or candidates[0].content is None:
        reason = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
        raise ProviderError(
            f"Gemini returned no content (block_reason={reason})", retryable=reason is None
        )
    content = candidates[0].content
    parts: list[TextPart | ToolCall | ToolResult] = []
    for i, part in enumerate(content.parts or []):
        if getattr(part, "thought", False):
            continue
        fc = getattr(part, "function_call", None)
        if fc is not None:
            parts.append(
                ToolCall(fc.id or f"{SYNTHETIC_ID}{turn}-{i}", fc.name, dict(fc.args or {}))
            )
        elif getattr(part, "text", None):
            parts.append(TextPart(part.text))
    u = getattr(resp, "usage_metadata", None)
    usage = Usage(
        input_tokens=getattr(u, "prompt_token_count", 0) or 0,
        output_tokens=(getattr(u, "candidates_token_count", 0) or 0)
        + (getattr(u, "thoughts_token_count", 0) or 0),
        cache_read_tokens=getattr(u, "cached_content_token_count", 0) or 0,
    )
    finish = str(getattr(candidates[0], "finish_reason", "") or "")
    stop: StopReason = (
        "tool_use"
        if any(isinstance(p, ToolCall) for p in parts)
        else ("max_tokens" if "MAX_TOKENS" in finish else "end_turn")
    )
    return Completion(
        Message(Role.ASSISTANT, parts, raw=content, raw_provider="gemini"),
        usage,
        stop,
        getattr(resp, "model_version", "") or "",
    )


class GeminiProvider:
    name = "gemini"
    supports_native_tools = True
    allows_history_edits = True

    def __init__(self, cfg: LLMConfig, model: str, api_key: str | None) -> None:
        from google import genai
        from google.genai import types

        self.types = types
        self.cfg = cfg
        self.model = model
        http_options = None
        if cfg.base_url or cfg.extra_headers:
            http_options = types.HttpOptions(
                base_url=cfg.base_url, headers=cfg.extra_headers or None
            )
        self.client = genai.Client(api_key=api_key, http_options=http_options)
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
        from google.genai import errors

        t = self.types
        config: dict[str, Any] = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
            "automatic_function_calling": t.AutomaticFunctionCallingConfig(disable=True),
        }
        if temperature is not None:
            config["temperature"] = temperature
        if tools:
            config["tools"] = [
                t.Tool(
                    function_declarations=[
                        t.FunctionDeclaration(
                            name=s.name,
                            description=s.description,
                            parameters_json_schema=s.parameters,
                        )
                        for s in tools
                    ]
                )
            ]
        self._turn += 1

        def call() -> Any:
            try:
                return self.client.models.generate_content(
                    model=self.model,
                    contents=to_contents(messages, t),
                    config=t.GenerateContentConfig(**config),
                )
            except errors.APIError as e:
                code = getattr(e, "code", None)
                raise ProviderError(
                    f"Gemini API error {code}: {e}", retryable=is_retryable_status(code)
                ) from e

        return from_response(with_retry(call), self._turn)
