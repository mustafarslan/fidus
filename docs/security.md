# Security model

Fidus runs an LLM agent over code you may not fully control: PR descriptions, commit messages
and source comments are all attacker-influenceable. The design assumes the model **can** be
manipulated by that text and limits what a manipulated model can do.

## What the agent can do

| Capability | Scope |
|---|---|
| Read files | Only inside the fresh clones of configured sources. Paths are resolved and must stay inside the clone; `..`, absolute paths and escaping symlinks are rejected; binary files are skipped; reads are size-capped. |
| Read docs | Only inside `docs.dir`. |
| Write | **Exactly one file**: the chapter the episode was assigned. The tool takes no path argument. The target is re-validated: it must be under `docs.dir`, contain no symlinked components, no hidden segments (`.git`, `.github`, `.fidus`, ...), end in `.md`, and not be the config or outline. |
| Shell or network | None. |

Everything the agent reads from PRs, diffs and files is wrapped in
`<untrusted_data source="...">` blocks, and the system prompt tells the model that such content
is data, never instructions.

## What gets committed

- The files changed by the run: chapter files, the generated `docs/README.md` index, and
  `.fidus/state.json`.
- Before any chapter is written, it is scanned for secrets: GitHub, Anthropic, OpenAI, Google and
  AWS key patterns, PEM private keys, and the literal values of the API key and token environment
  variables. A match is rejected, and the model is asked to remove it.
- Commits go to `fidus/*` branches only. Fidus never pushes to your default branch.

## The workflow

- It runs on `schedule` and `workflow_dispatch` only. There is **no `pull_request` trigger**, so
  code from forks never runs with your secrets.
- The job's `GITHUB_TOKEN` has only `contents: read`. Writes use the short-lived App installation
  token, which expires within an hour.
- Tokens are passed to git through an HTTP header (`http.extraheader`), never written to
  `.git/config`, and redacted from all logs.

## The human boundary

Nothing Fidus writes reaches your default branch until a person merges the PR. Review Fidus's PRs
the way you'd review a new contributor's: the PR body lists every chapter changed and why.

## Reporting a vulnerability

Please open a private security advisory on the GitHub repository rather than a public issue.
