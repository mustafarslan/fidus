"""Nightly orchestration: collect merged PRs, update affected chapters, audit, publish one PR."""

from __future__ import annotations

from collections.abc import Callable

from fidus.agent.budget import RunBudget
from fidus.agent.context import Mode
from fidus.agent.episode import EpisodeResult
from fidus.config.models import FidusConfig
from fidus.config.outline import Outline
from fidus.errors import NotBootstrappedError
from fidus.llm.cost import estimate_cost
from fidus.log import get_logger, write_step_summary
from fidus.mapping.globs import ChapterMatcher
from fidus.mapping.planner import WorkPlan, build_plan
from fidus.mapping.triage import triage
from fidus.pipeline.index import index_needs_update, write_index
from fidus.pipeline.results import RunReport
from fidus.pipeline.schedule import RunMode, audit_selection, resolve_modes
from fidus.pipeline.session import Options, Session
from fidus.publish.pr_body import render_pr_body
from fidus.publish.publisher import PublishRequest, publish_github, publish_local
from fidus.publish.sync_branch import BranchInfo, BranchState, inspect_branch, prepare_branch
from fidus.sources.filters import apply_filters
from fidus.sources.github_source import advance_cursor, github_triggers
from fidus.sources.local_source import is_ancestor, local_triggers
from fidus.sources.trigger import ChangedFile, Trigger
from fidus.state.models import (
    MAX_STORED_FILES,
    ChangedFileRef,
    IncompleteEpisode,
    PendingAudit,
    PendingTrigger,
    RunUsage,
    State,
)
from fidus.state.store import STATE_PATH, parse_state, write_state

log = get_logger("run")


# --------------------------------------------------------------------------------------------
# state loading


def _load_state(session: Session, branch: BranchInfo | None, default: str | None) -> State | None:
    if session.repo is None or branch is None or default is None:
        # Dry run: read the working tree's state as if it were the default branch.
        path = session.work_root / STATE_PATH
        text = path.read_text(encoding="utf-8") if path.exists() else None
        return parse_state(text, from_default_branch=True)
    remote = session.repo.remote
    if branch.state == BranchState.REJECTED:
        return _rejected_state(session, branch, default)
    if branch.state == BranchState.OPEN:
        return parse_state(
            session.repo.show(f"{remote}/{branch.name}", STATE_PATH), from_default_branch=False
        )
    return parse_state(
        session.repo.show(f"{remote}/{default}", STATE_PATH), from_default_branch=True
    )


def _rejected_state(session: Session, branch: BranchInfo, default: str) -> State | None:
    """The last PR was closed without merging: keep the default branch's state, but take the
    cursors from the rejected branch so its changes are skipped, and say so in the next PR."""
    assert session.repo is not None
    remote = session.repo.remote
    state = parse_state(
        session.repo.show(f"{remote}/{default}", STATE_PATH), from_default_branch=True
    )
    rejected = parse_state(
        session.repo.show(f"{remote}/{branch.name}", STATE_PATH), from_default_branch=False
    )
    if rejected is None:
        return state
    state = state or State()
    state.base.repos = rejected.base.repos
    state.pending.rejected = [
        f"{p.repo}#{p.number}" if p.number is not None else f"{p.repo}@{(p.sha or '')[:8]}"
        for p in rejected.pending.triggers
    ]
    return state


def _to_trigger(p: PendingTrigger, alias_of: Callable[[str], str]) -> Trigger:
    """Reconstruct a Trigger from state for rebuilds and retries: file list, but no diffs."""
    files = [ChangedFile(f.path, f.status, f.additions, f.deletions) for f in p.files]
    return Trigger(
        repo=p.repo,
        alias=alias_of(p.repo),
        title=p.title,
        number=p.number,
        sha=p.sha,
        url=p.url,
        merged_at=p.merged_at,
        files=files,
        omitted_files=max(0, p.files_total - len(files)),
        notes=["Diffs are not available for retried changes; read the current files instead."],
    )


def _to_pending(t: Trigger, unmapped_files: list[str] | None = None) -> PendingTrigger:
    kept = t.files[:MAX_STORED_FILES]
    return PendingTrigger(
        repo=t.repo,
        number=t.number,
        sha=t.sha,
        title=t.title,
        url=t.url,
        merged_at=t.merged_at,
        unmapped_files=unmapped_files or [],
        files=[
            ChangedFileRef(
                path=f.path, status=f.status, additions=f.additions, deletions=f.deletions
            )
            for f in kept
        ],
        files_total=len(t.files) + t.omitted_files,
    )


