from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from fidus.errors import GitHubError
from fidus.github.client import GitHubClient
from fidus.github.pulls import GitHubAPI
from fidus.sources.github_source import github_triggers
from fidus.state.models import RepoCursor

API = "https://api.github.com"


def pr(n: int, merged: str | None, updated: str) -> dict[str, object]:
    return {
        "number": n,
        "title": f"PR {n}",
        "merged_at": merged,
        "updated_at": updated,
        "body": "b",
        "html_url": f"https://github.com/acme/app/pull/{n}",
        "merge_commit_sha": f"sha{n}",
        "changed_files": 1,
    }


@respx.mock
def test_pagination_and_auth_header() -> None:
    page2 = f"{API}/repos/acme/app/pulls?page=2"
    respx.get(f"{API}/repos/acme/app/pulls", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=[{"n": 3}])
    )
    route1 = respx.get(f"{API}/repos/acme/app/pulls").mock(
        return_value=httpx.Response(
            200, json=[{"n": 1}, {"n": 2}], headers={"link": f'<{page2}>; rel="next"'}
        )
    )
    c = GitHubClient("tok")
    assert [x["n"] for x in c.paginate("/repos/acme/app/pulls")] == [1, 2, 3]
    assert route1.calls[0].request.headers["authorization"] == "Bearer tok"


@respx.mock
def test_rate_limit_waits_then_retries() -> None:
    slept: list[float] = []
    respx.get(f"{API}/repos/a/b").mock(
        side_effect=[
            httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "retry-after": "7"},
                json={"message": "rate"},
            ),
            httpx.Response(200, json={"default_branch": "main"}),
        ]
    )
    api = GitHubAPI(GitHubClient("t", sleep=slept.append))
    assert api.default_branch("a/b") == "main"
    assert slept == [7.0]


@respx.mock
def test_errors_carry_status() -> None:
    respx.get(f"{API}/repos/a/b/branches/x").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    api = GitHubAPI(GitHubClient("t"))
    assert api.branch_exists("a/b", "x") is False
    respx.get(f"{API}/repos/a/b").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )
    with pytest.raises(GitHubError, match="Bad credentials"):
        api.repo("a/b")


@respx.mock
def test_github_triggers_respect_cursor_and_stop_early() -> None:
    cursor = RepoCursor(cursor_merged_at=datetime(2026, 9, 1, tzinfo=UTC), cursor_pr_numbers=[10])
    listing = respx.get(f"{API}/repos/acme/app/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                pr(12, "2026-09-03T00:00:00Z", "2026-09-03T00:00:00Z"),
                pr(13, None, "2026-09-02T12:00:00Z"),  # closed, not merged
                pr(11, "2026-09-02T00:00:00Z", "2026-09-02T00:00:00Z"),
                pr(
                    10, "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z"
                ),  # already processed at cursor
                pr(9, "2026-08-30T00:00:00Z", "2026-08-30T00:00:00Z"),  # older than cursor: stop
            ],
        )
    )
    for n in (11, 12):
        respx.get(f"{API}/repos/acme/app/pulls/{n}/files").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "filename": "src/a.py",
                        "status": "modified",
                        "additions": 1,
                        "deletions": 0,
                        "patch": "@@ +x",
                    },
                ],
            )
        )
    api = GitHubAPI(GitHubClient("t"))
    ts = github_triggers(api, "acme/app", "app", "main", cursor, since_override=None, limit=50)
    assert [t.number for t in ts] == [11, 12]  # oldest first
    assert ts[0].files[0].path == "src/a.py" and ts[0].url.endswith("/11")  # type: ignore[union-attr]
    params = listing.calls[0].request.url.params
    assert params["state"] == "closed" and params["base"] == "main" and params["sort"] == "updated"
