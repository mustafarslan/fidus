from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fidus.config.models import FidusConfig, Limits, SourceConfig
from fidus.config.outline import Outline
from fidus.llm.fake import FakeProvider, fake_text
from fidus.mapping.globs import ChapterMatcher
from fidus.mapping.planner import build_plan
from fidus.mapping.triage import triage
from fidus.pipeline.index import render_index
from fidus.pipeline.schedule import audit_due, audit_selection, resolve_modes
from fidus.publish.pr_body import MARKER, render_pr_body
from fidus.sources.filters import apply_filters
from fidus.sources.github_source import advance_cursor
from fidus.sources.trigger import ChangedFile, Trigger
from fidus.state.models import Finding, PendingAudit, PendingTrigger, RepoCursor, State
from fidus.state.store import dump_state, parse_state

NOW = datetime(2026, 9, 27, 0, 0, tzinfo=UTC)


def trig(files: list[str], number: int = 1, alias: str = "app") -> Trigger:
    return Trigger(
        repo="acme/app",
        alias=alias,
        title=f"PR {number}",
        number=number,
        files=[ChangedFile(f, patch="+x") for f in files],
    )


def test_filters_drop_excluded_and_truncate() -> None:
    src = SourceConfig(repo="acme/app", include=["src/**"], exclude=["**/tests/**"])
    t = Trigger(
        repo="acme/app",
        alias="app",
        title="t",
        body="b" * 10_000,
        files=[
            ChangedFile("src/a.py", patch="x" * 50_000),
            ChangedFile("src/tests/test_a.py", patch="y"),
            ChangedFile("package-lock.json", patch="z"),
            ChangedFile("docs/readme.md", patch="w"),
        ],
    )
    apply_filters(t, src, Limits(diff_max_bytes_per_file=1000, pr_body_max_chars=100))
    assert [f.path for f in t.files] == ["src/a.py"]
    assert t.omitted_files == 3
    assert "truncated" in (t.files[0].patch or "")
    assert len(t.body) < 200


def test_matcher_and_plan(tmp_path: Path, outline: Outline) -> None:
    docs = tmp_path / "docs"
    (docs / "part-1").mkdir(parents=True)
    (docs / "part-1/01-overview.md").write_text("x")
    (docs / "part-1/02-data-model.md").write_text("x")
    (docs / "stray.md").write_text("x")
    m = ChapterMatcher(outline)
    assert m.chapters_for("app", "src/db/models.py") == ["data-model"]
    assert m.chapters_for("app", "src/other.py") == []
    t1, t2 = trig(["src/db/models.py", "scripts/x.py"], 1), trig(["README.md"], 2)
    plan = build_plan(
        outline=outline,
        triggers=[t1, t2],
        chapters_for=m.chapters_for,
        audit_chapters=[],
        rebuild={},
        docs_root=docs,
        index_file="README.md",
        max_episodes=10,
    )
    modes = {i.chapter_id: i.mode for i in plan.items}
    # auth has no file yet -> written as a new chapter; overview/data-model synced
    assert modes == {"overview": "sync", "data-model": "sync", "auth": "bootstrap"}
    assert plan.unmapped == {"acme/app#1": ["app:scripts/x.py"]}
    assert plan.orphans == ["stray.md"]
    capped = build_plan(
        outline=outline,
        triggers=[t1, t2],
        chapters_for=m.chapters_for,
        audit_chapters=[],
        rebuild={},
        docs_root=docs,
        index_file="README.md",
        max_episodes=1,
    )
    assert len(capped.items) == 1 and len(capped.deferred) == 2


def test_audit_schedule(cfg: FidusConfig, outline: Outline) -> None:
    s = State()
    assert audit_due(s, cfg, NOW)  # never audited
    s.base.audit.last_run_at = NOW - timedelta(days=3)
    assert not audit_due(s, cfg, NOW)
    assert resolve_modes("auto", s, cfg, NOW) == (True, False)
    assert resolve_modes("full", s, cfg, NOW) == (True, True)
    s.base.audit.last_run_at = NOW - timedelta(days=7)
    assert audit_due(s, cfg, NOW)
    cfg.schedule.audit.chapters_per_run = 2
    picked, nxt = audit_selection(outline, s, cfg)
    assert picked == ["overview", "data-model"] and nxt == 2
    s.base.audit.rotation_index = nxt
    picked, nxt = audit_selection(outline, s, cfg)
    assert picked == ["auth", "overview"] and nxt == 1


