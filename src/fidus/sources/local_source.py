"""Triggers for local `path:` sources: one per first-parent commit since the cursor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fidus.gitops.runner import git
from fidus.sources.trigger import ChangedFile, Trigger

SEP = "\x1f"


def local_triggers(
    root: Path,
    repo_key: str,
    alias: str,
    *,
    since_sha: str | None,
    since_time: datetime | None,
    limit: int,
) -> list[Trigger]:
    if not (root / ".git").exists():
        return []
    args = ["log", "--first-parent", "--reverse", f"--format=%H{SEP}%cI{SEP}%s{SEP}%b%x1e"]
    if (
        since_sha
        and git(["cat-file", "-e", f"{since_sha}^{{commit}}"], root, check=False).returncode
    ):
        since_sha = None  # history was rewritten or is shallow: fall back to the timestamp
    if since_sha:
        args.append(f"{since_sha}..HEAD")
    elif since_time:
        args.append(f"--since={since_time.isoformat()}")
    else:
        args += ["-n", str(limit)]
    out = git(args, root, check=False).stdout
    triggers: list[Trigger] = []
    for record in out.split("\x1e"):
        if len(triggers) >= limit:
            break  # oldest first; the rest wait for the next run
        record = record.strip("\n")
        if not record.strip():
            continue
        sha, when, subject, body = [*record.split(SEP), "", "", ""][:4]
        merged_at = datetime.fromisoformat(when.strip()).astimezone(UTC) if when.strip() else None
        if not since_sha and since_time and merged_at and merged_at <= since_time:
            continue  # `git log --since` is inclusive; the cursor itself was already processed
        files = _changed_files(root, sha)
        triggers.append(
            Trigger(
                repo=repo_key,
                alias=alias,
                title=subject.strip(),
                sha=sha.strip(),
                body=body.strip(),
                merged_at=merged_at,
                files=files,
            )
        )
    return triggers[:limit]  # oldest first; the rest wait for the next run


def is_ancestor(root: Path, a: str, b: str) -> bool:
    """True if commit `a` is an ancestor of (or equal to) commit `b`."""
    return git(["merge-base", "--is-ancestor", a, b], root, check=False).returncode == 0


def commit_time(root: Path, sha: str) -> datetime | None:
    out = git(["show", "-s", "--format=%cI", sha], root, check=False).stdout.strip()
    return datetime.fromisoformat(out).astimezone(UTC) if out else None


def _changed_files(root: Path, sha: str) -> list[ChangedFile]:
    stat = git(
        ["show", "--format=", "--numstat", "-M", "--diff-merges=first-parent", sha.strip()],
        root,
        check=False,
    ).stdout
    files: list[ChangedFile] = []
    for line in stat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, dele, path = parts
        prev = None
        if " => " in path:
            prev, path = _split_rename(path)
        patch = git(
            ["show", "--format=", "--diff-merges=first-parent", sha.strip(), "--", path],
            root,
            check=False,
        ).stdout
        files.append(
            ChangedFile(
                path=path,
                status="renamed" if prev else "modified",
                additions=int(add) if add.isdigit() else 0,
                deletions=int(dele) if dele.isdigit() else 0,
                patch=patch or None,
                previous_path=prev,
            )
        )
    return files


def _split_rename(path: str) -> tuple[str, str]:
    # "src/{old => new}/x.py" or "old.py => new.py"
    if "{" in path:
        pre, rest = path.split("{", 1)
        mid, post = rest.split("}", 1)
        old, new = mid.split(" => ")
        return (pre + old + post).replace("//", "/"), (pre + new + post).replace("//", "/")
    old, new = path.split(" => ")
    return old, new
