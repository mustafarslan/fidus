"""Token resolution. In Actions the token comes from actions/create-github-app-token."""

from __future__ import annotations

import os

from fidus.config.models import SourceConfig
from fidus.log import register_secret

TOKEN_ENVS = ("FIDUS_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")


def docs_token() -> str | None:
    for name in TOKEN_ENVS:
        value = os.environ.get(name)
        if value:
            register_secret(value)
            return value
    return None


def source_token(src: SourceConfig) -> str | None:
    if src.token_env:
        value = os.environ.get(src.token_env)
        if value:
            register_secret(value)
            return value
    return docs_token()


def docs_repo_slug() -> str | None:
    """owner/name of the docs repo: $GITHUB_REPOSITORY in Actions, else FIDUS_DOCS_REPO."""
    return os.environ.get("FIDUS_DOCS_REPO") or os.environ.get("GITHUB_REPOSITORY")
