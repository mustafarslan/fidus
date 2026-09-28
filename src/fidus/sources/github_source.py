"""Triggers for GitHub sources: PRs merged since the cursor, with files and patches from the API."""

from __future__ import annotations

from datetime import UTC, datetime

from fidus.github.pulls import GitHubAPI, parse_time
from fidus.sources.trigger import ChangedFile, Trigger
from fidus.state.models import RepoCursor

API_FILE_CAP = 3000  # GitHub's hard limit on files listed per PR


def github_triggers(
    api: GitHubAPI,
    slug: str,
    alias: str,
    base: str,
    cursor: RepoCursor,
    *,
    since_override: datetime | None,
    limit: int,
) -> list[Trigger]:
    since = since_override or cursor.cursor_merged_at
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    seen_at_cursor = set(cursor.cursor_pr_numbers) if since_override is None else set()
    prs = []
    for pr in api.merged_since(slug, base, since):
        merged = parse_time(pr["merged_at"])
        if merged is None:
            continue
        if since is not None and (
            merged < since or (merged == since and pr["number"] in seen_at_cursor)
        ):
            continue
        prs.append((merged, pr))
    prs.sort(key=lambda x: (x[0], x[1]["number"]))
    triggers: list[Trigger] = []
    for merged, pr in prs[:limit]:
        files = []
        raw = api.pr_files(slug, pr["number"])
        for f in raw:
            files.append(
                ChangedFile(
                    path=f["filename"],
                    status=f.get("status", "modified"),
                    additions=f.get("additions", 0),
                    deletions=f.get("deletions", 0),
                    patch=f.get("patch"),
                    previous_path=f.get("previous_filename"),
                )
            )
        t = Trigger(
            repo=slug,
            alias=alias,
            title=pr.get("title") or "",
            number=pr["number"],
            sha=pr.get("merge_commit_sha"),
            url=pr.get("html_url"),
            body=pr.get("body") or "",
            merged_at=merged,
            files=files,
        )
        if len(raw) >= API_FILE_CAP or pr.get("changed_files", 0) > len(raw):
            t.notes.append(f"GitHub lists at most {API_FILE_CAP} files; some changes are not shown")
            t.omitted_files += max(0, pr.get("changed_files", 0) - len(raw))
        triggers.append(t)
    return triggers


def advance_cursor(cursor: RepoCursor, triggers: list[Trigger]) -> None:
    """Move the cursor to the newest processed trigger (handles equal merged_at timestamps)."""
    dated = [t for t in triggers if t.merged_at is not None]
    if not dated:
        return
    newest = max(t.merged_at for t in dated if t.merged_at is not None)
    if cursor.cursor_merged_at is not None and newest < cursor.cursor_merged_at:
        return  # never move backwards (e.g. a re-run with --since)
    numbers = [t.number for t in dated if t.merged_at == newest and t.number is not None]
    if cursor.cursor_merged_at == newest:
        cursor.cursor_pr_numbers = sorted(set(cursor.cursor_pr_numbers) | set(numbers))
    else:
        cursor.cursor_merged_at = newest
        cursor.cursor_pr_numbers = sorted(numbers)
