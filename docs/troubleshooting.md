# Troubleshooting

Start with `fidus doctor` (add `--ping` to test the model). It checks git, tokens, access to every
repository, the SDK and the API key.

| Symptom | Likely cause and fix |
|---|---|
| `cannot read acme/api: ... 404` | The App isn't installed on that repository. Open the App's settings, go to *Install App*, and add the repository. |
| `no GitHub token` | In Actions, check that the `create-github-app-token` step ran and that `github-token:` is passed to the Fidus step. Locally, `export FIDUS_GITHUB_TOKEN=$(gh auth token)`. |
| `no .fidus/state.json found` | Run `fidus bootstrap` and merge its PR first, or use `--since` for a one-off run. |
| `LLM API key not found` | The env var named in `llm.api_key_env` isn't set. In the workflow, add it under `env:` from a secret. |
| The PR is marked **Fidus is paused** | Someone committed to `fidus/sync`, and `main` now conflicts with it. Merge or close the PR; Fidus resumes the next night. |
| A chapter is under *Needs attention: Incomplete* | Its episode ran out of budget or the model misbehaved. It is retried automatically next run. Raise `budgets.episode` if it keeps happening. |
| Many *Unmapped files* | Add globs covering those paths to the right chapters' `sources`. `fidus validate` shows coverage per repo. |
| The model never calls tools (open-weight) | Set `llm.tool_protocol: json`. For vLLM, start the server with tool-calling enabled. |
| `rate limit exceeded` | Fidus waits up to 15 minutes for GitHub limits. For LLM limits, lower `budgets.max_parallel_episodes`. |
| The workflow didn't run at night | Scheduled runs can be delayed or, in public repos, disabled after 60 days of inactivity. Re-enable it in the Actions tab. |
| I closed a PR and want those changes regenerated | Closing means "skip". Delete the `fidus/sync` branch too; the next run reprocesses everything since the last merged PR. |
| `the docs repo has uncommitted changes` | Fidus runs from the pushed default branch. Commit and push (or stash) your local edits, or use `--dry-run`. |

For more detail, run with `-v`. In Actions, every run also writes a summary to the job's *Summary*
page.
