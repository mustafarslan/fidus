"""Deterministic checks run on every chapter the agent writes."""

from __future__ import annotations

import re
from posixpath import dirname, join, normpath

from fidus.agent.context import EpisodeContext
from fidus.agent.guards import resolve_read_path
from fidus.config.loader import parse_yaml_text
from fidus.errors import ConfigError, GuardViolation

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
KEEP_RE = re.compile(r"<!--\s*fidus:keep\s*-->(.*?)<!--\s*/fidus:keep\s*-->", re.DOTALL)
FENCE_RE = re.compile(r"^(```|~~~).*?^\1", re.DOTALL | re.MULTILINE)
# Signs of invented examples: fake tool transcripts inside code, "imagine a PR ..." prose.
TRANSCRIPT_RE = re.compile(
    r"^\s*(?:#|//|>)\s*(?:tool call|result|output|response)\s*:", re.I | re.M
)
HYPOTHETICAL_RE = re.compile(
    r"\b(?:imagine|suppose|let's say|hypothetically)\b[^.\n]{0,80}\b"
    r"(?:pr|pull request|commit|change|function|developer|user|diff)\b",
    re.I,
)


def keep_blocks(text: str | None) -> list[str]:
    return KEEP_RE.findall(text or "")


def _citation_re(aliases: list[str]) -> re.Pattern[str]:
    alt = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
    return re.compile(rf"`(?P<alias>{alt}):(?P<path>[^`\s:#]+)(?:[:#][^`]*)?`")


def validate_chapter(content: str, ctx: EpisodeContext) -> list[str]:
    problems: list[str] = []
    chapter = ctx.chapter
    assert chapter is not None

    # 1. Frontmatter identifies the chapter.
    m = FRONTMATTER_RE.match(content)
    if not m:
        problems.append(
            "missing YAML frontmatter; start the file with:\n---\n"
            f"title: {chapter.title}\nfidus:\n  chapter: {chapter.id}\n---"
        )
    else:
        try:
            fm = parse_yaml_text(m.group(1)) or {}
        except ConfigError as e:
            fm = {}
            problems.append(f"frontmatter is not valid YAML: {e}")
        fid = fm.get("fidus", {}) if isinstance(fm, dict) else {}
        if not isinstance(fid, dict) or fid.get("chapter") != chapter.id:
            problems.append(f"frontmatter must contain `fidus: {{chapter: {chapter.id}}}`")

    prose = FENCE_RE.sub("", content)

    # 2. Relative links resolve to known docs.
    known = {c.path for c in ctx.outline.chapters()} if ctx.outline else set()
    if ctx.cfg.docs.index_file:
        known.add(ctx.cfg.docs.index_file)
    here = dirname(chapter.path)
    for target in LINK_RE.findall(prose):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("#", "/")):
            continue
        path = target.split("#", 1)[0]
        if not path:
            continue
        resolved = normpath(join(here, path))
        if resolved in known:
            continue
        if not (ctx.docs_root / resolved).exists():
            problems.append(f"broken relative link {target!r} (resolves to {resolved!r})")

    # 3. Cited source files exist in the clones.
    aliases = [a for a in ctx.workspaces]
    if aliases:
        rx = _citation_re(aliases)
        for cm in rx.finditer(content):
            alias, path = cm.group("alias"), cm.group("path")
            if any(ch in path for ch in "*?[") or path.endswith("/"):
                continue
            if re.search(r"(?::|#L)\d+", cm.group(0)):
                problems.append(
                    f"citation {cm.group(0)} includes a line number; cite the file (and symbol name) "
                    "only, since line numbers go stale as the code changes"
                )
            try:
                exists = resolve_read_path(ctx.workspaces[alias], path).exists()
            except GuardViolation:
                exists = False
            if not exists:
                problems.append(
                    f"citation `{alias}:{path}` does not exist in the source repository"
                )

    # 4. No invented example scenarios (models copy them into docs as if they were real).
    code = "\n".join(m.group(0) for m in FENCE_RE.finditer(content))
    if TRANSCRIPT_RE.search(code):
        problems.append(
            "a code block contains a made-up tool/output transcript (lines like '# Tool call:' or "
            "'# Result:'); replace it with real code excerpts and describe the process in prose"
        )
    hypo = HYPOTHETICAL_RE.search(prose)
    if hypo:
        problems.append(
            f"hypothetical scenario ({hypo.group(0)[:60]!r}...); explain with the real code "
            "instead of an imagined change or invented example"
        )

    # 5. Lines people added to this Fidus-maintained chapter survive.
    squashed = re.sub(r"\s+", " ", content)
    dropped = [ln for ln in ctx.human_lines if re.sub(r"\s+", " ", ln) not in squashed]
    if dropped:
        problems.append(
            f"{len(dropped)} line(s) a person added to this chapter were removed "
            f"(first: {dropped[0][:80]!r}); keep them unless the current code contradicts them"
        )

    # 6. Human-protected blocks are untouched.
    before = keep_blocks(ctx.original_doc)
    if before and keep_blocks(content) != before:
        problems.append(
            "a <!-- fidus:keep --> ... <!-- /fidus:keep --> block was modified, moved or removed; "
            "these are human-owned and must be preserved byte-for-byte"
        )
    return problems
