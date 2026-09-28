"""`fidus validate`: schema checks plus coverage against the real sources."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from fidus.agent.tools.source_tools import iter_files
from fidus.config.loader import load_config, load_outline
from fidus.mapping.globs import ChapterMatcher
from fidus.mapping.planner import find_orphans
from fidus.sources.filters import SourceFilter


def run_validate(config: Path, *, offline: bool, console: Console) -> list[str]:
    cfg = load_config(config)
    outline = load_outline(cfg, config.resolve())
    n = sum(1 for _ in outline.chapters())
    console.print(
        f"[green]✓[/] {config}: {len(cfg.sources)} source(s), provider {cfg.llm.provider}/{cfg.llm.model}"
    )
    console.print(f"[green]✓[/] outline: {len(outline.parts)} parts, {n} chapters")
    warnings: list[str] = []

    docs_root = config.resolve().parent / cfg.docs.dir
    missing = [c.path for c in outline.chapters() if not (docs_root / c.path).exists()]
    if missing:
        warnings.append(
            f"{len(missing)} chapter file(s) not written yet (run `fidus bootstrap`): "
            + ", ".join(missing[:5])
            + (" …" if len(missing) > 5 else "")
        )
    orphans = find_orphans(outline, docs_root, cfg.docs.index_file)
    if orphans:
        warnings.append("files in docs dir not in the outline: " + ", ".join(orphans[:10]))

    if not offline:
        from fidus.pipeline.session import Options, Session

        session = Session.open(Options(config), need_git=False)
        try:
            files_by_alias: dict[str, list[str]] = {}
            for src in cfg.sources:
                ws = session.clone_sources()[src.name]
                flt = SourceFilter(src)
                files_by_alias[src.name] = [f for f in iter_files(ws.root) if flt.accepts(f)]
            matcher = ChapterMatcher(outline)
            for alias, files in files_by_alias.items():
                covered, uncovered = matcher.coverage(alias, files)
                pct = 100 * covered / len(files) if files else 100
                console.print(f"  coverage {alias}: {covered}/{len(files)} files ({pct:.0f}%)")
                if pct < 60:
                    dirs = sorted({u.split("/", 1)[0] for u in uncovered})[:8]
                    warnings.append(
                        f"{alias}: low coverage; uncovered areas include {', '.join(dirs)}"
                    )
            for g in matcher.unmatched_globs(outline, files_by_alias):
                warnings.append(f"glob matches no files: {g}")
        finally:
            session.close()

    for w in warnings:
        console.print(f"[yellow]![/] {w}")
    if not warnings:
        console.print("[green]All good.[/]")
    return warnings
