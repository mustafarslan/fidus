"""Turn triggers, audit selection and outline state into a list of chapter work items."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fidus.agent.context import Mode
from fidus.config.outline import Outline
from fidus.sources.trigger import Trigger


@dataclass
class WorkItem:
    chapter_id: str
    mode: Mode
    triggers: list[Trigger] = field(default_factory=list)
    audit: bool = False
    rebuild: bool = False


@dataclass
class WorkPlan:
    items: list[WorkItem] = field(default_factory=list)
    deferred: list[WorkItem] = field(default_factory=list)
    trigger_chapters: dict[str, list[str]] = field(default_factory=dict)  # trigger key -> chapters
    unmapped: dict[str, list[str]] = field(default_factory=dict)  # trigger key -> alias:path
    new_chapters: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.items


def find_orphans(outline: Outline, docs_root: Path, index_file: str | None) -> list[str]:
    if not docs_root.exists():
        return []
    known = {c.path for c in outline.chapters()}
    if index_file:
        known.add(index_file)
    out = []
    for p in sorted(docs_root.rglob("*.md")):
        rel = p.relative_to(docs_root).as_posix()
        if rel not in known and not any(part.startswith(".") for part in rel.split("/")):
            out.append(rel)
    return out


def build_plan(
    *,
    outline: Outline,
    triggers: list[Trigger],
    chapters_for: Callable[[str, str], list[str]],
    audit_chapters: list[str],
    rebuild: dict[str, list[Trigger]],
    docs_root: Path,
    index_file: str | None,
    max_episodes: int,
    only: set[str] | None = None,
) -> WorkPlan:
    plan = WorkPlan()
    by_chapter: dict[str, list[Trigger]] = {}

    for t in triggers:
        chapters: list[str] = []
        unmapped: list[str] = []
        for f in t.files:
            hits = chapters_for(t.alias, f.path)
            if not hits and f.previous_path:
                hits = chapters_for(t.alias, f.previous_path)
            if hits:
                for c in hits:
                    if c not in chapters:
                        chapters.append(c)
            else:
                unmapped.append(f"{t.alias}:{f.path}")
        plan.trigger_chapters[t.key] = chapters
        if unmapped:
            plan.unmapped[t.key] = unmapped
        for c in chapters:
            by_chapter.setdefault(c, []).append(t)

    for c, ts in rebuild.items():
        existing = by_chapter.setdefault(c, [])
        keys = {t.key for t in existing}
        existing.extend(t for t in ts if t.key not in keys)

    audit_set = set(audit_chapters)
    for ch in outline.chapters():  # outline order: fundamentals first
        if only is not None and ch.id not in only:
            continue
        exists = (docs_root / ch.path).exists()
        ts = by_chapter.get(ch.id, [])
        if not exists:
            plan.new_chapters.append(ch.id)
            item = WorkItem(ch.id, "bootstrap", ts)
        elif ts:
            item = WorkItem(ch.id, "sync", ts, audit=ch.id in audit_set, rebuild=ch.id in rebuild)
        elif ch.id in audit_set or ch.id in rebuild:
            # Rebuilds/retries without source triggers (audit fixes, consistency passes, failed
            # audits) are re-verified against current code, like an audit.
            item = WorkItem(ch.id, "audit", [], audit=ch.id in audit_set, rebuild=ch.id in rebuild)
        else:
            continue
        if len(plan.items) < max_episodes:
            plan.items.append(item)
        else:
            plan.deferred.append(item)

    plan.orphans = find_orphans(outline, docs_root, index_file)
    return plan
