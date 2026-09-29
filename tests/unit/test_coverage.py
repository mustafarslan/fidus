from __future__ import annotations

from pathlib import Path

from fidus.agent.context import EpisodeContext
from fidus.agent.coverage import chapter_symbols, symbols_in, undocumented
from fidus.agent.prompts import chapter_brief
from fidus.config.models import FidusConfig
from fidus.config.outline import Outline
from fidus.sources.trigger import ChangedFile, Trigger


def test_symbol_extraction_per_language() -> None:
    py = "def issue_token():\n    pass\n\nasync def refresh_token():\n    def inner(): pass\nclass Settings:\n    pass\ndef _private(): pass\n"
    assert symbols_in(py, ".py") == [
        "issue_token",
        "refresh_token",
        "Settings",
    ]  # nested and _private skipped
    ts = "export function loadSession() {}\nexport interface Session {}\nfunction local() {}\nexport const KEY = 1\n"
    assert symbols_in(ts, ".ts") == ["loadSession", "Session", "KEY"]
    assert symbols_in("package x\nfunc Public() {}\nfunc private() {}\n", ".go") == ["Public"]
    assert symbols_in("anything", ".md") == []


def _ctx(
    tmp_path: Path, cfg: FidusConfig, outline: Outline, mode: str, doc: str, **kw: object
) -> EpisodeContext:
    src = tmp_path / "src-app"
    (src / "src/auth").mkdir(parents=True, exist_ok=True)
    (src / "src/auth/tokens.py").write_text(
        "def issue_token():\n    pass\n\ndef refresh_token():\n    pass\n"
    )
    (src / "src/auth/other.py").write_text("def unrelated_helper():\n    pass\n")
    (tmp_path / "docs").mkdir(exist_ok=True)
    return EpisodeContext(
        mode=mode,
        cfg=cfg,
        repo_root=tmp_path,
        workspaces={"app": src},
        outline=outline,  # type: ignore[arg-type]
        chapter=outline.get("auth"),
        original_doc=doc,
        **kw,
    )  # type: ignore[arg-type]


DOC = "---\ntitle: A\nfidus:\n  chapter: auth\n---\n# A\nTokens come from `issue_token`.\n"


def test_audit_brief_lists_undocumented_public_symbols(
    tmp_path: Path, cfg: FidusConfig, outline: Outline
) -> None:
    brief = chapter_brief(_ctx(tmp_path, cfg, outline, "audit", DOC))
    assert "### Possibly undocumented" in brief
    assert "`refresh_token` (`app:src/auth/tokens.py`)" in brief
    assert "`issue_token`" not in brief.split("### Possibly undocumented")[1].split("###")[0]
    assert "unrelated_helper" in brief  # audits look at every mapped file


def test_sync_brief_only_considers_changed_files(
    tmp_path: Path, cfg: FidusConfig, outline: Outline
) -> None:
    t = Trigger(
        repo="acme/app",
        alias="app",
        title="refresh",
        number=1,
        files=[ChangedFile("src/auth/tokens.py")],
    )
    brief = chapter_brief(_ctx(tmp_path, cfg, outline, "sync", DOC, triggers=[t]))
    assert "refresh_token" in brief and "unrelated_helper" not in brief


def test_no_section_when_everything_is_mentioned(
    tmp_path: Path, cfg: FidusConfig, outline: Outline
) -> None:
    ctx = _ctx(
        tmp_path, cfg, outline, "audit", DOC + "Also `refresh_token` and `unrelated_helper`.\n"
    )
    assert "Possibly undocumented" not in chapter_brief(ctx)
    syms = chapter_symbols(ctx.workspaces, ["app:src/auth/**"])
    assert undocumented(syms, ctx.original_doc or "") == []
