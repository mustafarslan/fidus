"""The handful of repository and pull-request endpoints Fidus needs."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from fidus.errors import GitHubError
from fidus.github.client import GitHubClient


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GitHubAPI:
    def __init__(self, client: GitHubClient) -> None:
        self.c = client

    # -- repos -------------------------------------------------------------------------------
    def repo(self, slug: str) -> dict[str, Any]:
        result: dict[str, Any] = self.c.get(f"/repos/{slug}")
        return result

    def default_branch(self, slug: str) -> str:
        return str(self.repo(slug)["default_branch"])

    def branch_exists(self, slug: str, branch: str) -> bool:
        try:
            self.c.get(f"/repos/{slug}/branches/{branch}")
            return True
        except GitHubError as e:
            if e.status == 404:
                return False
            raise

    def delete_branch(self, slug: str, branch: str) -> None:
        try:
            self.c.delete(f"/repos/{slug}/git/refs/heads/{branch}")
        except GitHubError as e:
            if e.status not in (404, 422):
                raise

    # -- pulls -------------------------------------------------------------------------------
    def merged_since(
        self, slug: str, base: str, since: datetime | None
    ) -> Iterator[dict[str, Any]]:
        """Merged PRs into `base`, newest-updated first, stopping once updated_at < since.

        Safe because merged_at <= updated_at for every PR.
        """
        for pr in self.c.paginate(
            f"/repos/{slug}/pulls", state="closed", base=base, sort="updated", direction="desc"
        ):
            updated = parse_time(pr.get("updated_at"))
            if since is not None and updated is not None and updated < since:
                return
            if pr.get("merged_at"):
                yield pr

    def pr_files(self, slug: str, number: int) -> list[dict[str, Any]]:
        return list(self.c.paginate(f"/repos/{slug}/pulls/{number}/files"))

    def find_open_pr(self, slug: str, head_branch: str) -> dict[str, Any] | None:
        owner = slug.split("/")[0]
        prs = self.c.get(f"/repos/{slug}/pulls", state="open", head=f"{owner}:{head_branch}")
        return prs[0] if prs else None

    def closed_pr_for_sha(self, slug: str, head_branch: str, sha: str) -> dict[str, Any] | None:
        """The closed PR (if any) whose head commit is `sha`: robust against comments bumping
        an older closed PR from the same branch to the top of the list."""
        owner = slug.split("/")[0]
        prs = self.c.get(
            f"/repos/{slug}/pulls",
            state="closed",
            head=f"{owner}:{head_branch}",
            sort="updated",
            direction="desc",
            per_page=20,
        )
        return next((p for p in prs if (p.get("head") or {}).get("sha") == sha), None)

    def last_closed_pr(self, slug: str, head_branch: str) -> dict[str, Any] | None:
        owner = slug.split("/")[0]
        prs = self.c.get(
            f"/repos/{slug}/pulls",
            state="closed",
            head=f"{owner}:{head_branch}",
            sort="updated",
            direction="desc",
            per_page=1,
        )
        return prs[0] if prs else None

    def create_pr(
        self, slug: str, *, head: str, base: str, title: str, body: str, draft: bool = False
    ) -> dict[str, Any]:
        result: dict[str, Any] = self.c.post(
            f"/repos/{slug}/pulls",
            {
                "head": head,
                "base": base,
                "title": title,
                "body": body,
                "draft": draft,
                "maintainer_can_modify": True,
            },
        )
        return result

    def update_pr(self, slug: str, number: int, *, title: str, body: str) -> dict[str, Any]:
        result: dict[str, Any] = self.c.patch(
            f"/repos/{slug}/pulls/{number}", {"title": title, "body": body}
        )
        return result

    def add_labels(self, slug: str, number: int, labels: list[str]) -> None:
        if labels:
            self.c.post(f"/repos/{slug}/issues/{number}/labels", {"labels": labels})

    def request_reviewers(self, slug: str, number: int, users: list[str], teams: list[str]) -> None:
        if users or teams:
            # Reviewers are best-effort (e.g. a reviewer lacks access to the repo).
            with contextlib.suppress(GitHubError):
                self.c.post(
                    f"/repos/{slug}/pulls/{number}/requested_reviewers",
                    {"reviewers": users, "team_reviewers": teams},
                )

    def comment(self, slug: str, number: int, body: str) -> None:
        self.c.post(f"/repos/{slug}/issues/{number}/comments", {"body": body})
