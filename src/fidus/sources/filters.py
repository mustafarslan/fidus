"""Include/exclude filtering and diff truncation for triggers."""

from __future__ import annotations

from fidus.config.models import Limits, SourceConfig
from fidus.mapping.globs import compile_globs
from fidus.sources.trigger import ChangedFile, Trigger

# Never documentation-relevant, regardless of user config.
DEFAULT_EXCLUDES = [
    "**/*.lock",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/poetry.lock",
    "**/uv.lock",
    "**/Cargo.lock",
    "**/go.sum",
    "**/*.min.js",
    "**/*.map",
    "**/*.snap",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.ico",
    "**/*.pdf",
    "**/*.woff*",
    "**/vendor/**",
    "**/node_modules/**",
]


class SourceFilter:
    def __init__(self, source: SourceConfig) -> None:
        self.include = compile_globs(source.include or ["**"])
        self.exclude = compile_globs([*DEFAULT_EXCLUDES, *source.exclude])

    def accepts(self, path: str) -> bool:
        return self.include.match_file(path) and not self.exclude.match_file(path)


def _truncate(text: str, limit: int) -> str:
    if len(text.encode()) <= limit:
        return text
    cut = text.encode()[:limit].decode(errors="ignore")
    return cut + f"\n... [diff truncated at {limit} bytes]"


def apply_filters(trigger: Trigger, source: SourceConfig, limits: Limits) -> Trigger:
    """Drop excluded files and truncate patches per file and per PR (in place)."""
    flt = SourceFilter(source)
    kept: list[ChangedFile] = []
    for f in trigger.files:
        if flt.accepts(f.path) or (f.previous_path and flt.accepts(f.previous_path)):
            kept.append(f)
        else:
            trigger.omitted_files += 1
    budget = limits.diff_max_bytes_per_pr
    for f in kept:
        if f.patch is None:
            continue
        f.patch = _truncate(f.patch, limits.diff_max_bytes_per_file)
        size = len(f.patch.encode())
        if size > budget:
            f.patch = None
            f_note = f"diff omitted (+{f.additions}/-{f.deletions} lines)"
            trigger.notes.append(f"{f.path}: {f_note}")
        else:
            budget -= size
    trigger.files = kept
    if len(trigger.body) > limits.pr_body_max_chars:
        trigger.body = trigger.body[: limits.pr_body_max_chars] + "\n... [truncated]"
    return trigger