def _pending_as_triggers(state: State, alias_of: Callable[[str], str]) -> dict[str, Trigger]:
    return {p.key: _to_trigger(p, alias_of) for p in state.pending.triggers}


# --------------------------------------------------------------------------------------------
# trigger collection


def _collect_triggers(session: Session, state: State) -> list[Trigger]:
    cfg = session.cfg
    triggers: list[Trigger] = []
    for src in cfg.sources:
        cursor = state.cursor(src.key)
        if src.repo:
            api = session.require_api()
            found = github_triggers(
                api,
                src.repo,
                src.name,
                session.source_branch(src.name),
                cursor,
                since_override=session.opts.since,
                limit=cfg.sync.max_prs_per_run,
            )
        else:
            ws = session.clone_sources()[src.name]
            found = local_triggers(
                ws.root,
                src.key,
                src.name,
                since_sha=None if session.opts.since else cursor.cursor_sha,
                # Always pass the timestamp: it is the fallback when the cursor commit vanished.
                since_time=session.opts.since or cursor.cursor_merged_at,
                limit=cfg.sync.max_prs_per_run,
            )
        for t in found:
            apply_filters(t, src, cfg.limits)
        log.info("%s: %d merged change(s) since last run", src.repo or src.name, len(found))
        triggers.extend(found)
    return triggers


def _advance_cursors(session: Session, state: State, triggers: list[Trigger]) -> None:
    for src in session.cfg.sources:
        mine = [t for t in triggers if t.repo == src.key]
        cursor = state.cursor(src.key)
        if src.repo:
            advance_cursor(cursor, mine)
        ws = session.workspaces.get(src.name)
        if not src.repo and mine and ws is not None:
            # Local cursors are commits; commit dates come from arbitrary clocks, so order by
            # ancestry. Never move back to an ancestor of the current cursor (e.g. --since).
            new_sha = mine[-1].sha
            if not (
                cursor.cursor_sha and new_sha and is_ancestor(ws.root, new_sha, cursor.cursor_sha)
            ):
                cursor.cursor_sha = new_sha
                cursor.cursor_merged_at = mine[-1].merged_at
        if ws and ws.head_sha:
            cursor.head_sha_seen = ws.head_sha


# --------------------------------------------------------------------------------------------
# planning


def _chapter_mapper(
    session: Session, outline: Outline, triggers: list[Trigger]
) -> Callable[[str, str], list[str]]:
    matcher = ChapterMatcher(outline)
    unmapped = sorted(
        {
            f"{t.alias}:{f.path}"
            for t in triggers
            for f in t.files
            if not matcher.chapters_for(t.alias, f.path)
        }
    )
    extra: dict[str, str] = {}
    if unmapped:
        log.info("triaging %d file(s) not covered by chapter globs", len(unmapped))
        extra, usage = triage(unmapped, outline, session.cfg, session.triage_provider())
        session.meter.add(usage)

    def chapters_for(alias: str, path: str) -> list[str]:
        hits = matcher.chapters_for(alias, path)
        if hits:
            return hits
        c = extra.get(f"{alias}:{path}")
        return [c] if c else []

    return chapters_for


def _retry_items(state: State, alias_of: Callable[[str], str]) -> dict[str, list[Trigger]]:
    """Chapters left incomplete by earlier runs (budget, failures, episode cap) to redo now.
    Kept in `base`, so they survive the PR being merged before they succeed."""
    return {
        inc.chapter: [_to_trigger(p, alias_of) for p in inc.triggers]
        for inc in state.base.retry
        if not inc.quarantined
    }


# --------------------------------------------------------------------------------------------
# state update


