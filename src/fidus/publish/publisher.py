"""Publishing: push a branch and open/update its PR, or write artifacts locally (dry run)."""

from __future__ import annotations

import difflib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fidus.log import get_logger
from fidus.pipeline.results import RunReport
from fidus.pipeline.session import Session
from fidus.publish.sync_branch import BranchInfo, BranchState
from fidus.state.models import State
from fidus.state.store import STATE_PATH, dump_state

log = get_logger("publish")


@dataclass
class PublishRequest:
    branch: BranchInfo
    title: str
    body: str
    commit_message: str
    force_push: bool
    base: str


def publish_github(session: Session, req: PublishRequest) -> str | None:
    """Commit docs + state, push the branch, create or update the PR. Returns the PR URL."""
    repo, api, slug = session.repo, session.require_api(), session.slug
    assert repo is not None and slug is not None
    committed = repo.commit_all(
        [session.cfg.docs.dir, STATE_PATH], req.commit_message, session.author
    )
    if not committed and req.branch.state != BranchState.OPEN:
        log.info("nothing to commit")
        return None
    if session.opts.no_push:
        log.info("--no-push: committed locally on %s, not pushing", req.branch.name)
        return None
    repo.push(req.branch.name, lease=req.branch.remote_sha, force=req.force_push)
    pr: dict[str, Any]
    if req.branch.state == BranchState.OPEN and req.branch.pr:
        pr = api.update_pr(slug, req.branch.pr["number"], title=req.title, body=req.body)
        log.info("updated PR #%s", pr["number"])
    else:
        pr = api.create_pr(
            slug,
            head=req.branch.name,
            base=req.base,
            title=req.title,
            body=req.body,
            draft=session.cfg.sync.draft,
        )
        api.add_labels(slug, pr["number"], session.cfg.sync.labels)
        api.request_reviewers(
            slug, pr["number"], session.cfg.sync.reviewers, session.cfg.sync.team_reviewers
        )
        log.info("opened PR #%s", pr["number"])
    return str(pr.get("html_url", ""))


def _unified_diff(original_root: Path, new_root: Path, rel_dirs: list[str]) -> str:
    chunks: list[str] = []
    files: set[str] = set()
    for rel in rel_dirs:
        for root in (original_root, new_root):
            base = root / rel
            if base.is_file():
                files.add(rel)
            elif base.is_dir():
                files.update(
                    (rel + "/" + p.relative_to(base).as_posix())
                    for p in base.rglob("*")
                    if p.is_file()
                )
    for f in sorted(files):
        a, b = original_root / f, new_root / f
        old = a.read_text(encoding="utf-8").splitlines(keepends=True) if a.exists() else []
        new = b.read_text(encoding="utf-8").splitlines(keepends=True) if b.exists() else []
        if old != new:
            chunks.extend(
                difflib.unified_diff(
                    old,
                    new,
                    f"a/{f}" if a.exists() else "/dev/null",
                    f"b/{f}" if b.exists() else "/dev/null",
                )
            )
    return "".join(chunks)


def publish_local(session: Session, state: State, body: str, report: RunReport) -> Path:
    """Write the dry-run artifacts: docs/, state.json, pr-body.md, run.json, diff.patch."""
    out = (session.opts.output or Path("fidus-out")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    docs_out = out / "docs"
    if docs_out.exists():
        shutil.rmtree(docs_out)
    if session.docs_root.exists():
        shutil.copytree(session.docs_root, docs_out)
    (out / "state.json").write_text(dump_state(state), encoding="utf-8")
    (out / "pr-body.md").write_text(body, encoding="utf-8")
    report.published = str(out)
    (out / "run.json").write_text(
        json.dumps(report.to_json(), indent=2, default=str), encoding="utf-8"
    )
    diff = _unified_diff(session.repo_root, session.work_root, [session.cfg.docs.dir, STATE_PATH])
    (out / "diff.patch").write_text(diff, encoding="utf-8")
    log.info("dry run: wrote %s", out)
    return out
