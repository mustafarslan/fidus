# Simulating Fidus

`fidus.sim` lets you watch Fidus behave over days or weeks in a few minutes. It runs the **real,
unmodified `fidus` CLI** (as a subprocess, exactly like the GitHub Action does) against:

- **a local fake GitHub:** a real HTTP server implementing the REST endpoints Fidus uses, backed by
  bare git repositories on disk
- **simulated source repos**, where "developers" merge pull requests with real code changes
- **a simulated reviewer**, who merges or closes the docs PR, edits it by hand, edits `main`, or
  adds chapters to the outline
- **a simulated clock:** each day's night run sees its own date, so the weekly audit fires on
  schedule

```bash
python -m fidus.sim tests/sim/realistic.yaml                     # offline fake model, seconds
python -m fidus.sim tests/sim/realistic.yaml --provider openai \
    --model qwen3-coder --base-url http://localhost:11434/v1 --bench   # a real model + accuracy check
```

It writes `sim-report.md`. The report has:
- a timeline table: each day's morning actions, the PRs merged into sources, the night's result,
  the Fidus PR afterwards, and the chapters changed
- per-day details, with the full PR description after each night
- the final book
- with `--bench`, the accuracy score of the final book

The simulated world is kept (see the path printed at the end, or pass `--workdir`). You can
`git log` any repo in it.

## Modes

- **Fake model (default):** deterministic. Each day's `expect:` block is a hard assertion, and the
  command exits 1 on any failure. `tests/integration/test_simulation.py` runs the built-in scenario
  this way in CI.
- **Real model:** the model makes real decisions, and "no change needed" is sometimes legitimate.
  Expectations are **reported** (✓/✗) but never fail the run.

## The built-in scenario (`tests/sim/realistic.yaml`)

A small order API (Python) and web client (TypeScript) over 8 days:

| Day | What happens | What Fidus should do |
|---|---|---|
| 0 | first run | bootstrap PR with every chapter |
| 1 | reviewer merges; two features ship | new `fidus/sync` PR updating *auth* and *web-client* |
| 2 | reviewer away; the order model changes | the **same** PR grows; the body says "accumulated over 2 runs" |
| 3 | reviewer edits the PR by hand; nothing merges | quiet night, no push |
| 4 | someone edits the same chapter on `main`; a config change ships | conflict with human edits, so **pause** (nothing destroyed) |
| 5 | reviewer closes the PR; the web session changes | rejected batch **skipped** and noted; day 4's change is **not lost**; new PR |
| 6 | reviewer merges; nothing new | stale branch cleaned up; no PR |
| 7 | reviewer adds a chapter to the outline; a week has passed | new chapter written; **weekly audit** runs |
| 8 | three test-only PRs; window `max_prs_per_run: 2` | state-only PR, so the cursor still advances |

## Writing a scenario

```yaml
name: My scenario
start: 2026-03-02          # date of day 0
docs_repo: acme/docs
sources:                   # like fidus.yaml sources, plus `fixture:` (a directory with the initial files)
  - {repo: acme/api, alias: api, branch: main, fixture: realistic/api, include: ["src/**"]}
outline: realistic/outline.yaml
config: {sync: {max_prs_per_run: 2}}   # merged into the generated fidus.yaml
days:
  - day: 0
    bootstrap: true
  - day: 1
    review: merge          # merge | close  (acts on the open Fidus PR, in the morning)
    edit_branch: {docs/part-2/01-auth.md: "\nappended text\n"}   # reviewer edits the PR branch
    edit_main:   {docs/part-2/01-auth.md: "\nappended text\n"}   # someone edits main
    outline: realistic/outline_v2.yaml                          # replace the outline on main
    merges:                # developer PRs merged during the day (file: new content, or null to delete)
      - {repo: acme/api, title: "Add refresh tokens", body: "...", files: {src/app.py: "..."}}
    expect:                # checked after the night run
      pr: open             # open | none
      changed: [auth]      # these chapters (at least) changed tonight
      unchanged: true      # no chapter changed
      noop: true
      paused: true
      audit: true
      body_contains: "Skipped"
      retry: ["auth (quarantined)"]
      chapter_contains: {auth: "some text"}   # text present in that chapter after the night
    expect_live:           # same keys; evaluated only with a real model, reported, never failing
      chapter_contains: {auth: "refresh_token"}
      exit: 0
```
