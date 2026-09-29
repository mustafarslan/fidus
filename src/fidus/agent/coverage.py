"""Coverage hints: public symbols in a chapter's sources that the chapter never mentions.

Advisory only (fed into the brief, never validated). Uses lightweight per-language patterns
rather than parsers, so it works on any repository without extra dependencies.
"""

from __future__ import annotations

import re
from pathlib import Path

from fidus.agent.tools.source_tools import iter_files
from fidus.mapping.globs import compile_globs

PATTERNS: dict[str, re.Pattern[str]] = {
    ".py": re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z]\w*)", re.M),
    ".ts": re.compile(
        r"^export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
        r"(?:function\*?|class|interface|type|const|enum|abstract\s+class)\s+([A-Za-z]\w*)",
        re.M,
    ),
    ".go": re.compile(r"^(?:func(?:\s+\([^)]*\))?|type)\s+([A-Z]\w*)", re.M),
    ".rs": re.compile(r"^pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type)\s+([A-Za-z]\w*)", re.M),
    ".java": re.compile(
        r"^\s*public\s+(?:static\s+)?(?:final\s+)?(?:class|interface|enum|record)\s+([A-Z]\w*)",
        re.M,
    ),
}
for ext in (".tsx", ".js", ".jsx", ".mjs"):
    PATTERNS[ext] = PATTERNS[".ts"]
PATTERNS[".kt"] = PATTERNS[".java"]

MAX_FILES = 200


def symbols_in(text: str, suffix: str) -> list[str]:
    pattern = PATTERNS.get(suffix)
    return list(dict.fromkeys(pattern.findall(text))) if pattern else []


def chapter_symbols(
    workspaces: dict[str, Path], sources: list[str], only_files: set[str] | None = None
) -> list[tuple[str, str]]:
    """(symbol, alias:path) for public top-level symbols in files matched by `sources`
    (optionally restricted to `only_files`, given as alias:path)."""
    by_alias: dict[str, list[str]] = {}
    for glob in sources:
        alias, pattern = glob.split(":", 1)
        by_alias.setdefault(alias, []).append(pattern)
    out: list[tuple[str, str]] = []
    for alias, patterns in by_alias.items():
        root = workspaces.get(alias)
        if root is None:
            continue
        spec = compile_globs(patterns)
        files = [f for f in iter_files(root) if spec.match_file(f)][:MAX_FILES]
        for rel in files:
            ref = f"{alias}:{rel}"
            if only_files is not None and ref not in only_files:
                continue
            path = root / rel
            if path.suffix not in PATTERNS or path.stat().st_size > 500_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            out += [
                (name, ref) for name in symbols_in(text, path.suffix) if not name.startswith("_")
            ]
    return out


def undocumented(
    symbols: list[tuple[str, str]], chapter_text: str, limit: int = 25
) -> list[tuple[str, str]]:
    missing = [
        (n, ref) for n, ref in symbols if not re.search(rf"\b{re.escape(n)}\b", chapter_text)
    ]
    return list(dict.fromkeys(missing))[:limit]
