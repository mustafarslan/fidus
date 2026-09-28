"""Lifecycle of a rolling Fidus branch (fidus/sync, fidus/outline, fidus/bootstrap).

NONE      no remote branch                  -> start fresh from the default branch
OPEN      remote branch with an open PR     -> continue it (rebase onto default if behind)
STALE     branch left over after a merge    -> delete it, treat as NONE
REJECTED  its last PR was closed unmerged   -> keep the branch (it holds the advanced cursor, so
                                               the rejected changes are skipped); the next PR
                                               starts fresh from the default branch over it
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fidus.github.pulls import GitHubAPI
from fidus.gitops.docs_repo import Author, DocsRepo
from fidus.log import get_logger

log = get_logger("branch")


class BranchState(StrEnum):
    NONE = "none"
    OPEN = "open"
    STALE = "stale"
    REJECTED = "rejected"


@dataclass
class BranchInfo:
    name: str
    state: BranchState
    remote_sha: str | None
    pr: dict[str, Any] | None


def inspect_branch(
    api: GitHubAPI,
    repo: DocsRepo,
    slug: str,
    branch: str,
    default: str,
    *,
    keep_rejected: bool = False,
) -> BranchInfo:
    repo.fetch(
        f"+refs/heads/{default}:refs/remotes/{repo.remote}/{default}",
        f"+refs/heads/{branch}:refs/remotes/{repo.remote}/{branch}",
    )
    remote_sha = repo.rev(f"{repo.remote}/{branch}")
    pr = api.find_open_pr(slug, branch) if remote_sha else None
    if remote_sha and pr:
        return BranchInfo(branch, BranchState.OPEN, remote_sha, pr)
    if remote_sha and keep_rejected:
        last = api.closed_pr_for_sha(slug, branch, remote_sha)
        if last is not None and not last.get("merged_at"):
            log.info("last PR from %s was closed without merging; skipping its changes", branch)
            return BranchInfo(branch, BranchState.REJECTED, remote_sha, None)
    if remote_sha:
        log.info("branch %s has no open PR (merged or closed); deleting it", branch)
        api.delete_branch(slug, branch)
        return BranchInfo(branch, BranchState.STALE, None, None)
    return BranchInfo(branch, BranchState.NONE, None, None)


@dataclass
class RebaseOutcome:
    force_push: bool = False
    rebuild: bool = False
    paused: str | None = None


def prepare_branch(repo: DocsRepo, info: BranchInfo, default: str, author: Author) -> RebaseOutcome:
    """Check out the working branch and bring it up to date with the default branch.

    Conflict policy: never 3-way-merge prose. If only Fidus committed to the branch, reset
    it to the default branch and regenerate the affected chapters. If a human committed to
    it, pause instead of destroying their work.
    """
    remote_default = f"{repo.remote}/{default}"
    if info.state != BranchState.OPEN:
        repo.checkout_new(info.name, remote_default)
        # A rejected branch still exists remotely; the next PR replaces it.
        return RebaseOutcome(force_push=info.state == BranchState.REJECTED)
    repo.checkout_new(info.name, f"{repo.remote}/{info.name}")
    if repo.is_ancestor(remote_default, "HEAD"):
        return RebaseOutcome()
    human = [
        sha
        for sha, email in repo.commits_between(remote_default, "HEAD")
        if email.lower() != author.email.lower()
    ]
    if repo.rebase(remote_default, author):
        log.info("rebased %s onto %s", info.name, default)
        return RebaseOutcome(force_push=True)
    if human:
        return RebaseOutcome(
            paused=(
                f"`{info.name}` has commits from someone other than Fidus that conflict with "
                f"`{default}`. Merge or close this PR (or resolve the conflict) and Fidus will resume."
            )
        )
    log.warning("rebase of %s conflicted; rebuilding from %s", info.name, default)
    repo.reset_hard(remote_default)
    return RebaseOutcome(force_push=True, rebuild=True)
