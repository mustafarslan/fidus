"""Load prompt templates (package data) and build episode briefs."""

from __future__ import annotations

import functools
from importlib import resources
from pathlib import Path
from string import Template

from fidus.agent.context import EpisodeContext
from fidus.agent.guards import wrap_untrusted
from fidus.sources.trigger import Trigger


@functools.cache
def load(name: str) -> str:
    return resources.files("fidus.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


def render(name: str, **values: object) -> str:
    return Template(load(name)).safe_substitute({k: str(v) for k, v in values.items()})


def system_prompt(ctx: EpisodeContext) -> str:
    repos = ", ".join(f"`{s.name}` ({s.repo or s.path})" for s in ctx.cfg.sources)
    title = ctx.outline.title if ctx.outline else "(to be designed)"
    base = render("system_base", repos=repos, book_title=title, language=ctx.cfg.docs.language)
    if ctx.mode == "init":
        return base
    extra = ""
    if ctx.cfg.docs.style_guide_extra:
        p = ctx.repo_root / ctx.cfg.docs.style_guide_extra
        if p.is_file():
            extra = "\n### Project-specific style notes\n" + p.read_text(encoding="utf-8")
    return base + "\n\n" + render("style_textbook", style_extra=extra)


def _chapter_values(ctx: EpisodeContext) -> dict[str, object]:
    ch = ctx.chapter
    assert ch is not None and ctx.outline is not None
    prereqs = []
    for pid in ch.prerequisites:
        p = ctx.outline.get(pid)
        prereqs.append(f"[{p.title}]({p.path}) (`{pid}`)")
    if ctx.original_doc:
        current = wrap_untrusted(f"docs:{ch.path}", ctx.original_doc)
    else:
        current = "(the chapter file does not exist yet)"
    return {
        "number": ctx.outline.number_of(ch.id),
        "title": ch.title,
        "chapter_id": ch.id,
        "path": ch.path,
        "level": ch.level,
        "summary": ch.summary or "(none)",
        "sources": ", ".join(f"`{s}`" for s in ch.sources) or "(none mapped)",
        "prerequisites": ", ".join(prereqs) or "none",
        "current_doc": current,
    }


def format_triggers(triggers: list[Trigger]) -> str:
    rows = []
    for t in triggers:
        files = ", ".join(f"`{t.alias}:{f.path}`" for f in t.files[:15])
        more = f" (+{len(t.files) - 15} more)" if len(t.files) > 15 else ""
        ref = f"get_pr(repo={t.repo!r}, number={t.number})" if t.number is not None else "commit"
        rows.append(f"- {t.label} — {ref}\n  files: {files}{more}")
    return wrap_untrusted("trigger-list", "\n".join(rows)) if rows else "(none)"


def known_problems(ctx: EpisodeContext) -> str:
    """Validation problems already present in the current chapter, as a brief section.

    Validation otherwise only runs on writes, so problems in a chapter that needs no other
    change (stale line-number citations, broken links, ...) would survive forever.
    """
    if not ctx.original_doc or ctx.chapter is None:
        return ""
    from fidus.agent.validate_doc import validate_chapter

    problems = validate_chapter(ctx.original_doc, ctx)
    if not problems:
        return ""
    items = "\n".join(f"- {p}" for p in problems)
    return (
        "### Known problems in this chapter\n"
        "Validation found these problems in the current chapter. Fix them with a minimal edit and "
        "call write_doc, even if nothing else needs to change:\n"
        f"{items}\n\n"
    )


def chapter_brief(ctx: EpisodeContext, *, changes: str = "") -> str:
    values = _chapter_values(ctx)
    values["known_problems"] = known_problems(ctx)
    if ctx.mode == "sync":
        return render("chapter_sync", triggers=format_triggers(ctx.triggers), **values)
    if ctx.mode == "audit":
        return render("chapter_audit", **values)
    if ctx.mode == "consistency":
        return render("chapter_consistency", changes=changes, **values)
    if ctx.mode == "bootstrap":
        if not ctx.original_doc:
            values["current_doc"] = ""
        else:
            values["current_doc"] = "### Existing draft (improve or replace it)\n" + str(
                values["current_doc"]
            )
        return render("chapter_bootstrap", **values)
    raise ValueError(f"no chapter brief for mode {ctx.mode}")


def read_text_or_none(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
