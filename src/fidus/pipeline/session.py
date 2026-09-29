"""Shared plumbing for run/bootstrap: config, working tree, providers, clones, episodes."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from fidus.agent.budget import Budget, RunBudget
from fidus.agent.context import EpisodeContext, Mode
from fidus.agent.episode import EpisodeResult, run_episode
from fidus.agent.guards import configured_secret_values
from fidus.agent.prompts import chapter_brief, read_text_or_none
from fidus.config.loader import load_config, load_outline, outline_path
from fidus.config.models import EpisodeBudget, FidusConfig
from fidus.config.outline import Outline
from fidus.errors import ConfigError, FidusError
from fidus.github.auth import docs_repo_slug, docs_token, source_token
from fidus.github.client import GitHubClient
from fidus.github.pulls import GitHubAPI
from fidus.gitops.docs_repo import Author, DocsRepo
from fidus.llm.base import Provider, make_provider
from fidus.llm.cost import UsageMeter
from fidus.log import get_logger
from fidus.sources.clone import SourceWorkspace, clone_source
from fidus.sources.trigger import Trigger

log = get_logger("session")


@dataclass
class Options:
    config_path: Path
    dry_run: bool = False
    output: Path | None = None
    provider: str | None = None  # e.g. "fake" overrides llm.provider
    model: str | None = None  # overrides llm.model
    base_url: str | None = None  # overrides llm.base_url
    since: datetime | None = None
    chapters: set[str] | None = None
    no_push: bool = False
    now: datetime | None = None


def slug_from_remote(url: str) -> str | None:
    m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url.strip())
    return m.group(1) if m else None


@dataclass
class Session:
    opts: Options
    cfg: FidusConfig
    outline: Outline | None
    repo_root: Path  # the real docs repo checkout
    work_root: Path  # where edits happen (a scratch copy in dry-run mode)
    now: datetime
    host: str
    token: str | None = None
    api: GitHubAPI | None = None
    slug: str | None = None
    repo: DocsRepo | None = None
    workspaces: dict[str, SourceWorkspace] = field(default_factory=dict)
    meter: UsageMeter = field(default_factory=UsageMeter)
    _clone_lock: threading.Lock = field(default_factory=threading.Lock)
    _provider: Provider | None = None
    _triage_provider: Provider | None = None
    _start_ref: str | None = None  # branch (or detached sha) to return to after a local run

    # -- construction -------------------------------------------------------------------------
    @classmethod
    def open(cls, opts: Options, *, need_outline: bool = True, need_git: bool = True) -> Session:
        if opts.since is not None and opts.since.tzinfo is None:
            opts.since = opts.since.replace(tzinfo=UTC)
        config_path = opts.config_path.resolve()
        cfg = load_config(config_path)
        if opts.provider:
            cfg.llm.provider = opts.provider  # type: ignore[assignment]
        if opts.model:
            cfg.llm.model = opts.model
        if opts.base_url:
            cfg.llm.base_url = opts.base_url
        outline = load_outline(cfg, config_path) if need_outline else None
        repo_root = config_path.parent
        now = opts.now or datetime.now(UTC)
        host = urlparse(cfg.github.server_url).netloc or "github.com"
        s = cls(opts, cfg, outline, repo_root, repo_root, now, host)
        s.token = docs_token()
        if opts.dry_run:
            s.work_root = s._scratch_copy()
        elif need_git:
            s.repo = DocsRepo(repo_root, token=s.token, host=host)
            if not s.repo.is_repo():
                raise ConfigError(
                    f"{repo_root} is not a git repository (use --dry-run to run locally)"
                )
            dirty = s.repo.status_paths()
            if dirty:
                raise ConfigError(
                    "the docs repo has uncommitted changes ("
                    + ", ".join(dirty[:5])
                    + "). Fidus works from the pushed default branch: commit and push them "
                    "(or stash them), or use --dry-run."
                )
            s._start_ref = s.repo.start_ref()
            s.slug = docs_repo_slug() or s._slug_from_git()
            if not s.slug:
                raise ConfigError("cannot determine the docs repo; set FIDUS_DOCS_REPO=owner/name")
            if not s.token:
                raise ConfigError(
                    "no GitHub token: set FIDUS_GITHUB_TOKEN (App installation token or PAT)"
                )
        if s.token or any(src.repo for src in cfg.sources):
            s.api = GitHubAPI(GitHubClient(s.token, cfg.github.api_url))
        return s

    def _slug_from_git(self) -> str | None:
        assert self.repo is not None
        from fidus.gitops.runner import git

        url = git(["remote", "get-url", "origin"], self.repo_root, check=False).stdout
        return slug_from_remote(url) if url else None

    def _scratch_copy(self) -> Path:
        out = self.opts.output.resolve() if self.opts.output else None
        tmp = Path(tempfile.mkdtemp(prefix="fidus-work-"))
        dest = tmp / "repo"

        def ignore(dirpath: str, names: list[str]) -> set[str]:
            skip = {n for n in names if n in {".git", ".fidus-cache", "node_modules", ".venv"}}
            if out is not None:
                skip |= {n for n in names if (Path(dirpath) / n).resolve() == out}
            return skip

        shutil.copytree(self.repo_root, dest, ignore=ignore, symlinks=True)
        return dest

    # -- paths and helpers --------------------------------------------------------------------
    @property
    def docs_root(self) -> Path:
        return self.work_root / self.cfg.docs.dir

    @property
    def cache_dir(self) -> Path:
        base = (
            os.environ.get("FIDUS_CACHE_DIR")
            or os.environ.get("RUNNER_TEMP")
            or tempfile.gettempdir()
        )
        digest = hashlib.sha1(str(self.repo_root).encode()).hexdigest()[:10]
        path = Path(base) / "fidus-cache" / digest
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def author(self) -> Author:
        return Author(
            os.environ.get("FIDUS_GIT_USER_NAME") or self.cfg.git.author_name,
            os.environ.get("FIDUS_GIT_USER_EMAIL") or self.cfg.git.author_email,
        )

    @property
    def protected_paths(self) -> list[Path]:
        return [
            self.work_root / self.opts.config_path.name,
            outline_path(self.cfg, self.work_root / self.opts.config_path.name),
            self.work_root / ".fidus" / "state.json",
        ]

    def provider(self) -> Provider:
        if self._provider is None:
            self._provider = make_provider(self.cfg.llm)
        return self._provider

    def triage_provider(self) -> Provider:
        if self._triage_provider is None:
            if self.cfg.llm.triage_model and self.cfg.llm.provider != "fake":
                self._triage_provider = make_provider(self.cfg.llm, model=self.cfg.llm.triage_model)
            else:
                self._triage_provider = self.provider()
        return self._triage_provider

    def require_api(self) -> GitHubAPI:
        if self.api is None:
            raise FidusError("GitHub access is required for this operation")
        return self.api

    # -- sources ------------------------------------------------------------------------------
    def clone_sources(self) -> dict[str, SourceWorkspace]:
        """Clone (once) every source. Thread-safe: episodes may call this concurrently."""
        with self._clone_lock:
            if not self.workspaces:
                self._clone_all()
        return self.workspaces

    def _clone_all(self) -> None:
        from fidus.agent.tools.source_tools import clear_file_cache

        clear_file_cache()  # clones are fresh: drop listings cached by an earlier session
        for src in self.cfg.sources:
            branch = src.branch
            if src.repo and not branch and self.api is not None:
                branch = self.api.default_branch(src.repo)
            self.workspaces[src.name] = clone_source(
                src,
                self.cfg,
                self.cache_dir,
                config_dir=self.repo_root,
                token=source_token(src),
                branch=branch,
            )

    def source_branch(self, alias: str) -> str:
        src = self.cfg.source(alias)
        if src.branch:
            return src.branch
        if src.repo is None:
            return "HEAD"  # local checkout: whatever is checked out
        return self.require_api().default_branch(src.repo)

    # -- episodes -----------------------------------------------------------------------------
    def make_context(
        self, mode: Mode, chapter_id: str | None, triggers: list[Trigger]
    ) -> EpisodeContext:
        chapter = self.outline.get(chapter_id) if (self.outline and chapter_id) else None
        ctx = EpisodeContext(
            mode=mode,
            cfg=self.cfg,
            repo_root=self.work_root,
            workspaces={a: w.root for a, w in self.clone_sources().items()},
            outline=self.outline,
            chapter=chapter,
            triggers=triggers,
            protected_paths=self.protected_paths,
            secret_values=configured_secret_values(
                [self.cfg.llm.resolved_api_key_env, *[s.token_env for s in self.cfg.sources]]
            ),
        )
        if ctx.chapter_path is not None:
            ctx.original_doc = read_text_or_none(ctx.chapter_path)
        return ctx

    def run_chapter_episodes(
        self,
        jobs: list[
            tuple[Mode, str, list[Trigger], str]
        ],  # (mode, chapter id, triggers, extra brief text)
        run_budget: RunBudget,
        *,
        budget_cfg: EpisodeBudget | None = None,
    ) -> list[EpisodeResult]:
        """Run chapter episodes in parallel. Jobs skipped for lack of run budget come back as
        `budget_exhausted` results without a model call."""
        provider = self.provider()
        bcfg = budget_cfg or self.cfg.budgets.episode

        def one(job: tuple[Mode, str, list[Trigger], str]) -> EpisodeResult:
            mode, chapter_id, triggers, extra = job
            if run_budget.exhausted:
                return EpisodeResult(
                    chapter_id, mode, "budget_exhausted", error="run budget exhausted"
                )
            ctx = self.make_context(mode, chapter_id, triggers)
            budget = Budget(
                max_turns=bcfg.max_turns,
                max_input_tokens=bcfg.max_input_tokens,
                max_output_tokens=bcfg.max_output_tokens,
                run=run_budget,
            )
            log.info("episode %s: %s", mode, chapter_id)
            try:
                result = run_episode(
                    ctx,
                    provider,
                    budget,
                    brief=chapter_brief(ctx, changes=extra),
                    max_tokens=self.cfg.llm.max_output_tokens,
                    temperature=self.cfg.llm.temperature,
                    context_window=self.cfg.llm.context_window,
                )
            except Exception as e:  # one broken episode must not sink the run
                log.exception("episode %s crashed", chapter_id)
                result = EpisodeResult(chapter_id, mode, "failed", error=f"{type(e).__name__}: {e}")
            self.meter.add(result.usage)
            log.info(
                "episode %s: %s -> %s (%s)", mode, chapter_id, result.status, result.summary[:80]
            )
            return result

        if not jobs:
            return []
        self.clone_sources()  # before fanning out
        ids = [j[1] for j in jobs]
        blockers = self._in_run_prerequisites(ids)
        workers = min(self.cfg.budgets.max_parallel_episodes, len(jobs))
        results: dict[int, EpisodeResult] = {}
        waiting = list(range(len(jobs)))
        running: dict[Future[EpisodeResult], int] = {}
        finished: set[str] = set()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # A chapter starts only after its prerequisites in this run have finished, so it can
            # read their updated text; independent chapters still run in parallel.
            while waiting or running:
                ready = [i for i in waiting if blockers[ids[i]] <= finished]
                if not ready and not running:  # unreachable for valid outlines; never deadlock
                    ready = waiting[:1]
                for i in ready[: workers - len(running)]:
                    waiting.remove(i)
                    running[pool.submit(one, jobs[i])] = i
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in done:
                    i = running.pop(fut)
                    results[i] = fut.result()
                    finished.add(ids[i])
        return [results[i] for i in range(len(jobs))]

    def _in_run_prerequisites(self, ids: list[str]) -> dict[str, set[str]]:
        """For each chapter in this run, the chapters it (transitively) depends on that are
        also in this run."""
        in_run = set(ids)
        if self.outline is None:
            return {cid: set() for cid in ids}
        known = {c.id: c for c in self.outline.chapters()}

        def ancestors(cid: str, seen: set[str]) -> set[str]:
            out: set[str] = set()
            for pre in known[cid].prerequisites if cid in known else []:
                if pre not in seen:
                    seen.add(pre)
                    out |= {pre} | ancestors(pre, seen)
            return out

        return {cid: (ancestors(cid, set()) & in_run) - {cid} for cid in ids}

    def close(self) -> None:
        if self.api is not None:
            self.api.c.close()
        if self.repo is not None and self._start_ref:
            # Put a local clone back where the user left it. The start was clean (enforced in
            # open), so anything left over in docs/ or .fidus/ came from this run.
            try:
                self.repo.restore(self._start_ref, [self.cfg.docs.dir, ".fidus"])
            except FidusError as e:
                log.warning("could not return to %s: %s", self._start_ref, e)
