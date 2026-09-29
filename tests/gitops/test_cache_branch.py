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


def test_human_lines_since_last_bot_edit(tmp_path: Path) -> None:
    repo_dir = make_repo(tmp_path / "docs", {"docs/a.md": "# A\nHuman-written intro paragraph.\n"})
    repo = DocsRepo(repo_dir)
    assert repo.human_lines("docs/a.md", BOT.email) == []  # Fidus never edited it: nothing flagged
    git(
        repo_dir,
        "-c",
        "user.name=Fidus",
        "-c",
        f"user.email={BOT.email}",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "noop",
    )  # ignore: doesn't touch the file
    (repo_dir / "docs/a.md").write_text("# A\nBot rewrote the intro entirely.\n")
    git(repo_dir, "add", "-A")
    git(
        repo_dir,
        "commit",
        "-qm",
        "bot",
        env={
            "GIT_AUTHOR_NAME": "Fidus",
            "GIT_AUTHOR_EMAIL": BOT.email,
            "GIT_COMMITTER_NAME": "Fidus",
            "GIT_COMMITTER_EMAIL": BOT.email,
        },
    )
    commit(
        repo_dir,
        {"docs/a.md": "# A\nBot rewrote the intro entirely.\nHotfix note from a human on call.\n"},
        "human hotfix",
    )
    assert repo.human_lines("docs/a.md", BOT.email) == ["Hotfix note from a human on call."]


def test_human_lines_stay_protected_after_later_bot_edits(tmp_path: Path) -> None:
    bot_env = {
        "GIT_AUTHOR_NAME": "Fidus",
        "GIT_AUTHOR_EMAIL": BOT.email,
        "GIT_COMMITTER_NAME": "Fidus",
        "GIT_COMMITTER_EMAIL": BOT.email,
    }
    repo_dir = make_repo(tmp_path / "d", {"README.md": "x"})
    (repo_dir / "docs").mkdir()
    (repo_dir / "docs/a.md").write_text("# A\nWritten by the bot at bootstrap.\n")
    git(repo_dir, "add", "-A")
    git(repo_dir, "commit", "-qm", "bootstrap", env=bot_env)
    commit(
        repo_dir,
        {"docs/a.md": "# A\nWritten by the bot at bootstrap.\nA human hotfix note on main.\n"},
        "hotfix",
    )
    # A later bot edit keeps the human line but changes other text.
    (repo_dir / "docs/a.md").write_text(
        "# A\nRevised by the bot later on.\nA human hotfix note on main.\n"
    )
    git(repo_dir, "commit", "-qam", "sync", env=bot_env)
    assert DocsRepo(repo_dir).human_lines("docs/a.md", BOT.email) == [
        "A human hotfix note on main."
    ]
