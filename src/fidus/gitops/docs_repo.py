"""Git operations on the docs repository working tree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fidus.errors import GitError
from fidus.gitops.runner import git


@dataclass
class Author:
    name: str
    email: str

    def env(self) -> dict[str, str]:
        return {
            "GIT_AUTHOR_NAME": self.name,
            "GIT_AUTHOR_EMAIL": self.email,
            "GIT_COMMITTER_NAME": self.name,
            "GIT_COMMITTER_EMAIL": self.email,
        }


class DocsRepo:
    def __init__(
        self,
        root: Path,
        *,
        token: str | None = None,
        host: str = "github.com",
        remote: str = "origin",
    ) -> None:
        self.root = root
        self.token = token
        self.host = host
        self.remote = remote

    def _git(
        self, *args: str, check: bool = True, env: dict[str, str] | None = None, auth: bool = False
    ) -> str:
        proc = git(
            list(args),
            self.root,
            token=self.token if auth else None,
            host=self.host,
            check=check,
            env=env,
        )
        return proc.stdout

    # -- inspection ---------------------------------------------------------------------------
    def is_repo(self) -> bool:
        return git(["rev-parse", "--git-dir"], self.root, check=False).returncode == 0

    def has_remote(self) -> bool:
        return self.remote in self._git("remote").split()

    def remote_default_branch(self) -> str | None:
        out = self._git("symbolic-ref", f"refs/remotes/{self.remote}/HEAD", check=False).strip()
        return out.rsplit("/", 1)[-1] if out else None

    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def start_ref(self) -> str | None:
        """The current branch name, or the commit sha when HEAD is detached (Actions)."""
        branch = self._git("symbolic-ref", "--short", "-q", "HEAD", check=False).strip()
        return branch or self.rev("HEAD")

    def restore(self, ref: str, scratch_paths: list[str]) -> None:
        """Check `ref` out again, dropping leftovers of an interrupted run in `scratch_paths`."""
        on_ref = self.current_branch() == ref or self.rev("HEAD") == ref
        if on_ref and not self.status_paths():
            return
        self._git("checkout", "-f", "-q", ref)
        existing = [p for p in scratch_paths if (self.root / p).exists()]
        if existing:
            self._git("clean", "-fdq", "--", *existing)

    def human_lines(self, path: str, bot_email: str, limit: int = 40) -> list[str]:
        """Lines of `path` (at HEAD) written by someone other than the bot after the bot first
        took over the file. Lines keep this status even after later bot edits preserve them.
        Empty if the bot never touched the file (docs adopted as-is are not flagged)."""
        log = self._git("log", "--format=%H %ae", "--", path, check=False).splitlines()
        bot_commits = [ln.split(" ", 1)[0] for ln in log if ln.endswith(" " + bot_email)]
        if not bot_commits:
            return []
        first_bot = bot_commits[-1]  # log is newest first
        out = self._git("blame", "--line-porcelain", f"{first_bot}..HEAD", "--", path, check=False)
        lines: list[str] = []
        boundary, author = False, ""
        for raw in out.splitlines():
            if raw.startswith("\t"):
                text = raw[1:].strip()
                if not boundary and author != f"<{bot_email}>" and len(text) >= 12:
                    lines.append(text)
                boundary, author = False, ""
            elif raw == "boundary":
                boundary = True
            elif raw.startswith("author-mail "):
                author = raw[len("author-mail ") :]
        return list(dict.fromkeys(lines))[:limit]

    def rev(self, ref: str) -> str | None:
        out = git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], self.root, check=False)
        return out.stdout.strip() or None

    def show(self, ref: str, path: str) -> str | None:
        proc = git(["show", f"{ref}:{path}"], self.root, check=False)
        return proc.stdout if proc.returncode == 0 else None

    def is_ancestor(self, a: str, b: str) -> bool:
        return git(["merge-base", "--is-ancestor", a, b], self.root, check=False).returncode == 0

    def commits_between(self, base: str, head: str) -> list[tuple[str, str]]:
        """[(sha, author email)] for commits in base..head."""
        out = self._git("log", "--format=%H %ae", f"{base}..{head}")
        return [tuple(line.split(" ", 1)) for line in out.splitlines() if line.strip()]  # type: ignore[misc]

    def _tracked(self, path: str) -> bool:
        return bool(self._git("ls-files", "--", path, check=False).strip())

    def status_paths(self) -> list[str]:
        out = self._git("status", "--porcelain", "--untracked-files=all")
        paths = []
        for line in out.splitlines():
            p = line[3:]
            if " -> " in p:
                p = p.split(" -> ", 1)[1]
            paths.append(p.strip('"'))
        return paths

    def diff_patch(self, base: str) -> str:
        self._git("add", "-A", "--intent-to-add", ".")
        return self._git("diff", base)

    # -- mutation -----------------------------------------------------------------------------
    def fetch(self, *refspecs: str) -> None:
        for spec in refspecs:
            # A missing remote branch is fine; everything else should surface.
            proc = git(
                ["fetch", "--no-tags", self.remote, spec],
                self.root,
                token=self.token,
                host=self.host,
                check=False,
            )
            if proc.returncode == 0:
                continue
            if "couldn't find remote ref" not in proc.stderr:
                raise GitError(f"git fetch {spec} failed: {proc.stderr.strip()}")
            # The branch is gone remotely: forget any stale remote-tracking ref for it.
            if ":" in spec:
                self._git("update-ref", "-d", spec.split(":", 1)[1], check=False)

    def checkout_new(self, branch: str, start: str) -> None:
        self._git("checkout", "-B", branch, start)

    def rebase(self, onto: str, author: Author) -> bool:
        proc = git(["rebase", onto], self.root, check=False, env=author.env())
        if proc.returncode != 0:
            git(["rebase", "--abort"], self.root, check=False)
            return False
        return True

    def reset_hard(self, ref: str) -> None:
        self._git("reset", "--hard", ref)
        self._git("clean", "-fd")

    def commit_all(self, paths: list[str], message: str, author: Author) -> bool:
        existing = [p for p in paths if (self.root / p).exists() or self._tracked(p)]
        if existing:
            self._git("add", "-A", "--", *existing)
        if not self._git("diff", "--cached", "--name-only").strip():
            return False
        self._git("commit", "-m", message, env=author.env())
        return True

    def push(self, branch: str, *, lease: str | None = None, force: bool = False) -> None:
        args = ["push", self.remote, f"HEAD:refs/heads/{branch}"]
        if force:
            # With no known remote sha, an empty lease means "the branch must not exist".
            args.insert(1, f"--force-with-lease=refs/heads/{branch}:{lease or ''}")
        self._git(*args, auth=True)

    def write_file_commit(
        self, path: str, content: str, message: str, author: Author, parent: str | None
    ) -> str:
        """Create a commit containing exactly one file, without touching the work tree or index."""
        blob = git(["hash-object", "-w", "--stdin"], self.root, input=content).stdout.strip()
        tree = git(["mktree"], self.root, input=f"100644 blob {blob}\t{path}\n").stdout.strip()
        args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
        return git(args, self.root, env=author.env()).stdout.strip()

    def push_commit(self, sha: str, branch: str, lease: str | None) -> None:
        """Push `sha` to `branch`; the lease ensures nobody moved it since we read it."""
        self._git(
            "push",
            f"--force-with-lease=refs/heads/{branch}:{lease or ''}",
            self.remote,
            f"{sha}:refs/heads/{branch}",
            auth=True,
        )

    def delete_remote_branch(self, branch: str) -> None:
        self._git("push", self.remote, "--delete", branch, auth=True, check=False)
