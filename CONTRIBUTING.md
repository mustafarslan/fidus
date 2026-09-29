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
  before/after chapter excerpts from a real run, plus accuracy benchmark numbers:

  ```bash
  fidus bootstrap -c path/to/fidus.yaml --dry-run --output out/   # before and after your change
  python -m fidus.bench.accuracy out/docs -c path/to/fidus.yaml [--provider P --model M]
  # save accuracy.json from the "before" run, then compare the "after" run against it:
  python -m fidus.bench.accuracy out2/docs -c path/to/fidus.yaml --baseline before.json --max-drop 0.05
  ```

  - **What it does:** the benchmark asks a judge model to check each chapter's claims against the
    source files it cites.
  - **Evidence check:** supported and contradicted verdicts must quote verbatim source evidence,
    which is checked mechanically.
  - **Output:** it writes `accuracy.json` with a score and an error rate.
  - **Choose the judge carefully:** use one at least as strong as the writer. In our tests a weak
    judge scored gemma4's book 97% before strict evidence checking and 87% after (catching a
    `checkout` vs `checkout_new` error). It still misses invented details inside hypothetical
    "example scenarios".