def _record(
    session: Session,
    state: State,
    plan: WorkPlan,
    triggers: list[Trigger],
    results: list[EpisodeResult],
    consistency: list[EpisodeResult],
    *,
    audit_chapters: list[str],
    rotation_index: int,
    did_audit: bool,
) -> None:
    now = session.now
    pending = state.pending
    pending.opened_at = pending.opened_at or now
    pending.runs += 1
    pending.paused_reason = None
    by_chapter = {r.chapter_id: r for r in results if r.chapter_id}
    items = {i.chapter_id: i for i in plan.items}

    # Incomplete work carried to the next run (in `base`: it must survive a merge).
    redone = set(by_chapter)
    previous = {e.chapter: e for e in state.base.retry}
    retry = [e for e in state.base.retry if e.chapter not in redone]
    max_attempts = session.cfg.limits.max_retry_attempts

    def carried(chapter: str, triggers: list[Trigger]) -> list[PendingTrigger]:
        """Keep the triggers of earlier attempts: they still owe an update."""
        out = list(previous[chapter].triggers) if chapter in previous else []
        keys = {p.key for p in out}
        return out + [_to_pending(t) for t in triggers if t.key not in keys]

    for r in results:
        if r.status not in ("failed", "protocol_error", "budget_exhausted") or not r.chapter_id:
            continue
        item = items.get(r.chapter_id)
        reason = f"{r.status}: {r.error or ''}".strip(": ")
        if r.status == "budget_exhausted" and r.changed:
            reason = "budget exhausted; partial update written, will be completed next run"
        prev = previous.get(r.chapter_id)
        attempts = (prev.attempts if prev else 0) + 1
        retry.append(
            IncompleteEpisode(
                chapter=r.chapter_id,
                reason=reason,
                triggers=carried(r.chapter_id, item.triggers if item else []),
                attempts=attempts,
                quarantined=attempts >= max_attempts,
            )
        )
    for item in plan.deferred:
        prev = previous.get(item.chapter_id)
        retry.append(
            IncompleteEpisode(
                chapter=item.chapter_id,
                reason="deferred: episode cap reached",
                triggers=carried(item.chapter_id, item.triggers),
                attempts=prev.attempts if prev else 0,
            )
        )
    state.base.retry = retry

    # Triggers: one row per source change.
    existing = {p.key: p for p in pending.triggers}
    rebuilt_keys = {t.key for i in plan.items if i.rebuild for t in i.triggers}
    for key in rebuilt_keys:  # rebuilt chapters get fresh reasons below
        if key in existing:
            existing[key].chapters = {}
    for t in triggers:
        existing[t.key] = _to_pending(t, unmapped_files=plan.unmapped.get(t.key, []))
    for chapter_id, r in by_chapter.items():
        item = items.get(chapter_id)
        if item is None:
            continue
        for t in item.triggers:
            p = existing.get(t.key)
            if p is None:
                continue
            if r.changed:
                p.chapters[chapter_id] = r.trigger_reasons.get(t.key) or r.summary or "updated"
    failed_chapters = {i.chapter for i in state.base.retry}
    for p in existing.values():
        mapped = plan.trigger_chapters.get(p.key, list(p.chapters))
        if p.chapters:
            p.status = "applied"
        elif any(c in failed_chapters for c in mapped):
            p.status = "failed"
        elif p.unmapped_files and not mapped:
            p.status = "unmapped"
        else:
            p.status = "no_impact"
    pending.triggers = sorted(
        existing.values(), key=lambda p: (p.merged_at is None, p.merged_at, p.key)
    )

    # Trigger-less rebuilds re-verify audit/consistency changes; keep the PR body truthful.
    for cid, item in items.items():
        if not (item.rebuild and item.mode == "audit"):
            continue
        rr = by_chapter.get(cid)
        for changes in (pending.audit.changed if pending.audit else {}, pending.consistency):
            if cid not in changes:
                continue
            if rr is not None and rr.changed:
                changes[cid] = rr.summary or changes[cid]
            else:
                del changes[cid]

    # New chapters, consistency pass, audit.
    for cid in plan.new_chapters:
        if by_chapter.get(cid) and by_chapter[cid].changed and cid not in pending.new_chapters:
            pending.new_chapters.append(cid)
    for r in consistency:
        if r.changed and r.chapter_id:
            pending.consistency[r.chapter_id] = (
                r.summary or "Updated references to changed concepts."
            )
    if did_audit:
        audit = pending.audit or PendingAudit()
        audit.ran_at = now
        audit.chapters_checked = sorted(set(audit.chapters_checked) | set(audit_chapters))
        for cid in audit_chapters:
            ar = by_chapter.get(cid)
            if ar is None:
                continue
            if ar.changed and items.get(cid) and items[cid].mode == "audit":
                audit.changed[cid] = ar.summary or "Fixed drift found by the audit."
            audit.findings = [f for f in audit.findings if f.chapter != cid] + ar.findings
            for s in ar.structure_suggestions:
                if s not in audit.structure_suggestions:
                    audit.structure_suggestions.append(s)
        pending.audit = audit
        state.base.audit.last_run_at = now
        state.base.audit.rotation_index = rotation_index
    else:
        for r in results:  # sync episodes may still report findings/suggestions
            if r.structure_suggestions:
                audit = pending.audit or PendingAudit()
                for s in r.structure_suggestions:
                    if s not in audit.structure_suggestions:
                        audit.structure_suggestions.append(s)
                pending.audit = audit

    pending.orphans = plan.orphans
    usage = session.meter.total
    if usage.input_tokens or usage.output_tokens:
        pending.usage.append(
            RunUsage(
                run_at=now,
                provider=session.cfg.llm.provider,
                model=session.cfg.llm.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cost_usd=estimate_cost(usage, session.cfg.llm.pricing),
            )
        )
        pending.usage = pending.usage[-30:]
    _advance_cursors(session, state, triggers)