def test_state_roundtrip_and_default_branch_clears_pending() -> None:
    s = State()
    s.pending.runs = 3
    s.pending.triggers.append(PendingTrigger(repo="a/b", number=1, title="x"))
    text = dump_state(s)
    assert parse_state(text, from_default_branch=False).pending.runs == 3  # type: ignore[union-attr]
    assert parse_state(text, from_default_branch=True).pending.runs == 0  # type: ignore[union-attr]
    assert parse_state(None, from_default_branch=True) is None


def test_cursor_handles_equal_timestamps() -> None:
    c = RepoCursor()
    t = datetime(2026, 1, 1, tzinfo=UTC)
    a, b = trig([], 5), trig([], 6)
    a.merged_at = b.merged_at = t
    advance_cursor(c, [a, b])
    assert c.cursor_merged_at == t and c.cursor_pr_numbers == [5, 6]
    later = trig([], 7)
    later.merged_at = t + timedelta(hours=1)
    advance_cursor(c, [later])
    assert c.cursor_pr_numbers == [7]


def test_pr_body_rendering(cfg: FidusConfig, outline: Outline) -> None:
    s = State()
    s.pending.runs = 2
    s.pending.opened_at = NOW
    s.pending.triggers = [
        PendingTrigger(
            repo="acme/app",
            number=12,
            title="TTL | config",
            url="https://github.com/acme/app/pull/12",
            status="applied",
            chapters={"auth": "Documented `AUTH_TTL` | new"},
        ),
        PendingTrigger(repo="acme/app", number=13, title="CI only", status="no_impact"),
        PendingTrigger(
            repo="acme/app",
            number=14,
            title="Scripts",
            status="unmapped",
            unmapped_files=["app:scripts/x.py"],
        ),
    ]
    s.pending.audit = PendingAudit(
        ran_at=NOW,
        chapters_checked=["overview"],
        changed={"overview": "fixed drift"},
        findings=[Finding(chapter="overview", severity="warn", text="stale name")],
        structure_suggestions=["Add a webhooks chapter"],
    )
    body = render_pr_body(s.pending, outline, cfg)
    assert "1 source change(s) → 2 chapter(s)" in body
    assert "[acme/app#12](https://github.com/acme/app/pull/12)" in body
    assert "\\|" in body  # pipes escaped inside table cells
    assert "[1.1 Overview](docs/part-1/01-overview.md) | weekly audit" in body
    assert "Reviewed, no documentation impact (1)" in body
    assert "`app:scripts/x.py`" in body
    assert "Add a webhooks chapter" in body
    assert body.rstrip().endswith(MARKER)
    # Rows follow outline order (fundamentals first)
    assert body.index("1.1 Overview") < body.index("2.1 Authentication")


def test_index_render(outline: Outline) -> None:
    idx = render_index(outline)
    assert "## Part I: Foundations" in idx
    assert "1.2. 🟢 [The Data Model](part-1/02-data-model.md)" in idx


def test_triage_threshold_and_bad_output(cfg: FidusConfig, outline: Outline) -> None:
    reply = (
        '[{"file":"app:lib/a.py","chapter":"auth","confidence":0.9},'
        '{"file":"app:lib/b.py","chapter":"auth","confidence":0.2},'
        '{"file":"app:lib/c.py","chapter":"not-a-chapter","confidence":0.99}]'
    )
    mapping, _ = triage(
        ["app:lib/a.py", "app:lib/b.py", "app:lib/c.py"],
        outline,
        cfg,
        FakeProvider([fake_text("Sure:\n" + reply)]),
    )
    assert mapping == {"app:lib/a.py": "auth"}
    mapping, _ = triage(["app:lib/a.py"], outline, cfg, FakeProvider([fake_text("I cannot help")]))
    assert mapping == {}
