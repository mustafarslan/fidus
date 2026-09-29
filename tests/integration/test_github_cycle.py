"""Simulated nights against a real bare git remote and a fake GitHub API (respx).

Covers the rolling-branch lifecycle: create PR, accumulate, quiet night, rebuild on conflict,
pause on human edits, closed-unmerged (stale) branch, merge -> state read from default.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fidus.pipeline.bootstrap import bootstrap
from fidus.pipeline.run import run
from fidus.pipeline.session import Options
from tests.conftest import APP_FILES, OUTLINE_YAML, commit, config_yaml, git, make_repo

API = "https://api.github.com/repos/acme/docs"


class FakeGitHub:
    """Just enough of the GitHub REST API for the docs repo, backed by a bare remote."""

    def __init__(self, remote: Path) -> None:
        self.remote = remote
        self.prs: list[dict[str, Any]] = []
        self.comments: list[str] = []

    def install(self, mock: respx.MockRouter) -> None:
        mock.get(API).mock(return_value=httpx.Response(200, json={"default_branch": "main"}))
        mock.get(f"{API}/pulls").mock(side_effect=self._list)
        mock.post(f"{API}/pulls").mock(side_effect=self._create)
        mock.patch(url__regex=rf"{API}/pulls/\d+$").mock(side_effect=self._update)
        mock.post(url__regex=rf"{API}/issues/\d+/labels").mock(
            return_value=httpx.Response(200, json=[])
        )
        mock.post(url__regex=rf"{API}/issues/\d+/comments").mock(side_effect=self._comment)
        mock.post(url__regex=rf"{API}/pulls/\d+/requested_reviewers").mock(
            return_value=httpx.Response(201, json={})
        )
        mock.delete(url__regex=rf"{API}/git/refs/heads/.+").mock(side_effect=self._delete_ref)

    def _list(self, request: httpx.Request) -> httpx.Response:
        head = request.url.params.get("head", "")
        branch = head.split(":", 1)[-1]
        state = request.url.params.get("state", "open")
        found = [
            {
                **p,
                "head": {
                    "ref": p["head"],
                    "sha": p.get("frozen_sha") or remote_sha(self.remote, p["head"]),
                },
            }
            for p in self.prs
            if p["state"] == state and p["head"] == branch
        ]
        return httpx.Response(200, json=found[::-1])  # newest first

    def _create(self, request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        pr = {
            "number": len(self.prs) + 1,
            "state": "open",
            "head": data["head"],
            "title": data["title"],
            "body": data["body"],
            "html_url": f"https://github.com/acme/docs/pull/{len(self.prs) + 1}",
        }
        self.prs.append(pr)
        return httpx.Response(201, json=pr)

    def _update(self, request: httpx.Request) -> httpx.Response:
        n = int(re.search(r"/pulls/(\d+)$", str(request.url)).group(1))  # type: ignore[union-attr]
        pr = self.prs[n - 1]
        pr.update(json.loads(request.content))
        return httpx.Response(200, json=pr)

    def _comment(self, request: httpx.Request) -> httpx.Response:
        self.comments.append(json.loads(request.content)["body"])
        return httpx.Response(201, json={})

    def _delete_ref(self, request: httpx.Request) -> httpx.Response:
        branch = str(request.url).split("/git/refs/heads/", 1)[1]
        git(self.remote, "update-ref", "-d", f"refs/heads/{branch}")
        return httpx.Response(204)

    def close(self, pr: dict[str, Any], merged: bool = False) -> None:
        """Like GitHub: a closed PR's head sha is frozen at close time."""
        pr["frozen_sha"] = remote_sha(self.remote, pr["head"])
        pr["state"] = "closed"
        if merged:
            pr["merged_at"] = "2026-09-27T12:00:00Z"

    def open_pr(self, head: str) -> dict[str, Any] | None:
        return next((p for p in self.prs if p["state"] == "open" and p["head"] == head), None)


def remote_sha(remote: Path, branch: str) -> str | None:
    out = git(remote, "for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}").strip()
    return out or None


