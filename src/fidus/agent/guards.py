"""Sandboxing for agent tools: path containment, deny-lists and secret scanning.

The agent reads only inside source clones and writes only its one assigned chapter
file. The human-reviewed PR remains the final safety boundary.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

from fidus.errors import GuardViolation

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

DENIED_WRITE_SEGMENTS = {".git", ".github", ".fidus", ".fidus-cache"}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_read_path(root: Path, rel: str) -> Path:
    """Resolve `rel` inside `root`, refusing absolute paths, '..' escapes and escaping symlinks."""
    if not rel or rel in {".", "/"}:
        return root.resolve()
    pure = PurePosixPath(rel.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        raise GuardViolation(
            f"path {rel!r} must be relative to the repository root and not use '..'"
        )
    root_r = root.resolve()
    target = (root_r / pure).resolve()
    if not _is_within(target, root_r):
        raise GuardViolation(f"path {rel!r} escapes the repository")
    return target


def check_write_path(target: Path, docs_root: Path, protected: list[Path]) -> Path:
    """Validate the single file an episode may write. Returns the resolved path."""
    docs_r = docs_root.resolve()
    # Refuse symlinks anywhere between docs root and the target.
    cur = docs_root
    if target.is_absolute():
        try:
            rel = target.relative_to(docs_root)
        except ValueError as e:
            raise GuardViolation(f"{target} is outside the docs directory") from e
    else:
        rel = target
    if ".." in rel.parts:
        raise GuardViolation(f"{target} must not contain '..'")
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            raise GuardViolation(f"refusing to write through symlink {cur}")
        if part in DENIED_WRITE_SEGMENTS or part.startswith("."):
            raise GuardViolation(f"refusing to write hidden or protected path {rel}")
    resolved = (docs_r / rel).resolve()
    if not _is_within(resolved, docs_r):
        raise GuardViolation(f"{target} escapes the docs directory")
    for p in protected:
        if resolved == p.resolve():
            raise GuardViolation(f"{p.name} is protected and cannot be written by the agent")
    if resolved.suffix.lower() != ".md":
        raise GuardViolation("the agent may only write Markdown files")
    return resolved


def scan_secrets(content: str, literal_secrets: list[str] | None = None) -> list[str]:
    """Return the names of secret kinds found in `content`."""
    found = [name for name, pat in SECRET_PATTERNS if pat.search(content)]
    for lit in literal_secrets or []:
        if lit and len(lit) >= 8 and lit in content:
            found.append("configured secret value")
    return found


def redact_secrets(text: str, literal_secrets: list[str] | None = None) -> str:
    """Mask secrets in free text the model returns (summaries, reasons, findings)."""
    for lit in literal_secrets or []:
        if lit and len(lit) >= 8:
            text = text.replace(lit, "[redacted]")
    for _, pat in SECRET_PATTERNS:
        text = pat.sub("[redacted]", text)
    return text


def configured_secret_values(env_names: list[str | None]) -> list[str]:
    names = [n for n in env_names if n] + [
        "FIDUS_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
    ]
    return [v for n in dict.fromkeys(names) if (v := os.environ.get(n))]


def is_binary(path: Path, probe: int = 4096) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(probe)
    except OSError:
        return True
    return b"\x00" in chunk


def wrap_untrusted(source: str, content: str) -> str:
    """Mark text from PRs, diffs and source files as data, never instructions."""
    safe = content.replace("</untrusted_data", "<\\/untrusted_data")
    src = source.replace('"', "'")
    return f'<untrusted_data source="{src}">\n{safe}\n</untrusted_data>'
