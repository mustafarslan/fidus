# Configuration reference

Fidus reads `fidus.yaml` from the root of your docs repository; pass `--config` to use another
path. Unknown keys are rejected, so typos fail loudly. Run `fidus validate` after every edit.

[`examples/fidus.yaml`](../examples/fidus.yaml) lists **every key with its default and a
comment**. It is the authoritative reference, and this page explains the parts that need more
context.

## `sources`

Each source has **exactly one** of:
- `repo: owner/name`: cloned from GitHub (depth 1) on each run. Its merged PRs are the triggers.
- `path: ../dir`: a local checkout, relative to `fidus.yaml`. Its first-parent commits are the
  triggers. Useful for local runs and for monorepos checked out next to the docs.

`alias` defaults to the repository name (lower-cased). It prefixes outline globs
(`api:src/**`) and citations in chapters (`` `api:src/auth/jwt.py` ``), so keep it short and
stable.

`include` and `exclude` are gitignore-style globs. They filter both which changed files count as
triggers and which files the coverage report considers. Lockfiles, minified bundles, images,
`vendor/` and `node_modules/` are always excluded.

## `llm`

See [providers.md](providers.md).

## `schedule.audit`

`fidus run --mode auto`, the default, adds an audit when `every_days` have passed since the last
one. The audit runs one episode per chapter that re-verifies every claim against the current
code. For large books, `chapters_per_run: N` rotates through the book N chapters at a time.

`structure_proposals` controls what happens to the audit's ideas for restructuring:
- `note` lists them in the sync PR.
- `pr` opens a separate `fidus/outline` PR that edits only `fidus.outline.yaml`.
- `off` ignores them.

## `sync`

`noop_policy` decides what happens when changes were merged but none of them affected the docs:
- `defer`, the default, opens no PR. The changes are reconsidered next night and show up as
  "no documentation impact" rows in the next PR that does open. There is one exception: if a
  source filled its whole `max_prs_per_run` window, Fidus opens a state-only PR anyway.
  Otherwise the cursor could never get past a long run of no-impact PRs.
- `state_pr` opens a PR anyway, so the cursor advances.

`include_pr_body: false` hides PR descriptions from the agent. Use it if your PR descriptions are
noisy or untrusted; the agent still sees titles, file lists and diffs.

## `limits` worth knowing

- **`init_coverage_target`** (default `0.9`): after `fidus init` proposes an outline, if its
  chapter globs cover a smaller share of the (filtered) source files, the agent gets one repair
  round that lists the uncovered locations.
- **`max_retry_attempts`** (default `3`): chapters that fail (errors, protocol errors, running out
  of budget) are retried on later nights, even after you merge. After this many failures a
  chapter is quarantined, no longer retried automatically, and shown as **Needs a human** in the
  PR. Any later successful update of the chapter clears it.
- **`triage_confidence_threshold`** (default `0.6`): triage decisions below this confidence are
  ignored. Decisions above it are cached in `.fidus/state.json` (from the first run that opens a
  PR onward) until the outline changes.

## `budgets`

Every model call counts against three budgets: the **episode** budget (turns and tokens per
chapter), the **run** budget (total tokens per night), and a per-call `max_output_tokens`. At 80%
of the episode budget the agent is told to finish up. A chapter that runs out of budget keeps its
partial write, which is flagged in the PR, or is retried the next night. The same happens to items
that don't fit in `run.max_episodes`.

## Environment variables

| Variable | Purpose |
|---|---|
| `FIDUS_GITHUB_TOKEN` (or `GH_TOKEN`, `GITHUB_TOKEN`) | GitHub token (App installation token or PAT) |
| `FIDUS_DOCS_REPO` | `owner/name` of the docs repo when not running in Actions |
| `FIDUS_GIT_USER_NAME`, `FIDUS_GIT_USER_EMAIL` | commit author (the action sets these to the App bot) |
| `FIDUS_CACHE_DIR` | where source clones go (default: `$RUNNER_TEMP` or the system temp directory) |
| the env var named by `llm.api_key_env` | the LLM API key |
