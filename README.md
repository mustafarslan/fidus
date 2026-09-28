# Fidus

**An autonomous documentation agent that keeps a textbook-style docs repository in sync with your codebases, one reviewed pull request per day.**

Fidus watches one or more source repositories. Every night it reads the pull requests that were merged, works out which chapters of your documentation they affect, rewrites those chapters so they match the latest code, and opens a single pull request against your docs repository. A human reviews that PR and merges it. Once a week it also audits every chapter against the current code to catch drift.

The documentation reads like a **computer-science textbook**. It is organised into Parts and Chapters that start with fundamentals and build up to advanced topics, all in plain Markdown. Fidus works with **Anthropic Claude**, **Google Gemini**, or **any OpenAI-compatible endpoint**. That last option covers open-weight models served by Ollama, vLLM, OpenRouter, Together and others. You bring your own API key.

```
 source repos (1..N)                         docs repo
┌────────────────────┐   nightly 00:00 UTC   ┌──────────────────────────────────────┐
│ acme/api   PR #412 ─┼──┐                    │ docs/                                │
│ acme/web   PR #88  ─┼──┼─► Fidus agent ───► │   part-1-foundations/01-overview.md  │
│ acme/infra PR #9   ─┼──┘   (your LLM)       │   part-2-core/01-auth.md   ◄─ edited │
└────────────────────┘                        │ .fidus/state.json                    │
                                              └──────────┬───────────────────────────┘
                                                         ▼
                                  PR "docs: Fidus nightly sync"  →  human review  →  merge
```

## Contents

