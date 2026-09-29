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

### Improvements since the first merge

- **Briefs name existing problems:** episodes are told about validation problems already in a
  chapter (stale line-number citations, broken links) and fix them, even when nothing else
  changes (#7).
- **`init` closes coverage gaps:** one repair round runs when chapter globs cover less than
  `limits.init_coverage_target` of the files. Live, coverage went from 36% to 97% (#8).
- **Retries keep their context:** retried and rebuilt changes keep their changed-file lists (#9).
- **Workflow template stays current:** the template users copy is kept in sync with the repo's
  action versions, enforced by a test (#10).
- **`fidus doctor` checks access:** it verifies push access to the docs repo and that the GitHub
  App is installed on every repository (#11).
- **Atomic writes:** chapters, the TOC, state and the outline are written atomically (#17).
- **Local runs restore your branch:** local runs return the clone to the branch it started on
  (#18).
- **Outline comments survive:** outline proposal PRs keep the comments and formatting in
  `fidus.outline.yaml` (#19).
- **Failing chapters stop retrying:** a chapter that fails `limits.max_retry_attempts` times is
  quarantined and flagged "Needs a human" in the PR (#20).
- **Prerequisites first:** chapters run after their prerequisites in the same run (#21).
- **Glob suggestions:** the PR suggests copy-pasteable outline globs for uncovered files (#22).
- **Triage cache:** triage decisions are cached across runs and reset when the outline changes
  (#23). They are kept on a separate `fidus/cache` branch, so quiet nights that open no PR keep
  them too (#35).
- **`fidus state clear-retry`:** drop retry entries or lift a quarantine (#36).
- **`tool_protocol: auto` is the default:** a turn is retried through the JSON protocol when a
  server drops a tool call.
