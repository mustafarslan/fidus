"""Tools over the docs repository. `write_doc` can only write this episode's chapter."""

from __future__ import annotations

from pydantic import Field

from fidus.agent.context import EpisodeContext
from fidus.agent.guards import check_write_path, resolve_read_path, scan_secrets
from fidus.agent.tools.base import Tool, ToolArgs
from fidus.agent.validate_doc import validate_chapter


class NoArgs(ToolArgs):
    pass


class ListDocsTool(Tool):
    name = "list_docs"
    description = "List Markdown files currently in the docs directory."
    Args = NoArgs

    def run(self, ctx: EpisodeContext, args: NoArgs) -> str:
        root = ctx.docs_root
        if not root.exists():
            return "(docs directory is empty)"
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md") if p.is_file())
        return "\n".join(files) or "(no markdown files yet)"


class ReadDocArgs(ToolArgs):
    path: str = Field(description="Path relative to the docs directory, e.g. part-1/01-intro.md")


class ReadDocTool(Tool):
    name = "read_doc"
    description = "Read a documentation file (relative to the docs directory)."
    Args = ReadDocArgs

    def run(self, ctx: EpisodeContext, args: ReadDocArgs) -> str:
        path = resolve_read_path(ctx.docs_root, args.path)
        if not path.is_file():
            raise FileNotFoundError(f"{args.path} does not exist in the docs directory (yet)")
        return path.read_text(encoding="utf-8")


class ReadOutlineTool(Tool):
    name = "read_outline"
    description = (
        "Show the textbook outline: parts, numbered chapters, paths, summaries, prerequisites."
    )
    Args = NoArgs

    def run(self, ctx: EpisodeContext, args: NoArgs) -> str:
        if ctx.outline is None:
            return "(no outline)"
        lines = [f"# {ctx.outline.title}"]
        current = None
        for n in ctx.outline.numbered():
            if n.part.id != current:
                current = n.part.id
                lines.append(f"\n## {n.part.title}")
            c = n.chapter
            pre = f" (prerequisites: {', '.join(c.prerequisites)})" if c.prerequisites else ""
            lines.append(
                f"- {n.number} [{c.id}] {c.title} -> {c.path} [{c.level}]{pre}\n    {c.summary}"
            )
        return "\n".join(lines)


class WriteDocArgs(ToolArgs):
    content: str = Field(description="The COMPLETE new Markdown content of your assigned chapter")


class WriteDocTool(Tool):
    name = "write_doc"
    description = (
        "Write the complete Markdown content of the chapter assigned to this episode (the path is "
        "fixed; you cannot write other files). Returns validation problems to fix, if any."
    )
    Args = WriteDocArgs

    def run(self, ctx: EpisodeContext, args: WriteDocArgs) -> str:
        target = ctx.chapter_path
        if target is None:
            raise ValueError("this episode has no chapter to write")
        resolved = check_write_path(target, ctx.docs_root, ctx.protected_paths)
        leaks = scan_secrets(args.content, ctx.secret_values)
        if leaks:
            raise ValueError(
                f"refusing to write: content appears to contain a secret ({', '.join(leaks)}). "
                "Remove it and write again."
            )
        if len(args.content) > 400_000:
            raise ValueError(
                "chapter is too large (>400 KB); split the material or be more concise"
            )
        content = args.content if args.content.endswith("\n") else args.content + "\n"
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        ctx.written = True
        ctx.last_written = content
        problems = validate_chapter(content, ctx)
        if problems:
            return (
                "Written, but validation found problems. Fix them and call write_doc again:\n- "
                + "\n- ".join(problems)
            )
        return f"Written {len(content)} bytes to {ctx.chapter.path if ctx.chapter else target}. Validation passed."
