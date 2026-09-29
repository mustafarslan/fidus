# Fidus

**Documentation that keeps up with your code.**

[![PyPI](https://img.shields.io/pypi/v/fidus.svg)](https://pypi.org/project/fidus/)
[![Python](https://img.shields.io/pypi/pyversions/fidus.svg)](https://pypi.org/project/fidus/)
[![CI](https://github.com/mustafarslan/fidus/actions/workflows/ci.yml/badge.svg)](https://github.com/mustafarslan/fidus/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Fidus is an open-source documentation agent. Every night it looks at the pull requests merged
into your repositories, updates the parts of your documentation they affect, and opens **one tidy
pull request** for a person to review. Your docs stay accurate, and your team stays in charge.

The documentation reads like a **textbook**: Parts and Chapters that start with the fundamentals
and build up to advanced topics, in plain Markdown that works with any docs site. Fidus works with
**Anthropic Claude**, **Google Gemini**, and **any OpenAI-compatible endpoint**, including
open-weight models on Ollama, vLLM, OpenRouter or Together. You bring your own key.

```
 your source repos                            your docs repo
┌────────────────────┐   every night         ┌──────────────────────────────────────┐
│ acme/api   PR #412 ─┼──┐                    │ docs/                                │
│ acme/web   PR #88  ─┼──┼─► Fidus ─────────► │   part-1-foundations/01-overview.md  │
│ acme/infra PR #9   ─┼──┘   (your model)     │   part-2-core/01-auth.md   ◄─ updated│
└────────────────────┘                        └──────────┬───────────────────────────┘
                                                         ▼
                           one PR: "docs: Fidus nightly sync" → you review → merge
```

## Why teams like Fidus

- **Docs that stay true.** Fidus updates only the chapters a change affects, with the smallest
  correct edit, citing the source files it relied on.
- **A review that takes minutes.** One rolling pull request, with a table of *what changed, why,
  and which PR caused it*. Quiet nights produce no noise at all.
- **Your words are safe.** Passages people add are recognised and preserved. Anything wrapped in
  `<!-- fidus:keep -->` is never touched.
- **You own the structure.** The book's outline is a plain YAML file you edit. Fidus fills in the
  chapters and suggests improvements, and you decide.
- **A weekly check-up.** Once a week every chapter is re-verified against the current code, so
  small drifts don't pile up.
- **Built to be trusted.** The agent can only read your code and write its own chapter. Every
  change arrives as a pull request you approve.

## See it in action in 5 minutes

Try Fidus on your own code, on your own machine, with no GitHub setup:

```bash
pipx install 'fidus[all]'
mkdir my-docs && cd my-docs && git init -q

# Point it at a local checkout; use any provider (here: a local Ollama model)
fidus init --source ../my-service --provider openai \
  --model qwen3-coder --base-url http://localhost:11434/v1 -y

fidus bootstrap --dry-run --output ./preview     # writes the whole book into ./preview
```

Open `preview/docs/` to read your book and `preview/pr-body.md` to see the pull request Fidus
would open. The [15-minute tutorial](docs/tutorial.md) walks through this step by step. If you're
curious how Fidus behaves over weeks, the [simulator](docs/simulation.md) plays out eight days of a
team's life in a few minutes.

## Contents

- [How it works](#how-it-works)
- [Choosing how Fidus authenticates](#choosing-how-fidus-authenticates)
- [Setup](#setup)
- [Works with your favourite models](#works-with-your-favourite-models)
- [Configuration and the outline](#configuration-and-the-outline)
- [Your daily review](#your-daily-review)
- [Commands](#commands)
- [Confidence built in](#confidence-built-in)
- [Security](#security)
- [FAQ](#faq)
- [Contributing](#contributing)

## How it works

1. **Collect.** Fidus lists the pull requests merged into each source repository since its last
   run, together with their changed files and diffs. Lockfiles, build output and anything you
   exclude are filtered out.
2. **Map.** Each changed file is matched to chapters through the `sources` globs in your outline,
   so `api:src/auth/**` belongs to *Authentication*. Files no glob covers are placed with a quick
   model call. Anything still unplaced is listed for you, never silently dropped.
3. **Write.** Each affected chapter gets its own agent session. The agent reads the pull requests,
   explores a fresh copy of the latest code, and makes the smallest correct edit, citing files as
   `alias:path`.
4. **Check.** Every edit is validated: the frontmatter, links between chapters, cited files,
   human-protected blocks, and no invented examples. Chapters that depend on each other are
   updated in order, and if a fundamentals chapter renames a concept, the chapters that build on it
   get a short consistency pass.
5. **Audit.** Weekly, every chapter is re-verified against the code, with hints about public
   functions a chapter doesn't mention yet.
6. **Publish.** Everything lands in **one rolling pull request** on the `fidus/sync` branch. If
   you don't merge for a few days, later updates join the same PR.

Fidus's progress travels with that pull request, so it only moves forward when you merge. Closing
a PR means "skip these changes". Chapters that fail or run out of budget are simply retried the
next night.

## Choosing how Fidus authenticates

Fidus needs a token that can read your source repositories and open a pull request on your docs
repository. Pick whichever suits you:

| Option | Setup | Great for | Good to know |
|---|---|---|---|
| **GitHub App** (recommended) | `fidus setup-app`, one click | Teams and private repositories | PRs come from `your-app[bot]`; short-lived tokens that aren't tied to a person; your docs CI runs on its PRs |
| **Built-in `GITHUB_TOKEN`** | None: `fidus init --write-workflow --auth github-token` | Public source repos, or docs in the same repo as the code | Reads only public repos and its own; PRs don't trigger other workflows; enable *Settings → Actions → General → Allow GitHub Actions to create and approve pull requests* |
| **Personal access token** | About 5 minutes ([see below](#using-a-personal-access-token)) | Personal projects and quick trials | PRs appear as you, and the token eventually expires |

## Setup

Setting Fidus up for your team takes about 15 minutes and is done once. You'll need the
repositories you want documented, a docs repository (it can start empty), and an API key for your
model.

### 1. Create a docs repository

Create a repository such as `acme/docs`. Fidus writes Markdown under `docs/` and keeps its two
settings files, `fidus.yaml` and `fidus.outline.yaml`, at the root. The Markdown works nicely with
MkDocs, Docusaurus, VitePress, mdBook, or GitHub's own rendering.

### 2. Create the GitHub App

One command does it:

```bash
pipx install fidus && gh auth login        # gh is optional, and saves the credentials for you
fidus setup-app --docs-repo acme/docs      # add --org acme for an organisation-owned App
```

Your browser opens a pre-filled *Register new GitHub App* page: the right permissions, the
webhook off, and the App private. Click **Create GitHub App**. Fidus saves the App's credentials
to your docs repository, as the `FIDUS_APP_CLIENT_ID` variable and the `FIDUS_APP_PRIVATE_KEY`
secret, and opens the install page. Install the App on **the docs repo and every source repo**, then
add your model key as a secret (for example `ANTHROPIC_API_KEY`).

Prefer to click through it yourself, or need a two-App least-privilege setup or GitHub Enterprise
Server? [docs/github-app.md](docs/github-app.md) has you covered.

### 3. Install Fidus and draft the outline

In a clone of your docs repository:

```bash
pipx install 'fidus[anthropic]'              # or fidus[gemini], fidus[openai], fidus[all]
export ANTHROPIC_API_KEY=sk-ant-...
export FIDUS_GITHUB_TOKEN=$(gh auth token)   # lets `init` read your private source repos

fidus init --source acme/api --source acme/web --write-workflow
```

Fidus writes `fidus.yaml` and a nightly workflow, studies your code, and **proposes
`fidus.outline.yaml`**, a table of contents ordered from fundamentals to advanced. If the proposal
misses parts of your code, it takes a second pass to fill the gaps.

Now make the outline yours: rename, reorder, merge or split chapters. Then check it:

```bash
fidus validate        # checks the files and shows how much of your code each repo's chapters cover
```

### 4. Write the book

Commit and push your two settings files, then let Fidus write every chapter in one pull request:

```bash
git add fidus.yaml fidus.outline.yaml .github && git commit -m "Add Fidus" && git push
fidus bootstrap
```

Review the bootstrap PR, polish anything you like, and merge. To preview first, add
`--dry-run --output ./preview`. You can also bootstrap from GitHub Actions by running the action
once with `command: bootstrap`.

### 5. Turn on the nightly run

`fidus init --write-workflow` created `.github/workflows/fidus.yml`; a copy lives in
[`examples/workflows/fidus.yml`](examples/workflows/fidus.yml). Commit it, then run it once from
*Actions → Fidus nightly docs sync → Run workflow* with **dry-run** ticked. From then on it runs
every night at 00:00 UTC.

### Using a personal access token

Create a **fine-grained personal access token** for the source and docs repositories with
*Contents: Read and write*, *Pull requests: Read and write* and *Metadata: Read*. Store it as the
`FIDUS_GITHUB_TOKEN` secret, and use it in place of the App token in the workflow:

```yaml
      - uses: actions/checkout@v7
        with: { token: "${{ secrets.FIDUS_GITHUB_TOKEN }}", fetch-depth: 0 }
      - uses: mustafarslan/fidus@v1
        with:
          github-token: ${{ secrets.FIDUS_GITHUB_TOKEN }}
```

## Works with your favourite models

Pick a provider in `fidus.yaml`. Keys always stay in environment variables, never in the file.

```yaml
llm: { provider: anthropic, model: claude-opus-5 }                                    # Anthropic Claude
llm: { provider: gemini, model: gemini-3.8-flash }                                    # Google Gemini
llm: { provider: openai, model: gpt-6-sol }                                           # OpenAI
llm: { provider: openai, model: qwen3-coder, base_url: "http://localhost:11434/v1" }  # Ollama
llm: { provider: openai, model: deepseek/deepseek-v3, base_url: "https://openrouter.ai/api/v1",
       api_key_env: OPENROUTER_API_KEY }                                              # OpenRouter
```

A few extras:
- **Smaller open-weight models work too.** If a model or server stumbles over tool calls, Fidus
  notices and switches to a simple JSON protocol automatically.
- **A cheaper triage model.** Use `triage_model` for the quick "which chapter is this?" step.
- **Cost estimates.** Add `pricing` to see estimated costs in each PR. Token counts are always
  shown.
- **Refusal fallbacks.** On Claude models that support them, a request declined by a safety filter
  is retried on Anthropic's recommended fallback model.

See [docs/providers.md](docs/providers.md) for details.

## Configuration and the outline

Everything lives in two small files in your docs repo.

**`fidus.yaml`** says what to document and how:

```yaml
version: 1
docs: { dir: docs, outline: fidus.outline.yaml }
sources:
  - { repo: acme/api, alias: api, include: ["src/**", "README.md"], exclude: ["**/tests/**"] }
  - { repo: acme/web, alias: web }
llm: { provider: anthropic, model: claude-opus-5 }
schedule: { audit: { enabled: true, every_days: 7 } }
sync: { labels: [documentation, fidus], reviewers: [alice] }
```

**`fidus.outline.yaml`** is your book's table of contents:

```yaml
version: 1
title: "The Acme Platform"
parts:
  - id: foundations
    title: "Part I: Foundations"
    chapters:
      - id: overview
        title: What Acme Is and How It Is Built
        path: part-1-foundations/01-overview.md
        level: fundamentals
        sources: ["api:README.md", "api:src/app.py", "web:src/main.ts"]
      - id: auth
        title: Authentication and Sessions
        path: part-1-foundations/02-auth.md
        level: intermediate
        sources: ["api:src/auth/**", "web:src/lib/session.ts"]
        prerequisites: [overview]
```

- **The structure is yours.** Nightly runs never add, remove or reorder chapters.
- **New chapters are written for you.** Add a chapter to the outline and merge; the next night
  writes it.
- **Suggestions, not surprises.** Restructuring ideas from the audit appear in the PR (or as a
  separate outline PR, if you prefer).

The complete reference is in [docs/configuration.md](docs/configuration.md), with every option in
[`examples/fidus.yaml`](examples/fidus.yaml).

## Your daily review

- **One pull request,** updated in place until you merge it. Its description shows each chapter
  that changed, the source PR behind it, and a one-line reason.
- **Merge** to publish. **Close** to skip that batch; delete the `fidus/sync` branch too if you'd
  like those changes regenerated instead.
- **Edit freely** on the PR branch. Fidus keeps your commits, and if they ever conflict with `main`
  it pauses politely instead of overwriting anything.
- **"Needs attention"** points out files no chapter covers, with ready-to-paste outline globs, and
  any chapter that needs a human look.
- **Keep text exactly as written** by wrapping it:

  ```markdown
  <!-- fidus:keep -->
  Our on-call runbook lives in PagerDuty; ask #sre for access.
  <!-- /fidus:keep -->
  ```

## Commands

| Command | What it does |
|---|---|
| `fidus init` | Create `fidus.yaml` (and the workflow) and draft the outline |
| `fidus validate` | Check your settings and show how well your code is covered |
| `fidus bootstrap` | Write every chapter and open the first pull request |
| `fidus run` | The nightly job: sync, plus the weekly audit when it's due |
| `fidus setup-app` | Create the GitHub App in one click |
| `fidus doctor --ping` | Check tokens, repository access, push permission and your model |
| `fidus state show \| set-cursor \| clear-retry` | Look at or adjust Fidus's progress |

Handy flags: `--dry-run --output DIR` previews everything locally, `--provider fake` runs offline
with no model, `--since 2026-09-01` processes changes after a date, and `--chapters auth,overview`
focuses on a few chapters.

## Confidence built in

Fidus is tested the way it's used:

- **A simulator.** `python -m fidus.sim tests/sim/realistic.yaml` runs the real `fidus` command
  against a local stand-in for GitHub over eight scripted days: features shipping, a reviewer away,
  hand edits, a merge conflict, a rejected PR, a new chapter and the weekly audit. It runs in CI on
  every change, and with a real model it shows how the docs actually evolve.
  [Read more](docs/simulation.md).
- **An accuracy check.** `python -m fidus.bench.accuracy DOCS_DIR -c fidus.yaml` asks a judge model
  to verify each chapter's claims against the code it cites, with quoted evidence.
- **A thorough test suite,** including a multi-night lifecycle against real git repositories.

## Security

- **Least privilege by design.** The agent can read only your source code and write only the one
  chapter it's working on. There are no shell or network tools.
- **Untrusted input stays untrusted.** PR descriptions, diffs and code are treated as data, never as
  instructions.
- **Secrets stay secret.** Every write is scanned for keys and tokens before it lands.
- **Minimal permissions.** The workflow needs only `contents: read`; writes go through the App's
  short-lived token.
- **People have the final say.** Nothing reaches your default branch until someone merges it.

More in [docs/security.md](docs/security.md).

## FAQ

**Does it run exactly at midnight?** GitHub may start scheduled runs a little late at busy times.
Fidus picks up exactly where it left off, so nothing is missed.

**What does it cost?** Fidus only touches the chapters a change affects, and every run has a token
budget you control. Each PR shows the tokens used.

**Will it work on a big repository or a monorepo?** Yes. Sources are cloned shallowly (sparse
checkouts are supported), the agent reads files on demand, and globs can slice a monorepo into as
many areas as you like.

**GitHub Enterprise Server?** Supported: set `github.api_url` and `github.server_url`.

**Something doesn't look right?** Run `fidus doctor`, and see
[docs/troubleshooting.md](docs/troubleshooting.md).

## Contributing

Contributions are very welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) to get started:

```bash
uv sync --all-extras
uv run pytest && uv run ruff check && uv run mypy
```

The design notes are in [docs/design.md](docs/design.md).

## License

[MIT](LICENSE)
