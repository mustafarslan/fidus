from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from fidus.cli.app import app as cli
from fidus.llm.fake import FakeProvider, fake_text, fake_tool_call
from fidus.pipeline.outline_proposal import revise_outline
from fidus.pipeline.session import Options, Session

runner = CliRunner()


def test_revise_outline_retries_until_valid(project: dict[str, Path]) -> None:
    session = Session.open(Options(project["config"]), need_git=False)
    assert session.outline is not None
    good = session.outline.model_dump(mode="json")
    good["parts"][1]["chapters"].append(
        {
            "id": "webhooks",
            "title": "Webhooks",
            "path": "part-2/02-webhooks.md",
            "sources": ["app:src/hooks/**"],
            "prerequisites": ["auth"],
        }
    )
    bad = json.loads(json.dumps(good))
    bad["parts"][0]["chapters"][0]["prerequisites"] = ["auth"]  # must point backwards
    session._provider = FakeProvider(
        [
            fake_text("Here is my idea"),
            fake_tool_call("propose_outline", **bad),
            fake_tool_call("propose_outline", **good),
        ]
    )
    outline = revise_outline(session, ["Add a webhooks chapter"])
    assert outline is not None and outline.get("webhooks").prerequisites == ["auth"]
    rejected = session._provider.calls[2]["messages"][-1].parts[0]  # type: ignore[union-attr]
    assert rejected.is_error and "earlier" in rejected.content


def test_state_set_cursor_and_show(project: dict[str, Path]) -> None:
    cfg = str(project["config"])
    res = runner.invoke(cli, ["state", "show", "-c", cfg])
    assert "no state yet" in res.output
    res = runner.invoke(
        cli,
        ["state", "set-cursor", "--repo", "local:app", "--to", "2026-09-01T00:00:00Z", "-c", cfg],
    )
    assert res.exit_code == 0, res.output
    state = json.loads((project["docs"] / ".fidus/state.json").read_text())
    assert state["base"]["repos"]["local:app"]["cursor_merged_at"].startswith("2026-09-01")
    res = runner.invoke(cli, ["state", "set-cursor", "--repo", "bad", "--to", "now", "-c", cfg])
    assert res.exit_code == 2


def test_clone_sources_is_thread_safe(project: dict[str, Path], monkeypatch: object) -> None:
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    import fidus.pipeline.session as sess

    calls: list[str] = []
    real = sess.clone_source

    def slow_clone(*args: object, **kwargs: object) -> object:
        calls.append(threading.current_thread().name)
        time.sleep(0.05)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sess, "clone_source", slow_clone)  # type: ignore[attr-defined]
    session = Session.open(Options(project["config"]), need_git=False)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: session.clone_sources(), range(4)))
    assert len(calls) == 1  # one source, cloned exactly once
