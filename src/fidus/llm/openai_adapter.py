"""OpenAI-compatible adapter (Chat Completions): OpenAI, Ollama, vLLM, OpenRouter, Together, ..."""

from __future__ import annotations

import json
from typing import Any

from fidus.config.models import LLMConfig
from fidus.errors import ProviderError
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

BAD_ARGS = "__fidus_bad_json__"


def to_request_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == Role.ASSISTANT:
            text = "".join(p.text for p in m.parts if isinstance(p, TextPart))
            calls = [p for p in m.parts if isinstance(p, ToolCall)]
            # content may only be null when tool_calls are present
            msg: dict[str, Any] = {"role": "assistant", "content": text or (None if calls else "")}
            if calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in calls
                ]
            out.append(msg)
            continue
        texts: list[str] = []
        for p in m.parts:
            if isinstance(p, ToolResult):
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": p.call_id,
                        "content": ("ERROR: " if p.is_error else "") + (p.content or "(empty)"),
                    }
                )
            elif isinstance(p, TextPart) and p.text:
                texts.append(p.text)
        if texts:
            out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def to_request_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


def from_response(resp: Any) -> Completion:
    if not resp.choices:
        raise ProviderError("empty response (no choices)", retryable=True)
    choice = resp.choices[0]
    msg = choice.message
    parts: list[TextPart | ToolCall | ToolResult] = []
    if getattr(msg, "content", None):
        parts.append(TextPart(msg.content))
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = tc.function
        try:
            args = json.loads(fn.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            # Surfaced to the model as a tool error by the ToolSet (unknown arg).
            args = {BAD_ARGS: f"could not parse arguments as JSON ({e}): {str(fn.arguments)[:200]}"}
        parts.append(ToolCall(tc.id or f"call_{len(parts)}", fn.name, args))
    u = getattr(resp, "usage", None)
    details = getattr(u, "prompt_tokens_details", None)
    usage = Usage(
        input_tokens=getattr(u, "prompt_tokens", 0) or 0,
        output_tokens=getattr(u, "completion_tokens", 0) or 0,
        cache_read_tokens=getattr(details, "cached_tokens", 0) or 0 if details else 0,
    )
    finish = getattr(choice, "finish_reason", None)
    stop: StopReason = {"tool_calls": "tool_use", "length": "max_tokens"}.get(
        finish or "", "end_turn"
    )  # type: ignore[assignment]
    return Completion(Message(Role.ASSISTANT, parts), usage, stop, getattr(resp, "model", "") or "")


class OpenAIProvider:
    name = "openai"
    supports_native_tools = True
    allows_history_edits = True

    def __init__(self, cfg: LLMConfig, model: str, api_key: str | None) -> None:
        import openai

        self._openai = openai
        self.cfg = cfg
        self.model = model
        self.client = openai.OpenAI(
            api_key=api_key or "not-needed",
            base_url=cfg.base_url,
            default_headers=cfg.extra_headers or None,
            max_retries=4,
            timeout=600,
        )

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        temperature: float | None,
    ) -> Completion:
        o = self._openai
        params: dict[str, Any] = {
            "model": self.model,
            "messages": to_request_messages(system, messages),
            "max_completion_tokens": max_tokens,
        }
        if self.cfg.base_url:  # most compatible servers still expect max_tokens
            params["max_tokens"] = params.pop("max_completion_tokens")
        if temperature is not None:
            params["temperature"] = temperature
        if tools:
            params["tools"] = to_request_tools(tools)
        try:
            resp = self.client.chat.completions.create(**params)
        except o.RateLimitError as e:
            raise ProviderError(f"rate limited: {e}", retryable=True) from e
        except o.APIStatusError as e:
            raise ProviderError(
                f"API error {e.status_code}: {e.message}", retryable=e.status_code >= 500
            ) from e
        except o.APIConnectionError as e:
            raise ProviderError(
                f"connection error to {self.cfg.base_url or 'api.openai.com'}: {e}", retryable=True
            ) from e
        return from_response(resp)
