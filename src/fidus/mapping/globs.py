"""Map `alias:path` to outline chapters via each chapter's source globs."""

from __future__ import annotations

import pathspec

from fidus.config.outline import Outline


def compile_globs(patterns: list[str]) -> pathspec.GitIgnoreSpec:
    """Compile gitignore-style globs (`**` works as users expect)."""
    return pathspec.GitIgnoreSpec.from_lines(patterns)


class ChapterMatcher:
    def __init__(self, outline: Outline) -> None:
        self._specs: list[tuple[str, str, pathspec.GitIgnoreSpec]] = []
        for ch in outline.chapters():
            by_alias: dict[str, list[str]] = {}
            for glob in ch.sources:
                alias, pattern = glob.split(":", 1)
                by_alias.setdefault(alias, []).append(pattern)
            for alias, patterns in by_alias.items():
                self._specs.append((ch.id, alias, compile_globs(patterns)))

    def chapters_for(self, alias: str, path: str) -> list[str]:
        out: list[str] = []
        for chapter_id, a, spec in self._specs:
            if a == alias and spec.match_file(path) and chapter_id not in out:
                out.append(chapter_id)
        return out

    def coverage(self, alias: str, files: list[str]) -> tuple[int, list[str]]:
        """Return (# covered, uncovered files) for a list of repo files."""
        uncovered = [f for f in files if not self.chapters_for(alias, f)]
        return len(files) - len(uncovered), uncovered

    def unmatched_globs(self, outline: Outline, files_by_alias: dict[str, list[str]]) -> list[str]:
        out = []
        for ch in outline.chapters():
            for glob in ch.sources:
                alias, pattern = glob.split(":", 1)
                spec = compile_globs([pattern])
                if not any(spec.match_file(f) for f in files_by_alias.get(alias, [])):
                    out.append(f"{ch.id}: {glob}")
        return out
