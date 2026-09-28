"""Provider protocol and factory."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from fidus.config.models import LLMConfig
from fidus.errors import ConfigError
from fidus.llm.types import Completion, Message, ToolSpec
from fidus.log import register_secret


@runtime_checkable
class Provider(Protocol):
    name: str
    model: str
    supports_native_tools: bool
    allows_history_edits: bool  # False: never rewrite earlier turns (e.g. preserved thinking)

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        temperature: float | None,
    ) -> Completion: ...


def resolve_api_key(cfg: LLMConfig, *, required: bool = True) -> str | None:
    env = cfg.resolved_api_key_env
    key = os.environ.get(env) if env else None
    if key:
        register_secret(key)
        return key
    if required:
        raise ConfigError(
            f"LLM API key not found: set the {env} environment variable "
            f"(or change llm.api_key_env in fidus.yaml)"
        )
    return None


def make_provider(cfg: LLMConfig, *, model: str | None = None) -> Provider:
    """Build the configured provider, wrapped in the JSON tool protocol when needed."""
    model = model or cfg.model
    provider: Provider
    if cfg.provider == "fake":
        from fidus.llm.fake import HeuristicFakeProvider

        return HeuristicFakeProvider()
    if cfg.provider == "anthropic":
        from fidus.llm.anthropic_adapter import AnthropicProvider

        provider = AnthropicProvider(cfg, model, resolve_api_key(cfg))
    elif cfg.provider == "gemini":
        from fidus.llm.gemini_adapter import GeminiProvider

        provider = GeminiProvider(cfg, model, resolve_api_key(cfg))
    elif cfg.provider == "openai":
        from fidus.llm.openai_adapter import OpenAIProvider

        # Local servers (Ollama, vLLM) usually need no key.
        key = resolve_api_key(cfg, required=cfg.base_url is None)
        provider = OpenAIProvider(cfg, model, key)
    else:  # pragma: no cover - Literal guards this
        raise ConfigError(f"unknown provider {cfg.provider}")

    from fidus.llm.json_protocol import AutoToolProvider, JsonToolProvider

    if cfg.tool_protocol == "json" or not provider.supports_native_tools:
        return JsonToolProvider(provider)
    if cfg.tool_protocol == "auto":
        return AutoToolProvider(provider)
    return provider
