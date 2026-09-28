"""Turn audit structure suggestions into a separate outline PR (never applied automatically)."""

from __future__ import annotations

import json

from pydantic import ValidationError

from fidus.agent.prompts import render
from fidus.agent.tools.control_tools import ProposeOutlineTool
from fidus.config.loader import dump_yaml, outline_path
from fidus.config.outline import Outline
from fidus.llm.types import Message, Role, ToolResult, user
from fidus.log import get_logger
from fidus.pipeline.init import OUTLINE_HEADER
from fidus.pipeline.session import Session
from fidus.publish.sync_branch import BranchInfo, BranchState, inspect_branch

log = get_logger("outline")


def revise_outline(session: Session, suggestions: list[str]) -> Outline | None:
    assert session.outline is not None
    spec = ProposeOutlineTool().spec()
    messages: list[Message] = [
        user(
            render(
                "outline_revise",
                suggestions="\n".join(f"- {s}" for s in suggestions),
                outline_json=json.dumps(session.outline.model_dump(mode="json"), indent=1),
            )
        )
    ]
    provider = session.provider()
    for _ in range(3):
        c = provider.complete(
            system="You are an editor maintaining the outline of a technical textbook.",
            messages=messages,
            tools=[spec],
            max_tokens=session.cfg.llm.max_output_tokens,
            temperature=0.2,
        )
        session.meter.add(c.usage)
        messages.append(c.message)
        calls = [tc for tc in c.message.tool_calls if tc.name == spec.name]
        if not calls:
            messages.append(user("Call propose_outline with the complete revised outline."))
            continue
        try:
            outline = Outline.model_validate(calls[0].arguments)
            outline.check_against_aliases(session.cfg.aliases, session.cfg.docs.index_file)
            return outline
        except (ValidationError, ValueError) as e:
            messages.append(
                Message(Role.USER, [ToolResult(calls[0].id, spec.name, f"Rejected: {e}", True)])
            )
    return None


def propose_outline_pr(session: Session, suggestions: list[str], default: str) -> str | None:
    if not suggestions or session.repo is None or session.slug is None:
        return None
    outline = revise_outline(session, suggestions)
    if outline is None or outline == session.outline:
        log.info("no outline revision produced")
        return None
    api = session.require_api()
    info = inspect_branch(api, session.repo, session.slug, session.cfg.sync.outline_branch, default)
    # A proposal is regenerated from the default branch every time.
    session.repo.checkout_new(info.name, f"{session.repo.remote}/{default}")
    path = outline_path(session.cfg, session.opts.config_path.resolve())
    path.write_text(
        dump_yaml(outline.model_dump(mode="json"), header=OUTLINE_HEADER), encoding="utf-8"
    )
    rel = path.relative_to(session.repo_root).as_posix()
    body = (
        "## Fidus: proposed outline changes\n\n"
        "The weekly audit suggested restructuring the book:\n\n"
        + "\n".join(f"- {s}" for s in suggestions)
        + "\n\nThis PR only edits the outline. After merging, the next nightly run writes any new "
        "chapters. Existing chapter files for removed chapters are reported, never deleted.\n"
    )
    if not session.repo.commit_all([rel], "docs(fidus): propose outline changes", session.author):
        return None
    if session.opts.no_push:
        return None
    session.repo.push(info.name, lease=info.remote_sha, force=True)
    branch = BranchInfo(info.name, info.state, info.remote_sha, info.pr)
    if branch.state == BranchState.OPEN and branch.pr:
        pr = api.update_pr(
            session.slug, branch.pr["number"], title="docs: Fidus outline proposal", body=body
        )
    else:
        pr = api.create_pr(
            session.slug,
            head=info.name,
            base=default,
            title="docs: Fidus outline proposal",
            body=body,
        )
        api.add_labels(session.slug, pr["number"], session.cfg.sync.labels)
    return str(pr.get("html_url", ""))