# --------------------------------------------------------------------------------------------
# main entry


def must_persist(cfg: FidusConfig, triggers: list[Trigger]) -> bool:
    """Publish a state-only PR even though no docs changed? Always with `state_pr`; and
    whenever a source filled its per-run window, or the cursor could never get past a
    long run of no-impact PRs (they would be re-collected every night)."""
    if not triggers:
        return False
    if cfg.sync.noop_policy == "state_pr":
        return True
    per_source: dict[str, int] = {}
    for t in triggers:
        per_source[t.repo] = per_source.get(t.repo, 0) + 1
    return max(per_source.values()) >= cfg.sync.max_prs_per_run


def _consistency_jobs(
    outline: Outline, results: list[EpisodeResult], cap: int
) -> list[tuple[Mode, str, list[Trigger], str]]:
    changed_now = {r.chapter_id for r in results if r.changed}
    notes: dict[str, list[str]] = {}
    for r in results:
        if not (r.changed and r.concepts_changed and r.chapter_id):
            continue
        src = outline.get(r.chapter_id)
        for dep in outline.dependents(r.chapter_id):
            if dep.id in changed_now:
                continue
            notes.setdefault(dep.id, []).append(
                f"- In [{src.title}]({src.path}): " + "; ".join(r.concepts_changed)
            )
    jobs: list[tuple[Mode, str, list[Trigger], str]] = []
    for ch in outline.chapters():
        if ch.id in notes and len(jobs) < cap:
            jobs.append(("consistency", ch.id, [], "\n".join(notes[ch.id])))
    return jobs


def run(opts: Options, mode: RunMode = "auto") -> RunReport:
    session = Session.open(opts)
    try:
        return _run(session, mode)
    finally:
        session.close()


