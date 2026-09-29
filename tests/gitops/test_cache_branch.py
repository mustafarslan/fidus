from __future__ import annotations

from pathlib import Path

from fidus.gitops.docs_repo import Author, DocsRepo
from fidus.publish.cache_branch import load_cache, merge_into, save_cache
from fidus.state.models import TriageCache
from tests.conftest import commit, git, make_repo

BOT = Author("Fidus", "bot@example.com")


def _clone(tmp_path: Path, remote: Path, name: str) -> DocsRepo:
    git(tmp_path, "clone", "-q", str(remote), str(tmp_path / name))
    return DocsRepo(tmp_path / name)


def test_cache_roundtrip_and_lease(tmp_path: Path) -> None:
    seed = make_repo(tmp_path / "seed", {"README.md": "x"})
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(remote))
    a, b = _clone(tmp_path, remote, "a"), _clone(tmp_path, remote, "b")

    loaded = load_cache(a, "fidus/cache")
    assert loaded.cache is None and loaded.sha is None
    cache = TriageCache(outline_hash="h1", entries={"app:x.py": "auth", "app:ci.yml": None})
    assert save_cache(a, "fidus/cache", cache, loaded, BOT)
    assert git(a.root, "status", "--porcelain") == ""  # work tree untouched

    got = load_cache(b, "fidus/cache")
    assert got.cache == cache
    state = TriageCache(outline_hash="h1", entries={"app:x.py": "overview"})
    merge_into(state, got.cache)
    assert state.entries == {"app:x.py": "overview", "app:ci.yml": None}  # state wins

    # A stale lease must not clobber a newer cache.
    stale = loaded  # tip before a's save
    commit(b.root, {"f": "1"}, "unrelated")
    assert not save_cache(
        b, "fidus/cache", TriageCache(outline_hash="h2", entries={"k": None}), stale, BOT
    )
    assert load_cache(b, "fidus/cache").cache == cache
