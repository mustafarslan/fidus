"""Regression tests for bugs found in review (advisor, agy, self-review)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from fidus.agent.context import EpisodeContext
from fidus.agent.validate_doc import FRONTMATTER_RE, validate_chapter
from fidus.config.models import FidusConfig
from fidus.config.outline import Outline
from fidus.errors import GitError
from fidus.gitops.docs_repo import DocsRepo
from fidus.gitops.runner import auth_args
from fidus.llm import gemini_adapter as G
from fidus.llm import openai_adapter as O
from fidus.llm.fake import FakeProvider, fake_text
from fidus.llm.json_protocol import parse_envelope
from fidus.llm.types import Completion, Message, Role, ToolCall, ToolResult, Usage, user
from fidus.mapping.triage import _collapse, _parse, triage
from fidus.pipeline.index import index_needs_update, render_index
from fidus.pipeline.run import must_persist
from fidus.sources.github_source import advance_cursor
from fidus.sources.local_source import local_triggers
from fidus.sources.trigger import Trigger
from fidus.state.models import RepoCursor
from tests.conftest import commit, git, make_repo


def test_auth_args_reset_inherited_headers() -> None:
    args = auth_args("tok-1234567890")
    assert args[:2] == ["-c", "http.https://github.com/.extraheader="]
    assert args[3].startswith("http.https://github.com/.extraheader=AUTHORIZATION: basic ")


def test_truncated_tool_calls_are_not_executed(
    tmp_path: Path, cfg: FidusConfig, outline: Outline
) -> None:
    from fidus.agent.budget import Budget
    from fidus.agent.episode import TRUNCATED, run_episode
    from fidus.llm.fake import fake_tool_call

    (tmp_path / "docs").mkdir()
    ctx = EpisodeContext(
        mode="sync",
        cfg=cfg,
        repo_root=tmp_path,
        workspaces={},
        outline=outline,
        chapter=outline.get("auth"),
    )
    cut = Completion(
        Message(Role.ASSISTANT, [ToolCall("w", "write_doc", {"content": "# half a chap"})]),
        Usage(10, 10),
        "max_tokens",
        "fake",
    )
    provider = FakeProvider([cut, fake_tool_call("done", summary="gave up", changed=False)])
    res = run_episode(
        ctx,
        provider,
        Budget(max_turns=5, max_input_tokens=10**6, max_output_tokens=10**6),
        brief="b",
        max_tokens=100,
        temperature=None,
        context_window=100_000,
    )
    assert not (tmp_path / "docs/part-2/01-auth.md").exists()
    fed_back = provider.calls[1]["messages"][-1].parts[0]
    assert isinstance(fed_back, ToolResult) and fed_back.is_error and fed_back.content == TRUNCATED
    assert res.status == "no_change"


def test_must_persist_when_window_is_full(cfg: FidusConfig) -> None:
    cfg.sync.max_prs_per_run = 2
    one = [Trigger(repo="a/b", alias="b", title="x", number=1)]
    two = [*one, Trigger(repo="a/b", alias="b", title="y", number=2)]
    assert not must_persist(cfg, [])
    assert not must_persist(cfg, one)
    assert must_persist(cfg, two)
    cfg.sync.noop_policy = "state_pr"
    assert must_persist(cfg, one)


def test_index_staleness(tmp_path: Path, outline: Outline) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    assert index_needs_update(outline, docs, "README.md")
    (docs / "README.md").write_text(render_index(outline))
    assert not index_needs_update(outline, docs, "README.md")
    assert not index_needs_update(outline, docs, None)


def test_cursor_never_moves_backwards() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    c = RepoCursor(cursor_merged_at=now, cursor_pr_numbers=[9])
    old = Trigger(repo="a/b", alias="b", title="old", number=3, merged_at=now - timedelta(days=5))
    advance_cursor(c, [old])
    assert c.cursor_merged_at == now and c.cursor_pr_numbers == [9]


def test_local_source_survives_rewritten_history_and_inclusive_since(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "svc", {"a.py": "1\n"})
    sha = commit(repo, {"a.py": "2\n"}, "second")
    # Cursor commit no longer exists (history rewritten): fall back to the timestamp instead of 0 triggers.
    ts = local_triggers(
        repo,
        "local:svc",
        "svc",
        since_sha="deadbeef" * 5,
        since_time=datetime(2000, 1, 1, tzinfo=UTC),
        limit=10,
    )
    assert [t.title for t in ts] == ["initial", "second"]
    # The commit exactly at since_time was already processed.
    at = datetime.fromisoformat(git(repo, "show", "-s", "--format=%cI", sha).strip()).astimezone(
        UTC
    )
    assert local_triggers(repo, "local:svc", "svc", since_sha=None, since_time=at, limit=10) == []


def test_force_push_lease_requires_absent_branch_when_unknown(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    seed = make_repo(tmp_path / "seed", {"x": "1"})
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(remote))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(remote), str(work))
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(remote), str(other))
    commit(other, {"y": "someone else"}, "created meanwhile")
    git(other, "push", "-q", "origin", "HEAD:refs/heads/fidus/sync")
    repo = DocsRepo(work)
    commit(work, {"z": "fidus"}, "fidus")
    with pytest.raises(GitError, match=r"stale info|rejected"):
        repo.push("fidus/sync", lease=None, force=True)  # must not clobber the new branch


def test_fetch_forgets_stale_tracking_ref(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    seed = make_repo(tmp_path / "seed", {"x": "1"})
    git(seed, "branch", "fidus/sync")
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(remote))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(remote), str(work))
    repo = DocsRepo(work)
    assert repo.rev("origin/fidus/sync")
    git(remote, "update-ref", "-d", "refs/heads/fidus/sync")
    repo.fetch("+refs/heads/fidus/sync:refs/remotes/origin/fidus/sync")
    assert repo.rev("origin/fidus/sync") is None


def test_commit_all_tolerates_missing_paths(tmp_path: Path) -> None:
    from fidus.gitops.docs_repo import Author

    work = make_repo(tmp_path / "w", {".fidus/state.json": "{}"})
    (work / ".fidus/state.json").write_text('{"v": 2}')
    repo = DocsRepo(work)
    assert repo.commit_all(["docs", ".fidus/state.json"], "m", Author("F", "f@x"))


def test_json_envelope_found_after_other_code_fences() -> None:
    text = 'Here is code:\n```python\nx = {"a": 1}\n```\nand the call:\n```json\n{"tool": "done", "arguments": {}}\n```'
    assert parse_envelope(text, {"done"}) == ("done", {})


def test_triage_parse_ignores_bracketed_prose() -> None:
    text = (
        'Note [1]: see below.\n[{"file": "app:a.py", "chapter": "auth", "confidence": 0.9}] [end]'
    )
    assert _parse(text)[0].chapter == "auth"


def test_triage_root_files_survive_collapse(cfg: FidusConfig, outline: Outline) -> None:
    files = [f"app:lib/f{i}.py" for i in range(250)] + ["app:setup.py"]
    collapsed = _collapse(files)
    assert "app:./" in collapsed
    reply = '[{"file": "app:./", "chapter": "overview", "confidence": 0.9}]'
    mapping, _ = triage(files, outline, cfg, FakeProvider([fake_text(reply)]))
    assert mapping == {"app:setup.py": "overview"}


def test_frontmatter_crlf_and_citation_containment(
    tmp_path: Path, cfg: FidusConfig, outline: Outline
) -> None:
    assert FRONTMATTER_RE.match("---\r\ntitle: x\r\n---\r\n# x")
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "docs").mkdir()
    ctx = EpisodeContext(
        mode="sync",
        cfg=cfg,
        repo_root=tmp_path,
        workspaces={"app": src},
        outline=outline,
        chapter=outline.get("auth"),
    )
    doc = "---\ntitle: A\nfidus:\n  chapter: auth\n---\n# A\nSee `app:/etc/hosts`.\n"
    problems = validate_chapter(doc, ctx)
    assert any("app:/etc/hosts" in p for p in problems)  # absolute paths never "exist"


def test_gemini_synthetic_ids_are_not_sent_back() -> None:
    from google.genai import types

    raw = types.Content(
        role="model",
        parts=[types.Part(function_call=types.FunctionCall(name="list_docs", args={}))],
    )
    resp = NS(
        candidates=[NS(content=raw, finish_reason="STOP")], usage_metadata=None, model_version="g"
    )
    c = G.from_response(resp, turn=3)
    call_id = c.message.tool_calls[0].id
    msgs = [
        user("brief"),
        c.message,
        Message(Role.USER, [ToolResult(call_id, "list_docs", "a.md")]),
        user("extra"),
    ]
    contents = G.to_contents(msgs, types)
    assert [x.role for x in contents] == ["user", "model", "user"]  # consecutive user turns merged
    assert contents[2].parts[0].function_response.id is None


def test_openai_empty_assistant_message_has_string_content() -> None:
    msgs = O.to_request_messages("s", [user("hi"), Message(Role.ASSISTANT, [])])
    assert msgs[2] == {"role": "assistant", "content": ""}


def test_naive_datetimes_in_state_become_utc() -> None:
    from fidus.state.store import parse_state

    text = '{"base": {"repos": {"a/b": {"cursor_merged_at": "2026-09-01T00:00:00"}}}}'
    state = parse_state(text, from_default_branch=True)
    assert state is not None
    assert state.base.repos["a/b"].cursor_merged_at == datetime(2026, 9, 1, tzinfo=UTC)
    advance_cursor(
        state.base.repos["a/b"],
        [
            Trigger(
                repo="a/b",
                alias="b",
                title="t",
                number=1,
                merged_at=datetime(2026, 9, 2, tzinfo=UTC),
            )
        ],
    )


def test_line_number_citations_are_flagged(
    tmp_path: Path, cfg: FidusConfig, outline: Outline
) -> None:
    src = tmp_path / "src"
    (src / "src").mkdir(parents=True)
    (src / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "docs").mkdir()
    ctx = EpisodeContext(
        mode="sync",
        cfg=cfg,
        repo_root=tmp_path,
        workspaces={"app": src},
        outline=outline,
        chapter=outline.get("auth"),
    )
    head = "---\ntitle: A\nfidus:\n  chapter: auth\n---\n# A\n"
    assert any("line number" in p for p in validate_chapter(head + "See `app:src/a.py:12`.\n", ctx))
    assert any("line number" in p for p in validate_chapter(head + "See `app:src/a.py#L3`.\n", ctx))
    assert validate_chapter(head + "See `app:src/a.py`.\n", ctx) == []
