# Changelog

## 0.1.0 (unreleased)

The first release.

- **Commands:** `fidus init`, `validate`, `bootstrap`, `run`, `doctor`, `state`.
- **Nightly sync:** merged PRs are mapped to chapters by outline globs, with LLM triage as a
  fallback, and each affected chapter gets its own agent episode.
- **Weekly audit** of every chapter, with optional rotation and structure proposals.
- **One rolling PR** (`fidus/sync`), with state carried on the branch, a rebuild on conflict,
  a pause on human edits, and no PR on quiet nights.
- **Providers:** Anthropic (with prompt caching and refusal fallbacks), Gemini, any
  OpenAI-compatible server, and a JSON tool protocol for models without native tool calling.
- **Safeguards:** a sandbox for the agent's reads and writes, secret scanning on every write,
  `fidus:keep` blocks for human-owned prose, and doc validation (frontmatter, links, citations).
- **Distribution:** a composite GitHub Action (`mustafarslan/fidus@v1`) and a Dockerfile.
