"""The triage cache on its own branch (`fidus/cache`), so it survives nights that open no PR.

The branch only ever holds `triage-cache.json`; cursors and pending state stay on the PR
branch, so run progress remains gated on a human merging.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from fidus.errors import GitError
from fidus.gitops.docs_repo import Author, DocsRepo
from fidus.log import get_logger
from fidus.state.models import TriageCache

log = get_logger("cache")
FILE = "triage-cache.json"


@dataclass
class LoadedCache:
    cache: TriageCache | None
    sha: str | None  # remote branch tip, used as the push lease


def load_cache(repo: DocsRepo, branch: str) -> LoadedCache:
    repo.fetch(f"+refs/heads/{branch}:refs/remotes/{repo.remote}/{branch}")
    sha = repo.rev(f"{repo.remote}/{branch}")
    if sha is None:
        return LoadedCache(None, None)
    text = repo.show(sha, FILE)
    try:
        return LoadedCache(TriageCache.model_validate_json(text) if text else None, sha)
    except ValidationError:
        log.warning("ignoring unreadable %s on %s", FILE, branch)
        return LoadedCache(None, sha)


def merge_into(state_cache: TriageCache, loaded: TriageCache | None) -> None:
    """Fold branch-cached decisions into the state's cache (state entries win)."""
    if loaded is None or not loaded.entries:
        return
    if state_cache.entries and state_cache.outline_hash != loaded.outline_hash:
        return  # different outlines: the mapper will sort out which one is current
    state_cache.outline_hash = state_cache.outline_hash or loaded.outline_hash
    state_cache.entries = {**loaded.entries, **state_cache.entries}


def save_cache(
    repo: DocsRepo, branch: str, cache: TriageCache, loaded: LoadedCache, author: Author
) -> bool:
    """Push the cache if it differs from what the branch holds. Never fails the run."""
    if loaded.cache is not None and loaded.cache == cache:
        return False
    if not cache.entries and loaded.cache is None:
        return False
    content = cache.model_dump_json(indent=1) + "\n"
    try:
        sha = repo.write_file_commit(
            FILE, content, "fidus: update triage cache", author, loaded.sha
        )
        repo.push_commit(sha, branch, loaded.sha)
        log.info("saved %d triage decisions to %s", len(cache.entries), branch)
        return True
    except GitError as e:  # e.g. a concurrent update: harmless, next run retries
        log.warning("could not save the triage cache to %s: %s", branch, e)
        return False
