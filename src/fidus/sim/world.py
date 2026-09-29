"""The simulated world: source repos, the docs repo, developers merging PRs, a human reviewer."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fidus.sim.server import FakeGitHub, iso

DEV = ("Dev Eloper", "dev@example.com")
REVIEWER = ("Rae Viewer", "reviewer@example.com")


def git(
    cwd: Path,
    *args: str,
    when: datetime | None = None,
    who: tuple[str, str] = DEV,
    input: str | None = None,
) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": who[0],
        "GIT_AUTHOR_EMAIL": who[1],
        "GIT_COMMITTER_NAME": who[0],
        "GIT_COMMITTER_EMAIL": who[1],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = iso(when)
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, input=input
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip()}")
    return proc.stdout


def write_files(root: Path, changes: dict[str, str | None]) -> None:
    for rel, content in changes.items():
        p = root / rel
        if content is None:
            p.unlink(missing_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")


class World:
    def __init__(self, root: Path, gh: FakeGitHub) -> None:
        self.root = root
        self.gh = gh
        self.work = root / "work"  # developers' and the reviewer's clones
        self.work.mkdir(parents=True, exist_ok=True)

    # -- setup --------------------------------------------------------------------------------
    def create_repo(self, slug: str, files: dict[str, str | None], when: datetime) -> Path:
        bare = self.gh.bare(slug)
        bare.parent.mkdir(parents=True, exist_ok=True)
        git(bare.parent, "init", "-q", "--bare", "-b", self.gh.default_branch, str(bare))
        clone = self.clone(slug)
        write_files(clone, files)
        git(clone, "add", "-A")
        git(clone, "commit", "-qm", "Initial commit", when=when)
        git(clone, "push", "-q", "origin", f"HEAD:{self.gh.default_branch}")
        return clone

    def clone(self, slug: str) -> Path:
        dest = self.work / slug.replace("/", "__")
        if not dest.exists():
            git(self.work, "clone", "-q", str(self.gh.bare(slug)), str(dest))
        git(dest, "fetch", "-q", "origin")
        return dest

    def fresh_checkout(self, slug: str, name: str) -> Path:
        """A clean clone like the one an Actions runner gets each night."""
        dest = self.root / "nights" / name
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        git(dest.parent, "clone", "-q", str(self.gh.bare(slug)), str(dest))
        return dest

    # -- developers ---------------------------------------------------------------------------
    def merge_pr(
        self, slug: str, title: str, body: str, changes: dict[str, str | None], when: datetime
    ) -> dict[str, Any]:
        """A developer opens a PR with `changes` and it gets merged (merge commit), at `when`."""
        clone = self.clone(slug)
        main = self.gh.default_branch
        git(clone, "checkout", "-q", "-B", main, f"origin/{main}")
        number = len(self.gh.prs.get(slug, [])) + 1
        branch = f"feature/pr-{number}"
        git(clone, "checkout", "-q", "-b", branch)
        write_files(clone, changes)
        git(clone, "add", "-A")
        git(clone, "commit", "-qm", title, when=when)
        head = git(clone, "rev-parse", "HEAD").strip()
        git(clone, "checkout", "-q", main)
        git(
            clone,
            "merge",
            "-q",
            "--no-ff",
            "-m",
            f"Merge pull request #{number}: {title}",
            branch,
            when=when,
        )
        git(clone, "push", "-q", "origin", f"{main}:{main}")
        merge_sha = git(clone, "rev-parse", "HEAD").strip()
        files = self._pr_files(clone, f"{merge_sha}^1", merge_sha)
        self.gh.now = when
        pr = self.gh.register_pr(
            slug,
            title=title,
            body=body,
            state="closed",
            merged_at=iso(when),
            updated_at=iso(when),
            created_at=iso(when),
            merge_commit_sha=merge_sha,
            head={"ref": branch, "sha": head},
            files=files,
            changed_files=len(files),
        )
        return pr

    @staticmethod
    def _pr_files(clone: Path, base: str, head: str) -> list[dict[str, object]]:
        files: list[dict[str, object]] = []
        status: dict[str, str] = {}
        for line in git(clone, "diff", "--name-status", base, head).splitlines():
            code, _, path = line.partition("	")
            status[path] = code
        for line in git(clone, "diff", "--numstat", base, head).splitlines():
            add, dele, path = line.split("\t")
            patch = git(clone, "diff", base, head, "--", path)
            patch = patch.split("@@", 1)[1] if "@@" in patch else ""
            files.append(
                {
                    "filename": path,
                    "status": {"A": "added", "D": "removed", "M": "modified"}.get(
                        status.get(path, "M")[:1], "modified"
                    ),
                    "additions": int(add) if add.isdigit() else 0,
                    "deletions": int(dele) if dele.isdigit() else 0,
                    "patch": "@@" + patch if patch else None,
                }
            )
        return files

    # -- the human reviewer (acts on the docs repo) ---------------------------------------------
    def open_fidus_pr(self, docs: str) -> dict[str, object] | None:
        for branch in ("fidus/bootstrap", "fidus/sync", "fidus/outline"):
            pr = self.gh.find_pr(docs, branch)
            if pr is not None:
                return pr
        return None

    def review_merge(self, docs: str, when: datetime) -> str | None:
        pr = self.open_fidus_pr(docs)
        if pr is None:
            return None
        clone = self.clone(docs)
        main = self.gh.default_branch
        head = str(pr["head"]["ref"])  # type: ignore[index]
        git(clone, "checkout", "-q", "-B", main, f"origin/{main}")
        git(
            clone,
            "merge",
            "-q",
            "--no-ff",
            "-m",
            f"Merge pull request #{pr['number']} from {head}",
            f"origin/{head}",
            when=when,
            who=REVIEWER,
        )
        git(clone, "push", "-q", "origin", f"{main}:{main}")
        self.gh.now = when
        self.gh.close_pr(
            docs, pr, merged=True
        )  # branch stays (no auto-delete), like GitHub's default
        return f"merged #{pr['number']} ({head})"

    def review_close(self, docs: str, when: datetime) -> str | None:
        pr = self.open_fidus_pr(docs)
        if pr is None:
            return None
        self.gh.now = when
        self.gh.close_pr(docs, pr, merged=False)
        return f"closed #{pr['number']} without merging"

    def edit(
        self, docs: str, branch: str, changes: dict[str, str], when: datetime, message: str
    ) -> str:
        """The reviewer edits files on `branch` (append text to existing files)."""
        clone = self.clone(docs)
        git(clone, "checkout", "-q", "-B", branch, f"origin/{branch}")
        for rel, text in changes.items():
            p = clone / rel
            p.write_text(p.read_text(encoding="utf-8") + text, encoding="utf-8")
        git(clone, "commit", "-qam", message, when=when, who=REVIEWER)
        git(clone, "push", "-q", "origin", f"{branch}:{branch}")
        return f"{message} ({branch})"

    def commit_files(
        self, docs: str, branch: str, changes: dict[str, str | None], when: datetime, message: str
    ) -> str:
        clone = self.clone(docs)
        git(clone, "checkout", "-q", "-B", branch, f"origin/{branch}")
        write_files(clone, changes)
        git(clone, "add", "-A")
        git(clone, "commit", "-qm", message, when=when, who=REVIEWER)
        git(clone, "push", "-q", "origin", f"{branch}:{branch}")
        return f"{message} ({branch})"

    def read(self, slug: str, ref: str, path: str) -> str | None:
        proc = subprocess.run(
            ["git", "show", f"{ref}:{path}"], cwd=self.gh.bare(slug), capture_output=True, text=True
        )
        return proc.stdout if proc.returncode == 0 else None
