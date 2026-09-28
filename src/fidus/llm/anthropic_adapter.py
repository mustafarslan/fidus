"""Anthropic (Claude) adapter over the official `anthropic` SDK."""

from __future__ import annotations

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

# Models that accept the server-side `fallbacks: "default"` refusal fallback.
FALLBACK_MODELS = ("claude-opus-5", "claude-opus-5-5", "claude-fable-5-1")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Blocks we never echo back from before a mid-output fallback boundary.
_PRE_FALLBACK_DROP = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}


def _block_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    data: dict[str, Any] = block.model_dump(mode="json", exclude_none=True)
    return data


def to_request_messages(
    messages: list[Message], provider_name: str = "anthropic"
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == Role.ASSISTANT and m.raw_provider == provider_name and m.raw is not None:
            out.append({"role": "assistant", "content": m.raw})
            continue
        content: list[dict[str, Any]] = []
        for p in m.parts:
            if isinstance(p, TextPart):
                if p.text:
                    content.append({"type": "text", "text": p.text})
            elif isinstance(p, ToolCall):
                content.append(
                    {"type": "tool_use", "id": p.id, "name": p.name, "input": p.arguments}
                )
            elif isinstance(p, ToolResult):
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": p.call_id,
                        "content": p.content or "(empty)",
                        "is_error": p.is_error,
                    }
                )
        if not content:
            content = [{"type": "text", "text": "(continue)"}]
        out.append({"role": m.role.value, "content": content})
    return out


def to_request_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
    ]


def _echo_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the fallback echo rules: drop model-internal blocks before the last `fallback`
    boundary, and the `fallback` audit markers themselves."""
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    out = []
    for i, b in enumerate(blocks):
        t = b.get("type")
        if t == "fallback":
            continue
        if i < last and t != "text":
            continue
        out.append(b)
    return out


def from_response(resp: Any) -> Completion:
    stop = getattr(resp, "stop_reason", None)
    if stop == "refusal":
        details = getattr(resp, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise ProviderError(
            f"model declined the request (refusal, category={category})", retryable=False
        )
    blocks = [_block_dict(b) for b in resp.content]
    echo = _echo_blocks(blocks)
    parts: list[TextPart | ToolCall | ToolResult] = []
    for b in echo:
        if b.get("type") == "text" and b.get("text"):
            parts.append(TextPart(b["text"]))
        elif b.get("type") == "tool_use":
            args = b.get("input") if isinstance(b.get("input"), dict) else {}
            parts.append(ToolCall(b["id"], b["name"], args or {}))
    u = resp.usage
    usage = Usage(
        input_tokens=(getattr(u, "input_tokens", 0) or 0)
        + (getattr(u, "cache_read_input_tokens", 0) or 0)
        + (getattr(u, "cache_creation_input_tokens", 0) or 0),
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
    mapped: StopReason = {"tool_use": "tool_use", "max_tokens": "max_tokens"}.get(
        stop or "", "end_turn"
    )  # type: ignore[assignment]
    return Completion(
        Message(Role.ASSISTANT, parts, raw=echo, raw_provider="anthropic"),
        usage,
        mapped,
        getattr(resp, "model", ""),
    )


class AnthropicProvider:
    name = "anthropic"
    supports_native_tools = True
    # Recent Claude models bind thinking blocks to an unedited history; never rewrite turns.
    allows_history_edits = False

    def __init__(self, cfg: LLMConfig, model: str, api_key: str | None) -> None:
        import anthropic

        self._anthropic = anthropic
        self.cfg = cfg
        self.model = model
        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 4}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        if cfg.extra_headers:
            kwargs["default_headers"] = cfg.extra_headers
        self.client = anthropic.Anthropic(**kwargs)

    def _use_fallbacks(self) -> bool:
        return self.cfg.fallbacks and self.model in FALLBACK_MODELS

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        temperature: float | None,  # not sent: current Claude models reject sampling params
    ) -> Completion:
        a = self._anthropic
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": to_request_messages(messages),
        }
        if tools:
            params["tools"] = to_request_tools(tools)
        if self.cfg.prompt_cache:
            params["cache_control"] = {"type": "ephemeral"}
        if self.cfg.effort:
            params["output_config"] = {"effort": self.cfg.effort}
        try:
            if self._use_fallbacks():
                resp = self.client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **params
                )
            else:
                resp = self.client.messages.create(**params)
        except a.RateLimitError as e:
            raise ProviderError(f"rate limited: {e}", retryable=True) from e
        except a.APIStatusError as e:
            raise ProviderError(
                f"API error {e.status_code}: {e.message}", retryable=e.status_code >= 500
            ) from e
        except a.APIConnectionError as e:
            raise ProviderError(f"connection error: {e}", retryable=True) from e
        return from_response(resp)
