"""`fidus init`: analyse the sources and have the agent propose fidus.outline.yaml."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from fidus.agent.budget import Budget
from fidus.agent.episode import run_episode
from fidus.agent.prompts import render
from fidus.agent.tools.source_tools import iter_files
from fidus.config.loader import dump_yaml, outline_path
from fidus.config.outline import Outline
from fidus.errors import FidusError
from fidus.log import get_logger
from fidus.pipeline.session import Options, Session
from fidus.sources.filters import SourceFilter

log = get_logger("init")

MANIFESTS = [
    "package.json",
    "pyproject.toml",
    "setup.py",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "mix.exs",
    "CMakeLists.txt",
]
OUTLINE_HEADER = """\
Fidus textbook outline: the single source of truth for the book's structure.

Edit freely: rename, reorder, merge or split chapters, and adjust `sources` globs
(prefixed with a source alias). Keep chapter ids and paths stable once published.
Prerequisites must point to EARLIER chapters (fundamentals first).

Next step: `fidus validate`, then `fidus bootstrap` to write every chapter in one PR.
"""


def _head(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:limit] + ("\n…" if len(text) > limit else "")


def build_digest(session: Session) -> tuple[str, int]:
    """A compact picture of each repo: top-level areas, file counts, tree, README, manifests."""
    sections: list[str] = []
    total = 0
    for src in session.cfg.sources:
        ws = session.clone_sources()[src.name]
        flt = SourceFilter(src)
        files = [f for f in iter_files(ws.root) if flt.accepts(f)]
        total += len(files)
        top = Counter(f.split("/", 1)[0] + ("/" if "/" in f else "") for f in files)
        lines = [
            f"### Repository `{src.name}` ({src.repo or src.path}), {len(files)} files",
            "",
            "Top-level areas (file counts):",
        ]
        for name, n in sorted(top.items()):
            lines.append(
                f"- {src.name}:{name}  ({n} files)"
                if name.endswith("/")
                else f"- {src.name}:{name}"
            )
        dirs = sorted({"/".join(f.split("/")[:3]) for f in files if f.count("/") >= 1})[:250]
        lines += ["", "Tree (depth 3, truncated):", "```", *dirs, "```"]
        for name in ("README.md", "README.rst", "README.txt", "README"):
            if (ws.root / name).is_file():
                lines += ["", f"README ({name}):", "```", _head(ws.root / name, 4000), "```"]
                break
        for m in MANIFESTS:
            if (ws.root / m).is_file():
                lines += ["", f"Manifest {m}:", "```", _head(ws.root / m, 1500), "```"]
        sections.append("\n".join(lines))
    return "\n\n".join(sections), total


def propose_outline(opts: Options, *, title: str | None = None) -> tuple[Outline, Path]:
    session = Session.open(opts, need_outline=False, need_git=False)
    try:
        digest, n_files = build_digest(session)
        target = max(8, min(40, n_files // 40 or 8))
        book_title = title or f"The {' & '.join(s.name for s in session.cfg.sources)} Handbook"
        ctx = session.make_context("init", None, [])
        b = session.cfg.budgets.outline
        budget = Budget(
            max_turns=b.max_turns,
            max_input_tokens=b.max_input_tokens,
            max_output_tokens=b.max_output_tokens,
        )
        brief = render(
            "outline_propose", book_title=book_title, digest=digest, target_chapters=target
        )
        result = run_episode(
            ctx,
            session.provider(),
            budget,
            brief=brief,
            max_tokens=session.cfg.llm.max_output_tokens,
            temperature=session.cfg.llm.temperature,
            context_window=session.cfg.llm.context_window,
        )
        if result.outline is None:
            raise FidusError(
                f"the model did not produce a valid outline ({result.status}: {result.error})"
            )
        path = outline_path(session.cfg, session.opts.config_path.resolve())
        path.write_text(
            dump_yaml(result.outline.model_dump(mode="json"), header=OUTLINE_HEADER),
            encoding="utf-8",
        )
        log.info("wrote %s (%d chapters)", path, sum(1 for _ in result.outline.chapters()))
        return result.outline, path
    finally:
        session.close()
