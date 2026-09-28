"""Materialise source repositories as local workspaces (depth-1 clones or local paths)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from fidus.config.models import FidusConfig, SourceConfig
from fidus.errors import ConfigError
from fidus.gitops.runner import git
from fidus.log import get_logger

log = get_logger("clone")


@dataclass
class SourceWorkspace:
    alias: str
    root: Path
    head_sha: str | None
    branch: str | None


def _host(cfg: FidusConfig) -> str:
    return urlparse(cfg.github.server_url).netloc or "github.com"


def clone_source(
    src: SourceConfig,
    cfg: FidusConfig,
    cache_dir: Path,
    *,
    config_dir: Path,
    token: str | None,
    branch: str | None = None,
) -> SourceWorkspace:
    if src.path is not None:
        root = (config_dir / src.path).resolve()
        if not root.is_dir():
            raise ConfigError(f"source {src.name!r}: path {root} does not exist")
        head = None
        if (root / ".git").exists():
            proc = git(["rev-parse", "HEAD"], root, check=False)
            head = proc.stdout.strip() or None
        return SourceWorkspace(src.name, root, head, None)

    assert src.repo is not None
    dest = cache_dir / "src" / src.name
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{cfg.github.server_url.rstrip('/')}/{src.repo}.git"
    args = ["clone", "--depth", "1", "--no-tags", "--single-branch"]
    br = branch or src.branch
    if br:
        args += ["--branch", br]
    if src.sparse:
        args += ["--sparse", "--filter=blob:none"]
    log.info("cloning %s%s", src.repo, f"@{br}" if br else "")
    git([*args, url, str(dest)], cache_dir, token=token, host=_host(cfg))
    if src.sparse:
        git(
            ["sparse-checkout", "set", "--no-cone", *src.include],
            dest,
            token=token,
            host=_host(cfg),
        )
    head = git(["rev-parse", "HEAD"], dest).stdout.strip()
    return SourceWorkspace(src.name, dest, head, br)