def merge_into_main(root: Path, remote: Path, branch: str, gh: FakeGitHub) -> None:
    helper = root / "helper"
    if not helper.exists():
        git(root, "clone", "-q", str(remote), str(helper))
    git(helper, "fetch", "-q", "origin")
    git(helper, "checkout", "-q", "main")
    git(helper, "reset", "-q", "--hard", "origin/main")
    git(helper, "merge", "-q", "--no-ff", "-m", f"Merge {branch}", f"origin/{branch}")
    git(helper, "push", "-q", "origin", "main")
    pr = gh.open_pr(branch)
    assert pr is not None
    gh.close(pr, merged=True)


def human_edit(root: Path, remote: Path, branch: str, path: str, text: str) -> None:
    helper = root / "helper"
    if not helper.exists():
        git(root, "clone", "-q", str(remote), str(helper))
    git(helper, "fetch", "-q", "origin")
    git(helper, "checkout", "-q", "-B", branch, f"origin/{branch}")
    p = helper / path
    p.write_text(p.read_text() + text)
    git(helper, "commit", "-qam", f"human edit on {branch}")
    git(helper, "push", "-q", "origin", f"{branch}:{branch}")


class Night:
    """Each night starts from a fresh checkout, like a GitHub Actions runner."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.n = 0

    def checkout(self, remote: Path) -> Path:
        self.n += 1
        work = self.root / f"night{self.n}"
        git(self.root, "clone", "-q", str(remote), str(work))
        return work


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("FIDUS_GITHUB_TOKEN", "test-token-123")
    monkeypatch.setenv("FIDUS_DOCS_REPO", "acme/docs")
    app = make_repo(tmp_path / "app", APP_FILES)
    seed = make_repo(
        tmp_path / "seed", {"fidus.yaml": config_yaml(), "fidus.outline.yaml": OUTLINE_YAML}
    )
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(remote))
    return {
        "root": tmp_path,
        "app": app,
        "remote": remote,
        "night": Night(tmp_path),
        "gh": FakeGitHub(remote),
    }


def do_run(work: Path) -> Any:
    return run(Options(work / "fidus.yaml"))


def test_rolling_branch_lifecycle(world: dict[str, Any]) -> None:
    root, app, remote, night, gh = (
        world["root"],
        world["app"],
        world["remote"],
        world["night"],
        world["gh"],
    )
    with respx.mock(assert_all_called=False) as mock:
        gh.install(mock)

        # Night 0: bootstrap PR, merged by a human.
        report = bootstrap(Options(night.checkout(remote) / "fidus.yaml"))
        assert not report.failed and gh.open_pr("fidus/bootstrap")
        merge_into_main(root, remote, "fidus/bootstrap", gh)

        # Night 1: an auth change -> fidus/sync branch + PR.
        commit(
            app, {"src/auth/login.py": "def login(user, ttl=3600):\n    return True\n"}, "feat: ttl"
        )
        work1 = night.checkout(remote)
        r1 = do_run(work1)
        assert git(work1, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"  # branch restored
        assert git(work1, "status", "--porcelain") == ""
        pr = gh.open_pr("fidus/sync")
        assert pr is not None and r1.changed_chapters == ["auth"]
        assert "feat: ttl" in json.dumps(pr) or "Authentication" in pr["body"]
        assert "fidus/sync" not in [p["head"] for p in gh.prs[:1]]

        # Night 2: a data-model change -> same PR updated, not a new one.
        commit(app, {"src/db/models.py": "class Model:\n    id: int\n"}, "feat: ids")
        do_run(night.checkout(remote))
        assert len([p for p in gh.prs if p["head"] == "fidus/sync"]) == 1
        body = gh.open_pr("fidus/sync")["body"]  # type: ignore[index]
        assert "2.1 Authentication" in body and "1.2 The Data Model" in body
        assert "Accumulated over 2 nightly run(s)" in body

        # Night 3: quiet night -> nothing pushed.
        before = remote_sha(remote, "fidus/sync")
        r3 = do_run(night.checkout(remote))
        assert r3.noop, json.dumps(r3.to_json(), default=str)[:1500]
        assert remote_sha(remote, "fidus/sync") == before

        # Night 4: main's auth chapter edited (conflicts with the bot branch) -> rebuild.
        human_edit(root, remote, "main", "docs/part-2/01-auth.md", "\nEdited on main.\n")
        commit(
            app,
            {"src/auth/login.py": "def login(user, ttl=60):\n    return True\n"},
            "feat: short ttl",
        )
        r4 = do_run(night.checkout(remote))
        assert r4.rebuilt
        work = night.checkout(remote)
        git(work, "fetch", "-q", "origin", "fidus/sync")
        assert git(work, "merge-base", "--is-ancestor", "origin/main", "origin/fidus/sync") == ""
        rebuilt_doc = git(work, "show", "origin/fidus/sync:docs/part-2/01-auth.md")
        assert "Edited on main." in rebuilt_doc  # regenerated on top of main, nothing lost

        # Night 5: a human committed on the bot branch, and main conflicts -> pause, touch nothing.
        human_edit(root, remote, "fidus/sync", "docs/part-2/01-auth.md", "\nReviewer tweak.\n")
        human_edit(root, remote, "main", "docs/part-2/01-auth.md", "\nAnother main edit.\n")
        sync_before = remote_sha(remote, "fidus/sync")
        r5 = do_run(night.checkout(remote))
        assert r5.paused and remote_sha(remote, "fidus/sync") == sync_before
        assert "Fidus is paused" in gh.open_pr("fidus/sync")["body"]  # type: ignore[index]

        # The reviewer closes the PR without merging -> its changes are skipped, not re-opened.
        gh.close(gh.open_pr("fidus/sync"))  # type: ignore[arg-type]
        rejected_sha = remote_sha(remote, "fidus/sync")
        r6 = do_run(night.checkout(remote))
        assert r6.noop and r6.triggers_collected == 0
        assert remote_sha(remote, "fidus/sync") == rejected_sha  # kept: it holds the cursor
        assert gh.open_pr("fidus/sync") is None

        # A new change -> a fresh PR from main, mentioning the skipped batch.
        commit(
            app, {"src/db/models.py": "class Model:\n    id: int\n    name: str\n"}, "feat: names"
        )
        r7 = do_run(night.checkout(remote))
        new_pr = gh.open_pr("fidus/sync")
        assert new_pr is not None and new_pr["number"] != pr["number"]
        assert r7.triggers_collected == 1 and r7.changed_chapters == ["data-model"]
        assert "Skipped 3 change(s)" in new_pr["body"]
        work = night.checkout(remote)
        git(work, "fetch", "-q", "origin", "fidus/sync")
        assert "Reviewer tweak." not in git(
            work, "show", "origin/fidus/sync:docs/part-2/01-auth.md"
        )

        # Merge it -> next night reads state from main, pending cleared, nothing to do.
        merge_into_main(root, remote, "fidus/sync", gh)
        r8 = do_run(night.checkout(remote))
        assert r8.noop
        state = json.loads(git(night.checkout(remote), "show", "origin/main:.fidus/state.json"))
        assert (
            state["base"]["repos"]["local:app"]["cursor_sha"]
            == git(app, "rev-parse", "HEAD").strip()
        )


def test_quiet_nights_keep_the_triage_cache(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import fidus.pipeline.run as runmod
    from fidus.llm.types import Usage

    root, app, remote, night, gh = (
        world["root"],
        world["app"],
        world["remote"],
        world["night"],
        world["gh"],
    )
    calls: list[list[str]] = []

    def triage(files, *a, **k):  # type: ignore[no-untyped-def]
        calls.append(list(files))
        return {f: None for f in files}, Usage()  # confident: no documentation impact

    monkeypatch.setattr(runmod, "triage", triage)
    with respx.mock(assert_all_called=False) as mock:
        gh.install(mock)
        bootstrap(Options(night.checkout(remote) / "fidus.yaml"))
        merge_into_main(root, remote, "fidus/bootstrap", gh)
        commit(app, {"tools/gen.py": "print(1)\n"}, "chore: tooling")

        r1 = do_run(night.checkout(remote))
        assert r1.noop and gh.open_pr("fidus/sync") is None  # deferred: no PR
        assert calls == [["app:tools/gen.py"]]
        assert remote_sha(remote, "fidus/cache")  # but the decision was saved
        cache = json.loads(git(remote, "show", "fidus/cache:triage-cache.json"))
        assert cache["entries"] == {"app:tools/gen.py": None}

        r2 = do_run(night.checkout(remote))  # same change re-collected, no new triage call
        assert r2.noop and len(calls) == 1
        before = remote_sha(remote, "fidus/cache")
        do_run(night.checkout(remote))
        assert remote_sha(remote, "fidus/cache") == before  # unchanged cache: no push
