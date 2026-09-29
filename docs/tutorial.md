# Tutorial: document your repo in 15 minutes

This walkthrough takes you from nothing to a nightly documentation PR. Part 1 runs entirely on
your machine, with no GitHub setup, so you can see what Fidus writes before committing to
anything.

**You need:** Python 3.11+, git, and a model. That can be an Anthropic, Gemini or OpenAI key, or a
local or cloud model through [Ollama](https://ollama.com).

---

## Part 1: try it locally (about 5 minutes)

### 1. Install

```bash
pipx install 'fidus[all]'
fidus --version
```

### 2. Make a scratch docs folder next to your code

```bash
mkdir my-docs && cd my-docs && git init -q
```

### 3. Create the config and let the agent propose the book's outline

Point Fidus at a local checkout of your code with `--source ../my-service`. Pick a provider.

With **Ollama** (no key; any model that supports tool calling):

```bash
fidus init --source ../my-service --provider openai \
  --model qwen3-coder --base-url http://localhost:11434/v1 -y
```

With **Anthropic** (the other providers work the same way):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
fidus init --source ../my-service --provider anthropic -y
```

`init` writes two files:
- `fidus.yaml`: the configuration.
- `fidus.outline.yaml`: the proposed Parts → Chapters, ordered from fundamentals to advanced, with
  each chapter mapped to source globs such as `my-service:src/auth/**`. If the chapters leave
  more than 10% of your files uncovered, `init` asks the agent to close the gaps once.

**Read the outline and edit it.** It's the table of contents of your book. Rename, reorder, merge
or split chapters. Then check it:

```bash
fidus validate          # schema, coverage per repo, globs that match nothing
```

### 4. Write the book, without touching anything

```bash
fidus bootstrap --dry-run --output ./preview
```

Look at the output:
- `preview/docs/`: the chapters, plus a generated `README.md` table of contents.
- `preview/pr-body.md`: what the pull request would say.
- `preview/state.json`: Fidus's bookkeeping.

### 5. Check the quality (optional)

```bash
python -m fidus.bench.accuracy preview/docs -c fidus.yaml
```

A judge model checks each chapter's claims against the source files it cites. Supported and
contradicted verdicts must quote real source code. Use a judge model at least as strong as the one
that wrote the docs.

If you like what you see, move on to the real setup.

---

## Part 2: a real docs repository (about 10 minutes)

1. **Create the docs repo**, for example `acme/docs`, and push `fidus.yaml` and
   `fidus.outline.yaml` to it.
2. **Switch sources to GitHub.** In `fidus.yaml`, replace `path: ../my-service` with
   `repo: acme/my-service`, keeping the same `alias`, so the outline globs still match. Commit and
   push.
3. **Create the GitHub App.** The fastest way is `fidus setup-app --docs-repo acme/docs` (one click in the browser; see [Create the GitHub App](../README.md#2-create-the-github-app)). By hand it takes:
   - permissions: Contents (read and write), Pull requests (read and write), Metadata (read)
   - install it on the docs repo **and** every source repo
   - add the `FIDUS_APP_CLIENT_ID` variable and the `FIDUS_APP_PRIVATE_KEY` secret, plus your LLM
     key secret
4. **Check the setup:**
   ```bash
   export FIDUS_GITHUB_TOKEN=$(gh auth token) FIDUS_DOCS_REPO=acme/docs
   fidus doctor --ping      # repo access, push permission, App installation, model tool calling
   ```
5. **Bootstrap for real.** It opens a PR with every chapter:
   ```bash
   fidus bootstrap
   ```
   Review it, edit anything you like, and merge. Your clone goes back to the branch you started on.
6. **Add the nightly workflow.** `fidus init --write-workflow` creates
   `.github/workflows/fidus.yml`; the template is in
   [`examples/workflows/fidus.yml`](../examples/workflows/fidus.yml). Commit it, then run it once
   from the Actions tab with **dry-run** ticked.

---

## Part 3: living with it

**Every night at 00:00 UTC**, Fidus reads the PRs merged into your source repos. It updates the
chapters they affect, re-checks every chapter once a week, and keeps **one** PR, `docs: Fidus
nightly sync`, up to date. Quiet nights produce nothing.

**Your daily review** is a few minutes on that PR:
- The **Chapter updates** table lists each chapter, the PR that triggered the change, and why.
- **Needs attention** lists files no chapter covers, with suggested `sources` globs you can paste
  into the outline. It also lists chapters that keep failing, marked **Needs a human**.
- Wrap anything hand-written in `<!-- fidus:keep -->` … `<!-- /fidus:keep -->` and Fidus will
  never change it.
- **Merge** to accept. **Close** to skip that batch; also delete the `fidus/sync` branch if you
  want it regenerated instead.

**When the code base grows**, add chapters to `fidus.outline.yaml` and merge. The next night writes
them.

More: [configuration reference](configuration.md) · [providers](providers.md) ·
[troubleshooting](troubleshooting.md) · [security model](security.md).
