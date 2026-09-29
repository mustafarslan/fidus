from __future__ import annotations

import json
from pathlib import Path

import pytest

from fidus.bench import accuracy
from fidus.llm.fake import FakeProvider, fake_text
from fidus.pipeline.session import Options

CHAPTER = """---
title: Auth
fidus:
  chapter: auth
---
# Auth
`login` lives in `app:src/auth/login.py`, takes `user` and returns True.
"""


def test_benchmark_scores_claims(project: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    docs = project["root"] / "out" / "docs"
    (docs / "part-2").mkdir(parents=True)
    (docs / "part-2/01-auth.md").write_text(CHAPTER)
    (docs / "README.md").write_text("# index without frontmatter\n")  # not a chapter: skipped
    seen: list[str] = []

    def judge(messages, tools):  # type: ignore[no-untyped-def]
        seen.append(messages[0].text)
        return fake_text(
            'Sure! {"claims": ['
            '{"claim": "login takes user", "verdict": "supported", "evidence": "def login(user)"},'
            '{"claim": "returns a token", "verdict": "contradicted", "evidence": "return True"},'
            '{"claim": "rate limited", "verdict": "unsupported"}]}'
        )

    import fidus.pipeline.session as sess

    monkeypatch.setattr(sess, "make_provider", lambda *a, **k: FakeProvider([judge]))
    report = accuracy.run_benchmark(docs, Options(project["config"]))
    assert report["totals"] == {"supported": 1, "contradicted": 1, "unsupported": 1}
    assert report["score"] == 0.333 and report["error_rate"] == 0.333
    assert report["chapters_judged"] == 1
    # The judge saw the cited source file as untrusted data.
    assert (
        "def login(user)" in seen[0]
        and '<untrusted_data source="app:src/auth/login.py">' in seen[0]
    )


def test_benchmark_cli_writes_json(
    project: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:  # type: ignore[no-untyped-def]
    docs = project["root"] / "o2" / "docs"
    docs.mkdir(parents=True)
    (docs / "a.md").write_text(CHAPTER)
    import fidus.pipeline.session as sess

    monkeypatch.setattr(
        sess, "make_provider", lambda *a, **k: FakeProvider([fake_text("not json")])
    )
    assert accuracy.main([str(docs), "-c", str(project["config"])]) == 0
    report = json.loads((docs.parent / "accuracy.json").read_text())
    assert report["chapters_failed"] == 1 and report["score"] is None
    assert "ERROR" in capsys.readouterr().out


def test_unverifiable_evidence_is_downgraded() -> None:
    src = "def checkout_new(self, branch, start):\n    ..."
    ok = accuracy.verify_evidence(
        accuracy.Claim(
            claim="has checkout_new", verdict="supported", evidence="def checkout_new(self, branch,"
        ),
        src,
    )
    assert ok.verdict == "supported"
    bad = accuracy.verify_evidence(
        accuracy.Claim(claim="has checkout", verdict="supported", evidence="def checkout(self)"),
        src,
    )
    assert bad.verdict == "unsupported" and "evidence not found" in bad.evidence
