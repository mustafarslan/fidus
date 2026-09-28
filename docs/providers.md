# LLM providers

Fidus talks to models through small adapters that share one internal message format. Install
only the extra you need:

| Provider | `llm.provider` | Install | Key env var (default) |
|---|---|---|---|
| Anthropic Claude | `anthropic` | `pip install 'fidus[anthropic]'` | `ANTHROPIC_API_KEY` |
| Google Gemini | `gemini` | `pip install 'fidus[gemini]'` | `GEMINI_API_KEY` |
| OpenAI and any OpenAI-compatible server | `openai` | `pip install 'fidus[openai]'` | `OPENAI_API_KEY` |
| Offline heuristic (tests, demos) | `fake` | none | none |

The GitHub Action installs all of them (`extras: all`).

## Anthropic

```yaml
llm:
  provider: anthropic
  model: claude-opus-5      # or claude-sonnet-5, claude-haiku-4-5, ...
  effort: high              # optional: low | medium | high | xhigh | max
  prompt_cache: true        # caches the system prompt and tools across turns
  fallbacks: true           # server-side refusal fallback on models that support it
```

- **No sampling parameters.** Current Claude models reject `temperature`, so Fidus never sends it
  to Anthropic.
- **Thinking.** Adaptive thinking is on by default on recent models. Fidus passes thinking
  blocks back unchanged between turns and never rewrites earlier turns in a conversation.
- **Refusal fallbacks.** With `fallbacks: true` on Claude Opus 5, Opus 5.5 and Fable 5.1, a
  request declined by a safety classifier is re-run server-side on Anthropic's recommended
  fallback model, using `fallbacks: "default"`.

## Gemini

```yaml
llm: { provider: gemini, model: gemini-3.8-flash }
```

Thought signatures are preserved across turns automatically.

## OpenAI and OpenAI-compatible servers

```yaml
llm: { provider: openai, model: gpt-6-sol }
```

Any server that implements the Chat Completions API works through `base_url`:

| Server | `base_url` | Notes |
|---|---|---|
| Ollama | `http://localhost:11434/v1` | no key needed |
| vLLM | `http://host:8000/v1` | start with `--enable-auto-tool-choice --tool-call-parser <parser>` for native tools |
| LM Studio | `http://localhost:1234/v1` | |
| OpenRouter | `https://openrouter.ai/api/v1` | `api_key_env: OPENROUTER_API_KEY`; optional `extra_headers: {HTTP-Referer: ...}` |
| Together | `https://api.together.xyz/v1` | `api_key_env: TOGETHER_API_KEY` |
| Groq, Fireworks, DeepInfra, Azure OpenAI, ... | their OpenAI-compatible URL | |

When `base_url` is set, Fidus sends `max_tokens` rather than `max_completion_tokens`, because
compatible servers expect the older name.

### Models without native tool calling

The default `tool_protocol: auto` switches to the JSON protocol automatically the first time a server returns an empty reply, which usually means it dropped a tool call it couldn't parse. Set `tool_protocol: json` to use it from the start. Tools are then described in the prompt, and the model replies with a
single JSON object such as `{"tool": "read_file", "arguments": {...}}`. Fidus tolerates code
fences and surrounding prose, and asks the model to repair malformed replies up to twice. This
works with essentially any instruction-tuned model, but larger models write noticeably better
chapters.

Run `fidus doctor --ping` to check whether your model/server combination handles native tool
calls.

## Picking a model

Chapter writing is long-context, tool-heavy work, and quality scales with model capability.
Use a frontier model for `bootstrap`, which writes every chapter. The nightly syncs make small,
targeted edits. If cost matters, try a cheaper model there, keeping `triage_model` cheaper
still, and compare a few PRs side by side.
