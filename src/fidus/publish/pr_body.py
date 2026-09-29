"""Render the sync PR description from structured state (a pure function)."""

from __future__ import annotations

from fidus.config.models import FidusConfig
from fidus.config.outline import Outline
from fidus.state.models import IncompleteEpisode, Pending, PendingTrigger

MARKER = "<!-- fidus:pr-body v1 -->"
SEVERITY_ICON = {"info": "ℹ️", "warn": "⚠️", "error": "❌"}


def _k(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def _esc(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _trigger_link(t: PendingTrigger) -> str:
    label = (
        f"{t.repo}#{t.number}"
        if t.number is not None
        else f"{t.repo.removeprefix('local:')}@{(t.sha or '')[:8]}"
    )
    return f"[{label}]({t.url})" if t.url else f"`{label}`"


def _chapter_link(outline: Outline, cfg: FidusConfig, chapter_id: str) -> str:
    try:
        ch = outline.get(chapter_id)
    except KeyError:
        return f"`{chapter_id}` (removed from outline)"
    return f"[{outline.number_of(chapter_id)} {_esc(ch.title)}]({cfg.docs.dir}/{ch.path})"


def _glob_for(alias_path: str) -> str:
    alias, path = alias_path.split(":", 1)
    return f"{alias}:{path.rsplit('/', 1)[0]}/**" if "/" in path else alias_path


def glob_suggestions(pending: Pending) -> list[str]:
    """Copy-pasteable outline `sources` additions for files no chapter glob covers."""
    by_chapter: dict[str | None, set[str]] = {}
    for t in pending.triggers:
        for f, chapter in t.triaged.items():
            by_chapter.setdefault(chapter, set()).add(_glob_for(f))
        for f in t.unmapped_files:
            by_chapter.setdefault(None, set()).add(_glob_for(f))
    lines = [
        f"`{chapter}`: add " + ", ".join(f"`{g}`" for g in sorted(globs))
        for chapter, globs in sorted((k, v) for k, v in by_chapter.items() if k is not None)
    ]
    if None in by_chapter:
        lines.append(
            "no chapter yet (add to a chapter's `sources`, or create a chapter): "
            + ", ".join(f"`{g}`" for g in sorted(by_chapter[None]))
        )
    return lines


def render_pr_body(
    pending: Pending,
    outline: Outline,
    cfg: FidusConfig,
    *,
    bootstrap: bool = False,
    retry: list[IncompleteEpisode] | None = None,
) -> str:
    rows: list[tuple[str, str, str]] = []  # chapter, trigger, reason
    for t in pending.triggers:
        for cid, reason in t.chapters.items():
            rows.append((cid, _trigger_link(t), reason))
    if pending.audit:
        for cid, reason in pending.audit.changed.items():
            rows.append((cid, "weekly audit", reason))
    for cid, reason in pending.consistency.items():
        rows.append((cid, "consistency pass", reason))
    for cid in pending.new_chapters:
        if not any(r[0] == cid for r in rows):
            rows.append((cid, "outline", "New chapter written from the outline."))

    order = {c.id: i for i, c in enumerate(outline.chapters())}
    rows.sort(key=lambda r: (order.get(r[0], 10_000), r[1]))
    chapters = {r[0] for r in rows}
    applied_triggers = [t for t in pending.triggers if t.status == "applied"]

    out: list[str] = []
    if bootstrap:
        out.append(f"## Fidus bootstrap: {len(chapters)} chapters of *{_esc(outline.title)}*")
        out.append(
            "_First full draft written from `fidus.outline.yaml`. Review it, edit freely, and merge; "
            "nightly syncs start from here._"
        )
    else:
        out.append(
            f"## Fidus sync: {len(applied_triggers)} source change(s) → {len(chapters)} chapter(s) updated"
        )
        since = pending.opened_at.date().isoformat() if pending.opened_at else "today"
        out.append(
            f"_Accumulated over {pending.runs} nightly run(s) since {since}. Merging advances Fidus's "
            "cursor; closing without merging skips these changes (delete the branch as well to "
            "have them regenerated)._"
        )

    if pending.paused_reason:
        out += ["", f"> [!WARNING]\n> **Fidus is paused:** {_esc(pending.paused_reason)}"]

    if rows:
        out += [
            "",
            "### Chapter updates",
            "",
            "| Chapter | Triggered by | Reason |",
            "|---|---|---|",
        ]
        for cid, trig, reason in rows:
            out.append(f"| {_chapter_link(outline, cfg, cid)} | {trig} | {_esc(reason)} |")

    no_impact = [t for t in pending.triggers if t.status == "no_impact"]
    if no_impact:
        out += [
            "",
            f"<details><summary>Reviewed, no documentation impact ({len(no_impact)})</summary>",
            "",
        ]
        out += [f"- {_trigger_link(t)} {_esc(t.title)}" for t in no_impact]
        out += ["", "</details>"]

    audit = pending.audit
    if audit and (audit.findings or audit.chapters_checked):
        date = audit.ran_at.date().isoformat() if audit.ran_at else ""
        out += [
            "",
            f"### Audit findings ({date}, {len(audit.chapters_checked)} chapters checked)",
            "",
        ]
        notable = [f for f in audit.findings if f.severity != "info"]
        if not notable:
            out.append("- ✅ No drift found.")
        for f in notable:
            out.append(f"- {SEVERITY_ICON[f.severity]} `{f.chapter}`: {_esc(f.text)}")

    attention: list[str] = []
    for t in pending.triggers:
        if t.unmapped_files:
            files = ", ".join(f"`{f}`" for f in t.unmapped_files[:8])
            more = f" (+{len(t.unmapped_files) - 8} more)" if len(t.unmapped_files) > 8 else ""
            attention.append(f"Unmapped files in {_trigger_link(t)}: {files}{more}")
        if t.status == "failed":
            attention.append(f"Could not process {_trigger_link(t)}; it will be retried.")
    for e in retry or []:
        if e.quarantined:
            attention.insert(
                0,
                f"**Needs a human:** `{e.chapter}` failed {e.attempts} times and is no longer "
                f"retried automatically ({_esc(e.reason)}). A later successful update of the "
                "chapter clears this.",
            )
        else:
            attention.append(f"Incomplete, retried next run: `{e.chapter}` ({_esc(e.reason)})")
    if pending.rejected:
        attention.append(
            f"Skipped {len(pending.rejected)} change(s) because the previous Fidus PR was closed "
            "without merging: " + ", ".join(f"`{r}`" for r in pending.rejected[:10])
        )
    for o in pending.orphans:
        attention.append(f"File not in outline: `{cfg.docs.dir}/{o}`")
    suggestions = glob_suggestions(pending)
    if suggestions:
        attention.append(
            "Suggested `sources` globs for `fidus.outline.yaml` (files currently matched by "
            "triage or by nothing):\n" + "\n".join(f"  - {s}" for s in suggestions)
        )
    if attention:
        out += ["", "### Needs attention", ""] + [f"- {a}" for a in attention]
        out.append("")
        out.append(
            "_Tip: add globs for unmapped files to a chapter's `sources` in `fidus.outline.yaml`._"
        )

    if audit and audit.structure_suggestions:
        out += ["", "### Structure suggestions (not applied)", ""]
        out += [f"- {_esc(s)}" for s in audit.structure_suggestions]
        out.append("")
        out.append(
            "_Edit `fidus.outline.yaml` to act on these; Fidus never restructures the book by itself._"
        )

    if pending.usage:
        out += [
            "",
            "<details><summary>Usage</summary>",
            "",
            "| Run | Model | Input | Output | Cache read | Est. cost |",
            "|---|---|---|---|---|---|",
        ]
        for u in pending.usage:
            cost = f"${u.cost_usd:.2f}" if u.cost_usd is not None else "n/a"
            out.append(
                f"| {u.run_at.date().isoformat()} | {u.provider}/{u.model} | {_k(u.input_tokens)} | "
                f"{_k(u.output_tokens)} | {_k(u.cache_read_tokens)} | {cost} |"
            )
        out += ["", "</details>"]

    out += [
        "",
        "---",
        "_Generated by [Fidus](https://github.com/mustafarslan/fidus). "
        "Protect hand-written passages with `<!-- fidus:keep -->` … `<!-- /fidus:keep -->`._",
        MARKER,
    ]
    body = "\n".join(out) + "\n"
    if len(body) > 64_000:  # GitHub's PR body limit is 65536
        body = (
            body[:63_000]
            + "\n\n… (truncated; see `.fidus/state.json` for the full record)\n"
            + MARKER
        )
    return body
