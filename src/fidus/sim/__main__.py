"""python -m fidus.sim SCENARIO [--provider P --model M --base-url U] [--out sim-report.md]"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from fidus.sim import run_scenario
from fidus.sim.report import render


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fidus.sim", description=__doc__)
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--provider", default="fake", help="fake (default), anthropic, gemini, openai")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key-env", default=None)
    ap.add_argument("--out", type=Path, default=Path("sim-report.md"))
    ap.add_argument("--workdir", type=Path, default=None, help="Keep the simulated world here.")
    ap.add_argument(
        "--bench", action="store_true", help="Run the accuracy benchmark on the final book."
    )
    args = ap.parse_args(argv)

    llm: dict[str, object] = {"provider": args.provider, "model": args.model or "heuristic-fake"}
    if args.base_url:
        llm["base_url"] = args.base_url
    if args.api_key_env:
        llm["api_key_env"] = args.api_key_env
    root = args.workdir or Path(tempfile.mkdtemp(prefix="fidus-sim-"))
    result = run_scenario(args.scenario.resolve(), root.resolve(), llm)
    if args.bench and result.final_docs:
        result.accuracy = _bench(root, result)
    args.out.write_text(render(result), encoding="utf-8")
    for d in result.days:
        mark = "" if not d.checks else (" ✓" if all(ok for _, ok in d.checks) else " ✗")
        print(
            f"day {d.day}: {d.command:<9} PR {d.pr:<28} changed={','.join(d.changed_chapters) or '-'}{mark}"
        )
    print(f"report: {args.out}  (world kept in {root})")
    failed = result.failed_checks
    if failed and result.strict:
        print("FAILED expectations:\n  " + "\n  ".join(failed))
        return 1
    return 0


def _bench(root: Path, result: object) -> dict[str, object]:
    from fidus.bench.accuracy import run_benchmark
    from fidus.pipeline.session import Options

    docs_dir = root / "final-book" / "docs"
    for rel, text in result.final_docs.items():  # type: ignore[attr-defined]
        p = root / "final-book" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    night_cfgs = sorted((root / "nights").glob("night-*/fidus.yaml"))
    report = run_benchmark(docs_dir, Options(night_cfgs[-1]))
    json.dumps(report)  # fail early if not serializable
    return report


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
