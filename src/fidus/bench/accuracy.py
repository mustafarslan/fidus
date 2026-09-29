"""Accuracy benchmark: have a judge model check each chapter's claims against its cited sources.

    python -m fidus.bench.accuracy DOCS_DIR -c fidus.yaml [--provider P --model M --base-url U]

Writes accuracy.json (per-chapter claims and verdicts, plus scores) next to DOCS_DIR, or to
--out. Scores are supported / (supported + contradicted + unsupported); `contradicted` claims are
the real errors. Use it to compare prompt or model changes before and after.

Judge quality matters: a judge weaker than the writer waves errors through. Verdicts must quote
verbatim source evidence (checked mechanically), which limits but doesn't eliminate that effect.
Use a judge at least as strong as the model that wrote the docs.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from fidus.agent.guards import resolve_read_path, wrap_untrusted
from fidus.agent.prompts import render
from fidus.errors import FidusError, GuardViolation
from fidus.llm.base import Provider
from fidus.llm.types import user
from fidus.pipeline.session import Options, Session

CITATION = re.compile(r"`([a-z0-9][a-z0-9_-]*):([^`\s:#*?]+)`")
MAX_SOURCE_CHARS = 60_000


class Claim(BaseModel):
    claim: str
    verdict: Literal["supported", "contradicted", "unsupported"]
    evidence: str = ""


class Verdicts(BaseModel):
    claims: list[Claim]


@dataclass
class ChapterScore:
    path: str
    claims: list[dict[str, str]] = field(default_factory=list)
    supported: int = 0
    contradicted: int = 0
    unsupported: int = 0
    error: str | None = None

    @property
    def total(self) -> int:
        return self.supported + self.contradicted + self.unsupported

    @property
    def score(self) -> float | None:
        return self.supported / self.total if self.total else None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip("`").lower()


def verify_evidence(verdict: Claim, source_text: str) -> Claim:
    """Downgrade supported/contradicted verdicts whose quoted evidence isn't in the sources.

    A deterministic guard against lenient judges: weaker models tend to wave claims through.
    """
    if verdict.verdict == "unsupported":
        return verdict
    ev = _norm(verdict.evidence)
    if len(ev) >= 4 and ev in _norm(source_text):
        return verdict
    return Claim(
        claim=verdict.claim,
        verdict="unsupported",
        evidence=f"(evidence not found in sources; judge said {verdict.verdict}: {verdict.evidence[:120]})",
    )


def cited_sources(text: str, workspaces: dict[str, Path]) -> str:
    """The cited source files, each wrapped as untrusted data, up to a size cap."""
    chunks: list[str] = []
    size = 0
    for alias, path in dict.fromkeys(CITATION.findall(text)):
        root = workspaces.get(alias)
        if root is None:
            continue
        try:
            p = resolve_read_path(root, path)
        except GuardViolation:
            continue
        if not p.is_file():
            continue
        body = p.read_text(encoding="utf-8", errors="replace")
        if size + len(body) > MAX_SOURCE_CHARS:
            body = body[: max(0, MAX_SOURCE_CHARS - size)] + "\n... [truncated]"
        chunks.append(wrap_untrusted(f"{alias}:{path}", body))
        size += len(body)
        if size >= MAX_SOURCE_CHARS:
            break
    return "\n\n".join(chunks) or "(the chapter cites no readable source files)"


def _parse(text: str) -> Verdicts:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in judge reply")
    return Verdicts.model_validate(json.loads(m.group(0)))


def judge_chapter(
    path: Path, rel: str, workspaces: dict[str, Path], judge: Provider, max_claims: int
) -> ChapterScore:
    text = path.read_text(encoding="utf-8")
    sources = cited_sources(text, workspaces)
    prompt = render(
        "judge_accuracy",
        max_claims=max_claims,
        chapter=wrap_untrusted(f"docs:{rel}", text),
        sources=sources,
    )
    result = ChapterScore(rel)
    try:
        reply = judge.complete(
            system="You are a meticulous technical fact-checker. Output JSON only.",
            messages=[user(prompt)],
            tools=[],
            max_tokens=4000,
            temperature=None,
        )
        verdicts = _parse(reply.message.text)
    except (FidusError, ValueError, ValidationError, json.JSONDecodeError) as e:
        result.error = str(e)[:300]
        return result
    for raw in verdicts.claims[:max_claims]:
        c = verify_evidence(raw, sources)
        result.claims.append(c.model_dump())
        setattr(result, c.verdict, getattr(result, c.verdict) + 1)
    return result


def run_benchmark(docs_dir: Path, opts: Options, *, max_claims: int = 15) -> dict[str, Any]:
    session = Session.open(opts, need_outline=False, need_git=False)
    try:
        workspaces = {a: w.root for a, w in session.clone_sources().items()}
        judge = session.provider()
        chapters = sorted(
            p for p in docs_dir.rglob("*.md") if "fidus:" in p.read_text(encoding="utf-8")[:400]
        )
        scores = [
            judge_chapter(p, p.relative_to(docs_dir).as_posix(), workspaces, judge, max_claims)
            for p in chapters
        ]
    finally:
        session.close()
    judged = [s for s in scores if s.total]
    totals = {
        k: sum(getattr(s, k) for s in scores) for k in ("supported", "contradicted", "unsupported")
    }
    all_claims = sum(totals.values())
    return {
        "judge": f"{session.cfg.llm.provider}/{session.cfg.llm.model}",
        "chapters": [asdict(s) | {"score": s.score} for s in scores],
        "totals": totals,
        "score": round(totals["supported"] / all_claims, 3) if all_claims else None,
        "error_rate": round(totals["contradicted"] / all_claims, 3) if all_claims else None,
        "chapters_judged": len(judged),
        "chapters_failed": sum(1 for s in scores if s.error),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("docs_dir", type=Path)
    ap.add_argument("-c", "--config", type=Path, default=Path("fidus.yaml"))
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    ap.add_argument("--max-claims", type=int, default=15)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    opts = Options(args.config, provider=args.provider, model=args.model, base_url=args.base_url)
    report = run_benchmark(args.docs_dir.resolve(), opts, max_claims=args.max_claims)
    out = args.out or args.docs_dir.resolve().parent / "accuracy.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"judge: {report['judge']}")
    for ch in report["chapters"]:
        s = ch["score"]
        print(
            f"  {ch['path']:<50} {'-' if s is None else f'{s:.0%}':>5}  "
            f"contradicted={ch['contradicted']} unsupported={ch['unsupported']}"
            + (f"  ERROR: {ch['error']}" if ch["error"] else "")
        )
    print(f"overall score: {report['score']}  error rate: {report['error_rate']}  -> {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
