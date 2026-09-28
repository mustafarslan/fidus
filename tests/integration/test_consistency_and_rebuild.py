from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fidus.config.outline import Outline
from fidus.errors import ConfigError
from fidus.llm.fake import FakeProvider, HeuristicFakeProvider, fake_tool_call
from fidus.llm.types import Completion, Message, ToolSpec
from fidus.mapping.planner import build_plan
from fidus.pipeline.bootstrap import bootstrap
from fidus.pipeline.run import run
from fidus.pipeline.session import Options, Session
from tests.conftest import commit, git


def _bootstrapped(project: dict[str, Path]) -> None:
    out = project["root"] / "boot"
    bootstrap(Options(project["config"], dry_run=True, output=out))
    shutil.copytree(out / "docs", project["docs"] / "docs")
    (project["docs"] / ".fidus").mkdir()
    shutil.copy(out / "state.json", project["docs"] / ".fidus/state.json")
    git(project["docs"], "add", "-A")
    git(project["docs"], "commit", "-qm", "bootstrap")


def _doc(chapter: str, title: str, body: str) -> str:
    return f"---\ntitle: {title}\nfidus:\n  chapter: {chapter}\n---\n\n# {title}\n\n{body}\n"


def scripted(messages: list[Message], tools: list[ToolSpec]) -> Completion:
    """A tiny 'model': renames Model->Entity in the data-model chapter, then fixes auth."""
    brief = messages[0].text
    turn = sum(1 for m in messages if m.role.value == "assistant")
    if "consistency pass on chapter" in brief:
        if turn == 0:
            return fake_tool_call(
                "write_doc", content=_doc("auth", "Authentication", "Users are Entity rows.")
            )
        return fake_tool_call("done", summary="Renamed Model to Entity references.", changed=True)
    if "`data-model`" in brief:
        if turn == 0:
            return fake_tool_call(
                "write_doc",
                content=_doc("data-model", "The Data Model", "The core type is Entity."),
            )
        return fake_tool_call(
            "done",
            summary="Model is now called Entity.",
            changed=True,
            concepts_changed=["Model renamed to Entity"],
        )
    return fake_tool_call("done", summary="no change", changed=False)


def test_consistency_wave_updates_dependents(
    project: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _bootstrapped(project)
    import fidus.pipeline.session as sess

    provider = FakeProvider([scripted] * 10)
    monkeypatch.setattr(sess, "make_provider", lambda *a, **k: provider)
    commit(
        project["app"], {"src/db/models.py": "class Entity:\n    pass\n"}, "refactor: rename Model"
    )
    out = project["root"] / "out"
    rep = run(Options(project["config"], dry_run=True, output=out))
    modes = [(e.mode, e.chapter_id) for e in rep.episodes]
    assert modes == [("sync", "data-model"), ("consistency", "auth")]
    state = json.loads((out / "state.json").read_text())
    assert state["pending"]["consistency"] == {"auth": "Renamed Model to Entity references."}
    assert "Entity rows" in (out / "docs/part-2/01-auth.md").read_text()
    assert "consistency pass" in (out / "pr-body.md").read_text()
    brief = next(
        c["messages"][0].text for c in provider.calls if "consistency pass" in c["messages"][0].text
    )
    assert "Model renamed to Entity" in brief


def test_failed_chapters_survive_a_merge(
    project: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _bootstrapped(project)
    import fidus.pipeline.session as sess
    from fidus.errors import ProviderError

    def boom(messages: list[Message], tools: list[ToolSpec]) -> Completion:
        raise ProviderError("overloaded", retryable=False)

    monkeypatch.setattr(sess, "make_provider", lambda *a, **k: FakeProvider([boom]))
    commit(
        project["app"],
        {"src/auth/login.py": "def login(u, mfa=True):\n    return True\n"},
        "feat: mfa",
    )
    out = project["root"] / "out"
    run(Options(project["config"], dry_run=True, output=out))
    state = json.loads((out / "state.json").read_text())
    # The cursor moved past the PR, but the chapter is remembered in `base` (which survives a merge).
    assert state["base"]["retry"][0]["chapter"] == "auth"
    assert state["base"]["retry"][0]["triggers"][0]["title"] == "feat: mfa"
    assert state["pending"]["triggers"][0]["status"] == "failed"

    # "Merge": state lands on main; pending is dropped when read back, retry is not.
    shutil.copy(out / "state.json", project["docs"] / ".fidus/state.json")
    git(project["docs"], "commit", "-qam", "merge")
    monkeypatch.setattr(sess, "make_provider", lambda *a, **k: HeuristicFakeProvider())
    rep = run(Options(project["config"], dry_run=True, output=project["root"] / "out2"))
    assert [(e.mode, e.chapter_id) for e in rep.episodes] == [("sync", "auth")]  # retried
    state2 = json.loads((project["root"] / "out2/state.json").read_text())
    assert state2["base"]["retry"] == []


def test_planner_keeps_triggerless_rebuilds(tmp_path: Path, outline: Outline) -> None:
    docs = tmp_path / "docs"
    for c in outline.chapters():
        (docs / c.path).parent.mkdir(parents=True, exist_ok=True)
        (docs / c.path).write_text("x")
    plan = build_plan(
        outline=outline,
        triggers=[],
        chapters_for=lambda a, p: [],
        audit_chapters=[],
        rebuild={"auth": []},
        docs_root=docs,
        index_file=None,
        max_episodes=10,
    )
    assert [(i.chapter_id, i.mode, i.rebuild) for i in plan.items] == [("auth", "audit", True)]


def test_refuses_dirty_docs_repo(project: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIDUS_GITHUB_TOKEN", "t")
    (project["docs"] / "notes.md").write_text("wip")
    with pytest.raises(ConfigError, match="uncommitted"):
        Session.open(Options(project["config"]))