def _run(session: Session, mode: RunMode) -> RunReport:
    cfg = session.cfg
    outline = session.outline
    assert outline is not None
    report = RunReport(started_at=session.now, mode=mode)

    # 1. Branch + state.
    branch: BranchInfo | None = None
    default: str | None = None
    outcome = None
    if session.repo is not None:
        api = session.require_api()
        assert session.slug is not None
        default = cfg.docs.default_branch or api.default_branch(session.slug)
        branch = inspect_branch(
            api, session.repo, session.slug, cfg.sync.branch, default, keep_rejected=True
        )
    state = _load_state(session, branch, default)
    if state is None:
        if session.opts.since is None:
            raise NotBootstrappedError(
                "no .fidus/state.json found: run `fidus bootstrap` first (or pass --since for a one-off run)"
            )
        state = State()

    if session.repo is not None and branch is not None and default is not None:
        outcome = prepare_branch(session.repo, branch, default, session.author)
        if outcome.paused:
            report.paused = outcome.paused
            state.pending.paused_reason = outcome.paused
            if branch.pr and session.slug:
                body = render_pr_body(state.pending, outline, cfg, retry=state.base.retry)
                session.require_api().update_pr(
                    session.slug, branch.pr["number"], title=cfg.sync.pr_title, body=body
                )
            write_step_summary(report.summary_markdown())
            log.warning("paused: %s", outcome.paused)
            return report
        report.rebuilt = outcome.rebuild

    # 2. What to do.
    do_sync, do_audit = resolve_modes(mode, state, cfg, session.now)
    report.did_sync, report.did_audit = do_sync, do_audit
    triggers = _collect_triggers(session, state) if do_sync else []
    report.triggers_collected = len(triggers)
    audit_chapters, rotation = audit_selection(outline, state, cfg) if do_audit else ([], 0)

    alias_by_key = {s.key: s.name for s in cfg.sources}

    def alias_of(key: str) -> str:
        return alias_by_key.get(key, key.split("/")[-1])

    pending_triggers = _pending_as_triggers(state, alias_of)
    rebuild: dict[str, list[Trigger]] = _retry_items(state, alias_of)
    if outcome and outcome.rebuild:
        for cid in state.pending.touched_chapters():
            ts = [pending_triggers[p.key] for p in state.pending.triggers if cid in p.chapters]
            rebuild.setdefault(cid, []).extend(ts)

    chapters_for = _chapter_mapper(session, outline, triggers) if triggers else (lambda a, p: [])
    plan = build_plan(
        outline=outline,
        triggers=triggers,
        chapters_for=chapters_for,
        audit_chapters=audit_chapters,
        rebuild=rebuild,
        docs_root=session.docs_root,
        index_file=cfg.docs.index_file,
        max_episodes=cfg.budgets.run.max_episodes,
        only=session.opts.chapters,
    )
    report.deferred_chapters = [i.chapter_id for i in plan.deferred]
    open_pr = branch is not None and branch.state == BranchState.OPEN

    index_stale = index_needs_update(outline, session.docs_root, cfg.docs.index_file)
    if plan.empty and not triggers and not do_audit and not index_stale:
        # Nothing new. Even with an open PR we don't push: no churn on quiet nights.
        report.noop = True
        log.info("nothing to do")
        write_step_summary(report.summary_markdown())
        return report

    # 3. Episodes.
    run_budget = RunBudget(cfg.budgets.run.max_total_tokens)
    jobs: list[tuple[Mode, str, list[Trigger], str]] = [
        (i.mode, i.chapter_id, i.triggers, "") for i in plan.items
    ]
    results = session.run_chapter_episodes(jobs, run_budget)
    cons_jobs = _consistency_jobs(outline, results, cfg.limits.max_consistency_episodes)
    consistency = session.run_chapter_episodes(
        cons_jobs, run_budget, budget_cfg=cfg.budgets.consistency
    )
    report.episodes = results + consistency

    # 4. Deterministic post-processing and state.
    index_changed = write_index(outline, session.docs_root, cfg.docs.index_file)
    _record(
        session,
        state,
        plan,
        triggers,
        results,
        consistency,
        audit_chapters=audit_chapters,
        rotation_index=rotation,
        did_audit=do_audit,
    )
    write_state(session.work_root, state)

    docs_changed = any(r.changed for r in report.episodes) or index_changed
    body = render_pr_body(state.pending, outline, cfg, retry=state.base.retry)

    # 5. Publish.
    if session.opts.dry_run:
        publish_local(session, state, body, report)
    elif docs_changed or open_pr or must_persist(cfg, triggers):
        assert branch is not None and default is not None
        msg = f"docs(fidus): sync {len(triggers)} change(s), {len(report.changed_chapters)} chapter(s) updated"
        report.published = publish_github(
            session,
            PublishRequest(
                branch=branch,
                title=cfg.sync.pr_title,
                body=body,
                commit_message=msg,
                force_push=bool(outcome and outcome.force_push),
                base=default,
            ),
        )
        if do_audit and cfg.schedule.audit.structure_proposals == "pr" and state.pending.audit:
            from fidus.pipeline.outline_proposal import propose_outline_pr

            propose_outline_pr(session, state.pending.audit.structure_suggestions, default)
    else:
        report.noop = True
        report.notes.append("changes reviewed but no documentation impact; nothing published")
        log.info("no documentation changes; not opening a PR (noop_policy=defer)")

    for r in report.episodes:
        for f in r.findings:
            if f.severity == "error":
                report.notes.append(f"{f.chapter}: {f.text}")
    write_step_summary(report.summary_markdown())
    return report
