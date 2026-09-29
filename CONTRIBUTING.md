# Contributing to Fidus

Thanks for helping! Issues and pull requests are welcome.

## Development setup

```bash
git clone https://github.com/mustafarslan/fidus && cd fidus
uv sync --all-extras
uv run pytest            # the whole suite runs offline in a few seconds
uv run ruff check && uv run ruff format --check && uv run mypy
```

## Layout

| Path | What lives there |
|---|---|
| `src/fidus/config/` | `fidus.yaml` and outline schemas (pydantic) |
| `src/fidus/agent/` | the tool-use loop, tools, sandbox guards, doc validation, prompts |
| `src/fidus/llm/` | provider-neutral types and the Anthropic, Gemini, OpenAI-compatible, JSON-protocol and fake adapters |
| `src/fidus/sources/` | turning merged PRs and commits into triggers; cloning |
| `src/fidus/mapping/` | files → chapters (globs, then LLM triage) and work planning |
| `src/fidus/pipeline/` | `run`, `bootstrap`, `init` orchestration |
| `src/fidus/publish/` | rolling-branch lifecycle, PR body rendering, publishing |
| `src/fidus/prompts/` | prompt templates (Markdown, `$placeholders`) |
| `tests/integration/test_github_cycle.py` | simulated nights against a real bare git remote and a fake GitHub API |

The design and its rationale are in [docs/design.md](docs/design.md).

## Guidelines

- Keep modules small and single-purpose. Add tests with every behaviour change; the multi-night
  cycle test is the best place for anything that touches branches, state or PRs.
- Tests never hit the network. Use `FakeProvider` (scripted) or `HeuristicFakeProvider`, `respx`
  for GitHub, and temporary git repos.
- **Adding a provider:** implement the `Provider` protocol in `src/fidus/llm/`. Keep
  `to_request`/`from_response` as pure functions, add fixture tests in `tests/llm/`, and register
  the provider in `llm/base.py:make_provider`.
- **Dependabot action bumps:** `tests/unit/test_templates.py` fails when the repo's workflows use a
  newer `actions/*` major than the workflow users copy. Update
  `src/fidus/templates/workflow.yml` (then copy it to `examples/workflows/fidus.yml`), the README
  and `docs/github-app.md` in the same PR.
- **Changing prompts:** they shape every user's docs. Explain the motivation in the PR and include
  before/after chapter excerpts from a real run.