- [How it works](#how-it-works)
- [Setup](#setup) (about 15 minutes)
  1. [Create a docs repository](#1-create-a-docs-repository)
  2. [Create the GitHub App](#2-create-the-github-app)
  3. [Install Fidus and generate the outline](#3-install-fidus-and-generate-the-outline)
  4. [Bootstrap the book](#4-bootstrap-the-book)
  5. [Add the nightly workflow](#5-add-the-nightly-workflow)
- [Quick start with a personal access token](#quick-start-with-a-personal-access-token-instead-of-an-app)
- [Choosing an LLM provider](#choosing-an-llm-provider)
- [Configuration](#configuration)
- [The outline](#the-outline-your-books-table-of-contents)
- [Daily review workflow](#daily-review-workflow)
- [Commands](#commands)
- [Running locally and dry runs](#running-locally-and-dry-runs)
- [Operations and FAQ](#operations-and-faq)
- [Security model](#security-model)
- [Contributing](#contributing)

## How it works

1. **Collect.** For each source repository, Fidus lists the PRs merged since its last run, reading their files and diffs from the GitHub API. For local sources it lists commits instead. Lockfiles, build output and files you exclude are filtered out.
2. **Map.** Each changed file is matched against the `sources` globs of the chapters in your outline, so `api:src/auth/**` maps to the *Authentication* chapter. Files that no glob covers go to a single LLM triage call. Anything it can't place is listed for you in the PR, never silently dropped.
3. **Write.** Each affected chapter gets its own **agent episode**. The agent reads the triggering PRs and explores a fresh clone of the latest code with `read_file`, `search_code` and `list_files`. It then edits the chapter with the **smallest correct change**, citing source files as `alias:path`. It cannot write anything except its own chapter file.
4. **Check.** Every write is validated: the frontmatter must be present, links to other chapters must resolve, cited files must exist, and human-protected `<!-- fidus:keep -->` blocks must be untouched. If a fundamentals chapter renames a concept, the chapters that build on it get a short consistency pass.
5. **Audit.** Once a week (you can change this), every chapter is re-verified against the current code. Ideas for restructuring the book are reported to you, never applied automatically.
6. **Publish.** Everything goes into **one rolling PR** on the branch `fidus/sync`. The PR description is a table: chapter, the PR that triggered the change, and a one-line reason. If you don't merge for a few days, later changes accumulate in the same PR. Nights with nothing to do produce no PR at all.

Progress is stored in `.fidus/state.json` on the PR branch, so **the cursor only advances when you merge**. Closing a PR without merging it means "skip these changes". Chapters that fail, run out of budget or don't fit into a night are remembered and retried, even if you merge in the meantime.

## Setup

You need:
- the source repositories you want documented
- an empty (or existing) **docs repository**
- permission to create a **GitHub App** in your organisation or on your account
- an LLM API key

Python 3.11 or newer is needed only for the one-time local steps.

### 1. Create a docs repository

Create a repository such as `acme/docs`. Fidus writes Markdown under `docs/` and keeps its configuration (`fidus.yaml`, `fidus.outline.yaml`) at the repository root. The Markdown works with any static-site generator (MkDocs, Docusaurus, VitePress, mdBook) or with GitHub's own rendering.

### 2. Create the GitHub App

The App gives Fidus its own identity, so its PRs come from `your-app[bot]` rather than a person, and it lets Fidus read your *other* private repositories.

1. Open the page for a new GitHub App:
   - For an **organisation**: *Settings → Developer settings → GitHub Apps → New GitHub App*, or `https://github.com/organizations/<org>/settings/apps/new`
   - For a **personal account**: `https://github.com/settings/apps/new`
2. Fill in the form:
   - **GitHub App name**: for example `acme-fidus`. It must be unique on GitHub.
   - **Homepage URL**: anything, for example your docs repo URL.
   - **Webhook**: **uncheck "Active"**. Fidus runs on a schedule and needs no webhooks or server.
3. Under **Repository permissions**, set:

   | Permission | Access | Why |
   |---|---|---|
   | **Contents** | Read and write | Read the source code; push the `fidus/*` branches to the docs repo |
   | **Pull requests** | Read and write | List merged PRs in the sources; open and update the docs PR |
   | **Metadata** | Read-only | Mandatory for every App |

   Leave everything else at *No access*. Fidus only ever **reads** the source repositories and never pushes to their branches. If you want the App itself to be unable to write to the source repos, see [two-App setup](docs/github-app.md#least-privilege-two-app-setup).
4. **Where can this GitHub App be installed?** Choose *Only on this account*, then click **Create GitHub App**.
5. On the App's page, note the **Client ID**. Then scroll to *Private keys* and click **Generate a private key**. A `.pem` file downloads.
6. Click **Install App** in the sidebar and install it on your account or organisation. Choose **Only select repositories**, and pick **every source repository and the docs repository**. If a repository is missing here, Fidus can't read it.
7. In the **docs repository**, go to *Settings → Secrets and variables → Actions* and add:

   | Kind | Name | Value |
   |---|---|---|
   | Variable | `FIDUS_APP_CLIENT_ID` | the Client ID from step 5 |
   | Secret | `FIDUS_APP_PRIVATE_KEY` | the **entire** contents of the `.pem` file, including the `BEGIN` and `END` lines |
   | Secret | `ANTHROPIC_API_KEY` (or `GEMINI_API_KEY`, `OPENAI_API_KEY`, …) | your LLM key |

> **Why not the built-in `GITHUB_TOKEN`?** `GITHUB_TOKEN` only covers the repository the workflow runs in, so it can't read your other private repositories. PRs it opens also don't trigger other workflows, which means your docs CI (link checks, preview deploys) wouldn't run on Fidus's PRs. The App token covers all of these.

If your sources live under a **different owner** than the docs repo, or you're on GitHub Enterprise Server, see [docs/github-app.md](docs/github-app.md).

### 3. Install Fidus and generate the outline

On your machine, in a clone of the docs repository:

```bash
pipx install 'fidus[anthropic]'      # or fidus[gemini], fidus[openai], fidus[all]
export ANTHROPIC_API_KEY=sk-ant-...
export FIDUS_GITHUB_TOKEN=$(gh auth token)   # lets `init` clone your private source repos

fidus init --source acme/api --source acme/web --write-workflow
```

`fidus init` does three things:
- It writes `fidus.yaml`, asking for your provider and model if you didn't pass them.
- It writes `.github/workflows/fidus.yml`.
- It has the agent study your repositories and **propose `fidus.outline.yaml`**: Parts and Chapters ordered from fundamentals to advanced, each chapter mapped to source globs.

**Now edit the outline.** It is the single source of truth for the book's structure. Rename, reorder, merge and split chapters, and fix the `sources` globs. Then check it:

```bash
fidus validate        # schema checks and a coverage report per source repo
```

### 4. Bootstrap the book

Commit and push `fidus.yaml` and `fidus.outline.yaml` first. Fidus always works from your pushed default branch, and it refuses to run on uncommitted changes. Then either run it locally:

```bash
git add fidus.yaml fidus.outline.yaml .github && git commit -m "Add Fidus" && git push
fidus bootstrap       # writes every chapter, opens a "Fidus bootstrap" PR (leaves your clone on fidus/bootstrap)
```

or commit `fidus.yaml`, `fidus.outline.yaml` and the workflow, and run the workflow once with `command: bootstrap` (see [below](#bootstrap-from-actions)). Review the bootstrap PR, edit anything you like, and merge it. Nightly syncs start from that point.

To preview without touching GitHub, run `fidus bootstrap --dry-run --output ./preview`.

### 5. Add the nightly workflow

`fidus init --write-workflow` already created `.github/workflows/fidus.yml`. A copy is in [`examples/workflows/fidus.yml`](examples/workflows/fidus.yml):

```yaml
name: Fidus nightly docs sync
on:
  schedule:
    - cron: "0 0 * * *"          # 00:00 UTC
  workflow_dispatch:
    inputs:
      mode: { type: choice, options: [auto, sync, audit, full], default: auto }
      dry-run: { type: boolean, default: false }

concurrency: { group: fidus-${{ github.repository }}, cancel-in-progress: false }
permissions: { contents: read }  # writes use the App token

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    steps:
      - id: app-token
        uses: actions/create-github-app-token@v3
        with:
          client-id: ${{ vars.FIDUS_APP_CLIENT_ID }}
          private-key: ${{ secrets.FIDUS_APP_PRIVATE_KEY }}
          owner: ${{ github.repository_owner }}   # one token for every repo the App is installed on
      - id: bot   # the App's bot user id, so commits are attributed to it
        env: { GH_TOKEN: "${{ steps.app-token.outputs.token }}", SLUG: "${{ steps.app-token.outputs.app-slug }}" }
        run: echo "id=$(gh api "/users/${SLUG}%5Bbot%5D" --jq .id)" >> "$GITHUB_OUTPUT"
      - uses: actions/checkout@v4
        with: { token: "${{ steps.app-token.outputs.token }}", fetch-depth: 0, persist-credentials: false }
      - uses: mustafarslan/fidus@v1
        with:
          github-token: ${{ steps.app-token.outputs.token }}
          mode: ${{ inputs.mode || 'auto' }}
          dry-run: ${{ inputs.dry-run || 'false' }}
          git-user-name: ${{ steps.app-token.outputs.app-slug }}[bot]
          git-user-email: ${{ steps.bot.outputs.id }}+${{ steps.app-token.outputs.app-slug }}[bot]@users.noreply.github.com
```

Commit it, then trigger it once by hand under *Actions → Fidus nightly docs sync → Run workflow*, with `dry-run` ticked, to check everything is wired up. The dry-run output is uploaded as an artifact.

#### Bootstrap from Actions

To bootstrap from CI instead of locally, add a temporary step, or a second `workflow_dispatch` workflow, that uses the action with `command: bootstrap`:

```yaml
      - uses: mustafarslan/fidus@v1
        with:
          command: bootstrap
          github-token: ${{ steps.app-token.outputs.token }}
```

## Quick start with a personal access token (instead of an App)

This is fine for trying Fidus out or for a personal project. Create a **fine-grained personal access token** with access to the source repositories and the docs repository, with *Contents: Read and write*, *Pull requests: Read and write* and *Metadata: Read*. Store it as the secret `FIDUS_GITHUB_TOKEN`, then replace the `create-github-app-token` step:

```yaml
      - uses: actions/checkout@v4
        with: { token: "${{ secrets.FIDUS_GITHUB_TOKEN }}", fetch-depth: 0 }
      - uses: mustafarslan/fidus@v1
        with:
          github-token: ${{ secrets.FIDUS_GITHUB_TOKEN }}
```

The drawbacks: PRs appear as *you*, the token expires, and it is tied to one person's access. Switch to the App for team use.

## Choosing an LLM provider

Set `llm` in `fidus.yaml` and put the key in the environment variable named by `api_key_env`. Keys are **never** stored in the YAML.

```yaml
# Anthropic Claude (default)
llm: { provider: anthropic, model: claude-opus-5, api_key_env: ANTHROPIC_API_KEY }

# Google Gemini
llm: { provider: gemini, model: gemini-3.8-flash, api_key_env: GEMINI_API_KEY }

# OpenAI
llm: { provider: openai, model: gpt-6-sol, api_key_env: OPENAI_API_KEY }

# Open-weight models through any OpenAI-compatible server
llm: { provider: openai, model: qwen3-coder, base_url: "http://localhost:11434/v1" }            # Ollama
llm: { provider: openai, model: meta-llama/Llama-4-Maverick, base_url: "http://gpu-box:8000/v1" } # vLLM
llm: { provider: openai, model: deepseek/deepseek-v3, base_url: "https://openrouter.ai/api/v1", api_key_env: OPENROUTER_API_KEY }
```

- **Models without reliable native tool calling.** Many small open-weight models fall in this group. The default `tool_protocol: auto` uses native tool calls. When a server returns an empty reply, which usually means it dropped a tool call it couldn't parse, Fidus retries that turn through a plain-JSON tool protocol that works with any chat model, and stays on it. Set `json` to use it from the start, or `native` to disable the fallback.
- **Cheaper triage.** `triage_model` can point the file-to-chapter triage call at a smaller, cheaper model.
- **Cost reporting.** Add `pricing: {input_per_mtok, output_per_mtok}` to show estimated dollar costs in the PR. Token counts are always shown.
- **Claude refusal fallbacks.** On Claude models that support them (Opus 5 and newer), Fidus turns on Anthropic's server-side refusal fallback (`fallbacks: "default"`). A request that a safety classifier declines is retried on Anthropic's recommended fallback model instead of failing the chapter. Disable it with `llm.fallbacks: false`.

More detail is in [docs/providers.md](docs/providers.md).

## Configuration

A complete, commented example is in [`examples/fidus.yaml`](examples/fidus.yaml), and every key is documented in [docs/configuration.md](docs/configuration.md). The essentials:

```yaml
version: 1
docs:
  dir: docs                       # where chapters live
  outline: fidus.outline.yaml
sources:                          # one or more
  - repo: acme/api
    alias: api                    # used in outline globs and citations: api:src/x.py
    include: ["src/**", "README.md"]
    exclude: ["**/tests/**"]
  - repo: acme/web
    alias: web
llm:
  provider: anthropic
  model: claude-opus-5
schedule:
  audit: { enabled: true, every_days: 7 }
sync:
  branch: fidus/sync
  labels: [documentation, fidus]
  reviewers: [alice]              # requested on each new PR
```

## The outline: your book's table of contents

```yaml
version: 1
title: "The Acme Platform: A Textbook"
parts:
  - id: foundations
    title: "Part I: Foundations"
    chapters:
      - id: overview
        title: What Acme Is and How It Is Built
        path: part-1-foundations/01-overview.md
        level: fundamentals              # fundamentals | intermediate | advanced
        summary: System context, main components, request lifecycle.
        sources: ["api:README.md", "api:src/app.py", "web:src/main.ts"]
      - id: auth
        title: Authentication and Sessions
        path: part-1-foundations/02-auth.md
        level: intermediate
        sources: ["api:src/auth/**", "web:src/lib/session.ts"]
        prerequisites: [overview]        # must be EARLIER chapters
```

Rules:
- **Only you change the structure.** Nightly runs never add, remove or reorder chapters.
- **New chapters flow in automatically.** Add a chapter to the outline, merge it, and the next nightly run writes it.
- **Removed chapters are reported, not deleted.** Their files are listed under *Needs attention*.
- **Audit suggestions stay suggestions.** Structure ideas from the audit appear in the PR description. With `schedule.audit.structure_proposals: pr`, they arrive as a separate `fidus/outline` PR instead.
- **Chapter numbers are computed.** Numbers like "2.1" come from each chapter's position, so reordering never breaks file paths.
- **The table of contents is generated.** `docs/README.md` is rebuilt from the outline on every run.

## Daily review workflow

- **One PR.** `docs: Fidus nightly sync` is updated in place every night until you merge it.
- **Merging** publishes the docs and advances Fidus's cursor.
- **Closing without merging** rejects the batch. Those source changes are skipped, and the next PR notes which ones. To have Fidus **regenerate** them instead, delete the `fidus/sync` branch as well after closing. The weekly audit catches any drift either way.
- **Editing on the branch** is fine; Fidus keeps your commits. If `main` later conflicts with your edits, Fidus **pauses**: it marks the PR and waits instead of overwriting your work. Merge or close the PR to resume.
- **Protecting prose.** Wrap hand-written passages to keep them verbatim forever:

  ```markdown
  <!-- fidus:keep -->
  Our on-call runbook lives in PagerDuty; ask #sre for access.
  <!-- /fidus:keep -->
  ```

## Commands

| Command | What it does |
|---|---|
| `fidus init` | Write `fidus.yaml` (and optionally the workflow), then have the agent propose the outline |
| `fidus validate [--offline] [--strict]` | Check config and outline; report source coverage, missing chapters and orphaned files |
| `fidus bootstrap [--chapters a,b] [--resume]` | Write every chapter and open the bootstrap PR |
| `fidus run [--mode auto\|sync\|audit\|full]` | The nightly job. `auto` = sync, plus an audit when one is due |
| `fidus doctor [--ping]` | Check git, tokens, repo access, SDK and key; `--ping` tests tool calling |
| `fidus state show \| set-cursor` | Inspect or move the run cursor |

Common flags:
- `--dry-run --output DIR` writes the would-be docs, `state.json`, `pr-body.md`, `run.json` and `diff.patch` locally, with no GitHub writes.
- `--provider fake` runs fully offline, with no LLM.
- `--since 2026-09-01` processes changes merged after a date.
- `--chapters auth,overview` limits the run to those chapters.

Exit codes: `0` for success or a quiet night, `1` for an error, `2` for invalid config, `3` when some chapters failed but a PR was still produced.

## Running locally and dry runs

```bash
# Full pipeline, no GitHub writes, no LLM: good for checking your outline globs
fidus run --dry-run --provider fake --since 2026-09-01 --output ./out
cat out/pr-body.md && cat out/diff.patch

# The same with your real model
fidus run --dry-run --output ./out
```

Sources can be local paths (`- path: ../my-service`), in which case Fidus treats each commit as a change. That is handy for local use and for monorepos checked out next to the docs.

A container image is also possible: `docker build -t fidus .`, then run `docker run --rm -v "$PWD:/work" -w /work -e ANTHROPIC_API_KEY fidus run --dry-run`.

## Operations and FAQ

- **The run didn't start at exactly midnight.** GitHub delays scheduled workflows under load, and 00:00 UTC is the busiest minute. Fidus is cursor-based, so a late run loses nothing. Use a cron such as `7 0 * * *` if you prefer.
- **Scheduled workflows stopped.** In public repositories GitHub disables them after 60 days without activity. Merging Fidus's PRs counts as activity. If it happens, re-enable the workflow in the Actions tab.
- **Cost.** Fidus only touches affected chapters and caps every episode (turns and tokens) and every run (`budgets.run.max_total_tokens`). Chapters that don't fit in the budget are carried to the next night. The PR shows token usage for each run.
- **Large repositories.** Sources are cloned with `--depth 1`, optionally with a sparse checkout (`sparse: true`). Diffs come from the API and are truncated per file and per PR, and the agent reads files on demand.
- **Monorepos.** Use one source with several `alias`es and `include` globs, or a single alias with precise outline globs.
- **GitHub Enterprise Server.** Set `github.api_url` and `github.server_url` in `fidus.yaml`.
- **Something's off.** Run `fidus doctor`, and see [docs/troubleshooting.md](docs/troubleshooting.md).

## Security model

- **Read-only tools.** The agent can only read files inside fresh clones of your sources, and only write **its one assigned chapter file**. There are no shell or network tools. Paths are resolved and checked for containment, symlinks are refused, and hidden paths, `.github/`, the config and the outline are never writable.
- **Untrusted input.** PR titles, descriptions, diffs and code are wrapped as untrusted data, and the agent is instructed never to follow instructions found in them.
- **Secret scanning.** Every write is scanned for token and key patterns, and for the literal values of your configured secrets, before it lands.
- **Minimal workflow permissions.** The workflow has no `pull_request` trigger and needs only `contents: read`. All writes go through the App token.
- **Human review.** Nothing reaches your default branch without a human merging the PR. That review is the final safety boundary.

Details: [docs/security.md](docs/security.md).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). The quick version:

```bash
uv sync --all-extras
uv run pytest && uv run ruff check && uv run mypy
```

The design rationale is in [docs/design.md](docs/design.md).

## License

[MIT](LICENSE)
