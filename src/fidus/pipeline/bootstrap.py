"""Write every chapter of the outline from scratch and open a bootstrap PR."""

from __future__ import annotations

from fidus.agent.budget import RunBudget
from fidus.agent.context import Mode
from fidus.config.outline import Outline
from fidus.github.pulls import parse_time
from fidus.log import get_logger, write_step_summary
from fidus.pipeline.index import write_index
from fidus.pipeline.results import RunReport
from fidus.pipeline.session import Options, Session
from fidus.publish.pr_body import render_pr_body
from fidus.publish.publisher import PublishRequest, publish_github, publish_local
from fidus.publish.sync_branch import inspect_branch, prepare_branch
from fidus.sources.local_source import commit_time
from fidus.sources.trigger import Trigger
from fidus.state.models import RunUsage, State
from fidus.state.store import write_state

log = get_logger("bootstrap")


def _initial_cursors(session: Session, state: State) -> None:
    """Start nightly syncs from 'now': the newest merged PR per GitHub source, HEAD for local ones."""
    for src in session.cfg.sources:
        cursor = state.cursor(src.key)
        ws = session.workspaces.get(src.name)
        if ws is not None:
            cursor.head_sha_seen = ws.head_sha
        if src.repo:
            newest = None
            numbers: list[int] = []
            api = session.require_api()
            # Sorted by updated_at desc and merged_at <= updated_at: once a PR was last updated
            # before the newest merge seen, no later PR can have merged more recently.
            for i, pr in enumerate(
                api.merged_since(src.repo, session.source_branch(src.name), None)
            ):
                updated = parse_time(pr.get("updated_at"))
                if newest is not None and updated is not None and updated < newest:
                    break
                merged = parse_time(pr["merged_at"])
                if merged and (newest is None or merged > newest):
                    newest, numbers = merged, [pr["number"]]
                elif merged and merged == newest:
                    numbers.append(pr["number"])
                if i >= 2000:  # pathological repos: stop scanning
                    break
            cursor.cursor_merged_at = newest or session.now
            cursor.cursor_pr_numbers = numbers
        else:
            cursor.cursor_sha = ws.head_sha if ws else None
            # The commit's own time (not "now"): it is the fallback if history gets rewritten.
            head_time = commit_time(ws.root, ws.head_sha) if ws and ws.head_sha else None
            cursor.cursor_merged_at = head_time or session.now


def bootstrap(opts: Options, *, resume: bool = False) -> RunReport:
    session = Session.open(opts)
    try:
        return _bootstrap(session, resume=resume)
    finally:
        session.close()


def _bootstrap(session: Session, *, resume: bool) -> RunReport:
    cfg, outline = session.cfg, session.outline
    assert isinstance(outline, Outline)
    report = RunReport(started_at=session.now, kind="bootstrap", mode="bootstrap")

    branch = None
    default = None
    if session.repo is not None:
        api = session.require_api()
        assert session.slug is not None
        default = cfg.docs.default_branch or api.default_branch(session.slug)
        branch = inspect_branch(api, session.repo, session.slug, cfg.sync.bootstrap_branch, default)
        prepare_branch(session.repo, branch, default, session.author)

    session.clone_sources()
    run_budget = RunBudget(cfg.budgets.run.max_total_tokens)
    only = session.opts.chapters
    results = []
    # Parts in order so later chapters can read (and link to) earlier ones; chapters within a
    # part run in parallel.
    for part in outline.parts:
        jobs: list[tuple[Mode, str, list[Trigger], str]] = []
        for ch in part.chapters:
            if only is not None and ch.id not in only:
                continue
            if resume and (session.docs_root / ch.path).exists():
                continue
            jobs.append(("bootstrap", ch.id, [], ""))
        log.info("%s: writing %d chapter(s)", part.title, len(jobs))
        results.extend(session.run_chapter_episodes(jobs, run_budget))
    report.episodes = results
    write_index(outline, session.docs_root, cfg.docs.index_file)

    state = State()
    state.base.bootstrapped_at = session.now
    state.base.audit.last_run_at = session.now
    _initial_cursors(session, state)
    pending = state.pending
    pending.opened_at = session.now
    pending.runs = 1
    pending.new_chapters = [r.chapter_id for r in results if r.changed and r.chapter_id]
    # Chapters that failed are simply missing: the next nightly run writes them as new chapters.
    u = session.meter.total
    pending.usage = [
        RunUsage(
            run_at=session.now,
            provider=cfg.llm.provider,
            model=cfg.llm.model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_tokens,
        )
    ]
    write_state(session.work_root, state)
    body = render_pr_body(pending, outline, cfg, bootstrap=True)

    if session.opts.dry_run:
        publish_local(session, state, body, report)
    else:
        assert branch is not None and default is not None
        report.published = publish_github(
            session,
            PublishRequest(
                branch=branch,
                title=f"docs: Fidus bootstrap — {outline.title}",
                body=body,
                commit_message=f"docs(fidus): bootstrap {len(pending.new_chapters)} chapter(s)",
                force_push=True,
                base=default,
            ),
        )
    write_step_summary(report.summary_markdown())
    return report
