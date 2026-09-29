"""One batched LLM call that maps files no chapter glob covers."""

from __future__ import annotations

import json
import re
from collections import defaultdict

from pydantic import BaseModel, TypeAdapter, ValidationError

from fidus.agent.budget import estimate_tokens
from fidus.agent.guards import wrap_untrusted
from fidus.agent.prompts import render
from fidus.config.models import FidusConfig
from fidus.config.outline import Outline
from fidus.errors import ProviderError
from fidus.llm.base import Provider
from fidus.llm.types import Usage, user
from fidus.log import get_logger

log = get_logger("triage")


class TriageItem(BaseModel):
    file: str
    chapter: str | None = None
    confidence: float = 0.0


_items = TypeAdapter(list[TriageItem])
ROOT = "./"  # collapsed marker for files at a repository's root


def _collapse(files: list[str], max_entries: int = 200) -> list[str]:
    if len(files) <= max_entries:
        return files
    by_dir: dict[str, int] = defaultdict(int)
    for f in files:
        alias, path = f.split(":", 1)
        by_dir[f"{alias}:{path.rsplit('/', 1)[0] + '/' if '/' in path else ROOT}"] += 1
    return [d for d, _ in sorted(by_dir.items(), key=lambda kv: -kv[1])][:max_entries]


def _parse(text: str) -> list[TriageItem]:
    """Find the first JSON array of objects anywhere in the reply (prose and fences tolerated)."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\[", text):
        try:
            value, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and all(isinstance(v, dict) for v in value):
            return _items.validate_python(value)
    raise ValueError("no JSON array in triage response")


def _in_dir(file: str, d: str) -> bool:
    if d.endswith(":" + ROOT):
        alias = d[: -len(ROOT) - 1]
        return file.startswith(alias + ":") and "/" not in file.split(":", 1)[1]
    return file.startswith(d)


def triage(
    files: list[str], outline: Outline, cfg: FidusConfig, provider: Provider
) -> tuple[dict[str, str | None], Usage]:
    """Return ({alias:path -> chapter id, or None for a confident "no documentation impact"},
    usage). Low-confidence or invalid results are omitted."""
    if not files:
        return {}, Usage()
    ids = {c.id for c in outline.chapters()}
    chapters = "\n".join(
        f"- {c.id}: {c.title} — {c.summary} globs: {', '.join(c.sources)}"
        for c in outline.chapters()
    )
    listed = _collapse(sorted(set(files)))
    prompt = render(
        "triage",
        chapters=chapters,
        files=wrap_untrusted("changed-files", "\n".join(f"- {f}" for f in listed)),
    )
    if estimate_tokens(len(prompt)) > cfg.budgets.triage.max_input_tokens:
        log.warning("triage prompt exceeds budget; leaving %d files unmapped", len(files))
        return {}, Usage()
    try:
        c = provider.complete(
            system="You are a precise classifier. Output JSON only.",
            messages=[user(prompt)],
            tools=[],
            max_tokens=cfg.budgets.triage.max_output_tokens,
            temperature=0.0,
        )
        items = _parse(c.message.text)
    except (ProviderError, ValueError, ValidationError, json.JSONDecodeError) as e:
        log.warning("triage failed (%s); files stay unmapped", e)
        return {}, Usage()
    threshold = cfg.limits.triage_confidence_threshold

    def accepted(i: TriageItem) -> bool:
        return i.confidence >= threshold and (i.chapter is None or i.chapter in ids)

    mapping: dict[str, str | None] = {}
    dirs = {i.file: i for i in items if i.file.endswith("/")}
    for i in items:
        if accepted(i) and not i.file.endswith("/"):
            mapping[i.file] = i.chapter
    # Expand collapsed directory answers back to files.
    for f in files:
        if f in mapping:
            continue
        for d, item in dirs.items():
            if _in_dir(f, d) and accepted(item):
                mapping[f] = item.chapter
                break
    return {f: ch for f, ch in mapping.items() if f in set(files)}, c.usage
