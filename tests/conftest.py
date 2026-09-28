from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from fidus.config.models import FidusConfig
from fidus.config.outline import Outline

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "Dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}


def git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV, **(env or {})},
    ).stdout


def write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def make_repo(path: Path, files: dict[str, str], message: str = "initial") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    write(path, files)
    git(path, "add", "-A")
    git(path, "commit", "-qm", message)
    return path


def commit(path: Path, files: dict[str, str], message: str) -> str:
    write(path, files)
    git(path, "add", "-A")
    git(path, "commit", "-qm", message)
    return git(path, "rev-parse", "HEAD").strip()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name in (
        "FIDUS_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_REPOSITORY",
        "FIDUS_DOCS_REPO",
        "GITHUB_STEP_SUMMARY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "FIDUS_GIT_USER_NAME",
        "FIDUS_GIT_USER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FIDUS_CACHE_DIR", str(tmp_path / "cache"))
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)
    yield


OUTLINE_YAML = """\
version: 1
title: The App Book
parts:
  - id: foundations
    title: "Part I: Foundations"
    chapters:
      - {id: overview, title: Overview, path: part-1/01-overview.md, level: fundamentals, sources: ["app:README.md"]}
      - {id: data-model, title: The Data Model, path: part-1/02-data-model.md, level: fundamentals, sources: ["app:src/db/**"], prerequisites: [overview]}
  - id: core
    title: "Part II: Core"
    chapters:
      - {id: auth, title: Authentication, path: part-2/01-auth.md, level: intermediate, sources: ["app:src/auth/**"], prerequisites: [data-model]}
"""

APP_FILES = {
    "README.md": "# App\n",
    "src/auth/login.py": "def login(user):\n    return True\n",
    "src/db/models.py": "class Model:\n    pass\n",
}


def config_yaml(extra: str = "") -> str:
    return (
        "version: 1\n"
        "docs: {dir: docs}\n"
        "sources:\n  - {path: ../app, alias: app}\n"
        "llm: {provider: fake, model: fake}\n" + extra
    )


@pytest.fixture
def cfg() -> FidusConfig:
    return FidusConfig.model_validate(
        {"sources": [{"path": "../app", "alias": "app"}], "llm": {"provider": "fake"}}
    )


@pytest.fixture
def outline() -> Outline:
    from fidus.config.loader import parse_yaml_text

    return Outline.model_validate(parse_yaml_text(OUTLINE_YAML))


@pytest.fixture
def project(tmp_path: Path) -> dict[str, Path]:
    """A local source repo `app` and a docs repo with fidus.yaml + outline (not bootstrapped)."""
    app = make_repo(tmp_path / "app", APP_FILES)
    docs = make_repo(
        tmp_path / "docsrepo", {"fidus.yaml": config_yaml(), "fidus.outline.yaml": OUTLINE_YAML}
    )
    return {"app": app, "docs": docs, "config": docs / "fidus.yaml", "root": tmp_path}
