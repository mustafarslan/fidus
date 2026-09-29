# GitHub App setup, in depth

The [README](../README.md#2-create-the-github-app) has the standard walkthrough. This page covers
the variations.

## What the token needs

| Where | Permission | Used for |
|---|---|---|
| Source repos | Contents: read | `git clone --depth 1` of the latest code |
| Source repos | Pull requests: read | listing merged PRs, reading their files and diffs |
| Docs repo | Contents: read and write | pushing `fidus/sync`, `fidus/bootstrap` and `fidus/outline` |
| Docs repo | Pull requests: read and write | opening and updating PRs, labels and reviewer requests |
| All | Metadata: read | mandatory |

A GitHub App has one set of permissions for all the repositories it's installed on, so the App
above *could* write to your source repos. Fidus never does: the only place it pushes is the docs
repository, and only to `fidus/*` branches. If your policy needs that guaranteed by permissions
rather than by code, use two Apps.

## Least-privilege two-App setup

1. **`acme-fidus-reader`**: *Contents: read*, *Pull requests: read*, *Metadata: read*. Install it
   on the source repositories.
2. **`acme-fidus-writer`**: *Contents: read and write*, *Pull requests: read and write*. Install it
   on the docs repository only.

Mint both tokens in the workflow:

```yaml
      - id: reader
        uses: actions/create-github-app-token@v3
        with:
          client-id: ${{ vars.FIDUS_READER_CLIENT_ID }}
          private-key: ${{ secrets.FIDUS_READER_PRIVATE_KEY }}
          owner: ${{ github.repository_owner }}
      - id: writer
        uses: actions/create-github-app-token@v3
        with:
          client-id: ${{ vars.FIDUS_WRITER_CLIENT_ID }}
          private-key: ${{ secrets.FIDUS_WRITER_PRIVATE_KEY }}
      - uses: actions/checkout@v7
        with: { token: "${{ steps.writer.outputs.token }}", fetch-depth: 0 }
      - uses: mustafarslan/fidus@v1
        env:
          FIDUS_SOURCE_TOKEN: ${{ steps.reader.outputs.token }}
        with:
          github-token: ${{ steps.writer.outputs.token }}
```

Then point every source at the reader token in `fidus.yaml`:

```yaml
sources:
  - { repo: acme/api, alias: api, token_env: FIDUS_SOURCE_TOKEN }
```

## Sources under another owner

An installation token only covers repositories owned by the account it was minted for. If
`acme/docs` documents `partner-org/sdk`:

1. Install the App on `partner-org` too, or make the App public and have `partner-org` install it.
2. Mint a second token with `owner: partner-org` and expose it as an environment variable.
3. Set `token_env` on that source.

Public source repositories need no token at all to be cloned, but a token raises the API rate
limit.

## GitHub Enterprise Server

```yaml
github:
  api_url: https://github.example.com/api/v3
  server_url: https://github.example.com
```

Pass `github-api-url` to `actions/create-github-app-token` as well.

## Running Fidus outside GitHub Actions

Fidus reads its token from `FIDUS_GITHUB_TOKEN` (falling back to `GH_TOKEN`, then
`GITHUB_TOKEN`) and the docs repository slug from `FIDUS_DOCS_REPO` (falling back to
`GITHUB_REPOSITORY`, then the `origin` remote). From any scheduler (cron, Kubernetes CronJob,
GitLab CI):

```bash
git clone https://github.com/acme/docs && cd docs
export FIDUS_GITHUB_TOKEN=...   # an App installation token or a PAT
export ANTHROPIC_API_KEY=...
fidus run
```

To mint an App installation token outside Actions, use the App's private key to sign a JWT and
exchange it at `POST /app/installations/{id}/access_tokens`. The `gh` CLI extension
`gh-token`, or any GitHub App library, can do this for you.
