"""Run a scenario: each simulated day = reviewer actions, source PR merges, then a real nightly
`fidus` run (as a subprocess) against the fake GitHub, followed by a snapshot and expectations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fidus.config.loader import dump_yaml, parse_yaml_text, read_yaml
from fidus.sim.server import FakeGitHub, serve
from fidus.sim.world import World


@dataclass
class DayResult:
    day: int
    date: str
    morning: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    command: str = "run"
    exit_code: int = 0
    summary: list[str] = field(default_factory=list)
    pr: str = "none"  # "open #N", "none"
    pr_body: str = ""
    changed_chapters: list[str] = field(default_factory=list)
    noop: bool = False
    paused: bool = False
    audit: bool = False
    retry: list[str] = field(default_factory=list)
    cache_entries: int = 0
    checks: list[tuple[str, bool]] = field(default_factory=list)
    stderr_tail: str = ""


@dataclass
class SimResult:
    name: str
    model: str
    strict: bool
    days: list[DayResult]
    unhandled: list[str]
    final_docs: dict[str, str]
    report_path: Path | None = None
    accuracy: dict[str, Any] | None = None

    @property
    def failed_checks(self) -> list[str]:
        return [f"day {d.day}: {name}" for d in self.days for name, ok in d.checks if not ok]


def _load_dir(path: Path) -> dict[str, str | None]:
    """Fixture files (text only; caches and binaries are skipped)."""
    out: dict[str, str | None] = {}
    for p in sorted(path.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix in {".pyc", ".pyo"}:
            continue
        try:
            out[p.relative_to(path).as_posix()] = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return out


def _chapter_ids(outline_text: str) -> dict[str, str]:
    data = parse_yaml_text(outline_text) or {}
    return {c["path"]: c["id"] for part in data.get("parts", []) for c in part.get("chapters", [])}


class Simulation:
    def __init__(self, scenario_path: Path, root: Path, llm: dict[str, Any] | None = None) -> None:
        self.scenario_path = scenario_path
        self.base = scenario_path.parent
        self.sc: dict[str, Any] = read_yaml(scenario_path)
        self.root = root
        self.llm = llm or {"provider": "fake", "model": "heuristic-fake"}
        self.strict = self.llm.get("provider") == "fake"
        self.docs = self.sc.get("docs_repo", "acme/docs")
        self.start = datetime.fromisoformat(str(self.sc.get("start", "2026-03-02"))).replace(
            tzinfo=UTC
        )
        self.gh = FakeGitHub(root / "git")
        self.server = serve(self.gh)
        self.world = World(root, self.gh)

    def at(self, day: int, hour: int = 10, minute: int = 0) -> datetime:
        return self.start + timedelta(days=day, hours=hour, minutes=minute)

    # -- setup --------------------------------------------------------------------------------
    def setup(self) -> None:
        t0 = self.at(-1)
        for src in self.sc["sources"]:
            self.world.create_repo(src["repo"], _load_dir(self.base / src["fixture"]), t0)
        cfg: dict[str, Any] = {
            "version": 1,
            "docs": {"dir": "docs", "outline": "fidus.outline.yaml"},
            "sources": [{k: v for k, v in s.items() if k != "fixture"} for s in self.sc["sources"]],
            "llm": self.llm,
            "github": {"api_url": self.gh.base_url, "server_url": f"file://{self.gh.git_root}"},
        }
        for key, value in (self.sc.get("config") or {}).items():
            cfg[key] = {**cfg.get(key, {}), **value} if isinstance(value, dict) else value
        outline = (self.base / self.sc["outline"]).read_text(encoding="utf-8")
        self.world.create_repo(
            self.docs, {"fidus.yaml": dump_yaml(cfg), "fidus.outline.yaml": outline}, t0
        )

    # -- one night ----------------------------------------------------------------------------
    def night(self, day: int, command: str) -> tuple[int, str, str]:
        work = self.world.fresh_checkout(self.docs, f"night-{day:02d}")
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORY", "GITHUB_STEP_SUMMARY"}
        }
        env.update(
            {
                "FIDUS_GITHUB_TOKEN": "sim-token",
                "FIDUS_DOCS_REPO": self.docs,
                "FIDUS_NOW": self.at(day + 1, hour=0).isoformat(),
                "FIDUS_CACHE_DIR": str(self.root / "cache"),
                "FIDUS_GIT_USER_NAME": "fidus-sim[bot]",
                "FIDUS_GIT_USER_EMAIL": "fidus-sim[bot]@users.noreply.github.com",
            }
        )
        self.gh.now = self.at(day + 1, hour=0)
        proc = subprocess.run(
            [sys.executable, "-m", "fidus", command, "-c", str(work / "fidus.yaml")],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def snapshot(self, result: DayResult, before_sync: str | None) -> None:
        pr = self.world.open_fidus_pr(self.docs)
        result.pr = f"open #{pr['number']} ({pr['head']['ref']})" if pr else "none"  # type: ignore[index]
        result.pr_body = str(pr["body"]) if pr else ""
        result.paused = "Fidus is paused" in result.pr_body
        after = self.gh.ref_sha(
            self.docs, "fidus/bootstrap" if result.command == "bootstrap" else "fidus/sync"
        )
        base = self.gh.ref_sha(self.docs, self.gh.default_branch)
        if (
            before_sync
            and after
            and subprocess.run(
                ["git", "merge-base", "--is-ancestor", before_sync, after],
                cwd=self.gh.bare(self.docs),
            ).returncode
            == 0
        ):
            base = before_sync  # the branch grew tonight: diff only tonight's commits
        outline = self.world.read(self.docs, self.gh.default_branch, "fidus.outline.yaml") or ""
        ids = _chapter_ids(outline)
        if after and base and after != base:
            changed = subprocess.run(
                ["git", "diff", "--name-only", base, after, "--", "docs/"],
                cwd=self.gh.bare(self.docs),
                capture_output=True,
                text=True,
            ).stdout.split()
            result.changed_chapters = sorted(ids[p[5:]] for p in changed if p[5:] in ids)
        result.noop = any("Nothing to do" in s or "nothing published" in s for s in result.summary)
        result.audit = any("audit=True" in s for s in result.summary)
        state_ref = (
            "fidus/sync" if self.gh.ref_sha(self.docs, "fidus/sync") else self.gh.default_branch
        )
        state = json.loads(self.world.read(self.docs, state_ref, ".fidus/state.json") or "{}")
        retry = (state.get("base") or {}).get("retry") or []
        result.retry = [
            f"{e['chapter']}{' (quarantined)' if e.get('quarantined') else ''}" for e in retry
        ]
        cache = self.world.read(self.docs, "fidus/cache", "triage-cache.json")
        result.cache_entries = len(json.loads(cache)["entries"]) if cache else 0

    def check(self, result: DayResult, expect: dict[str, Any]) -> None:
        for key, want in (expect or {}).items():
            if key == "pr":
                ok = result.pr.startswith("open") if want == "open" else result.pr == "none"
            elif key == "noop":
                ok = result.noop == want
            elif key == "paused":
                ok = result.paused == want
            elif key == "audit":
                ok = result.audit == want
            elif key == "changed":
                ok = set(want) <= set(result.changed_chapters)
            elif key == "unchanged":
                ok = not result.changed_chapters
            elif key == "exit":
                ok = result.exit_code == want
            elif key == "body_contains":
                ok = all(s in result.pr_body for s in ([want] if isinstance(want, str) else want))
            elif (
                key == "chapter_contains"
            ):  # {chapter-id: text} on the current Fidus branch (or main)
                ok = all(text in self._chapter_text(cid) for cid, text in want.items())
            elif key == "retry":
                ok = sorted(result.retry) == sorted(want)
            else:
                ok = False
            result.checks.append((f"{key} == {want!r}", ok))

    def _chapter_text(self, chapter_id: str) -> str:
        ref = "fidus/sync" if self.gh.ref_sha(self.docs, "fidus/sync") else self.gh.default_branch
        outline = self.world.read(self.docs, self.gh.default_branch, "fidus.outline.yaml") or ""
        path = {cid: p for p, cid in _chapter_ids(outline).items()}.get(chapter_id)
        return (self.world.read(self.docs, ref, f"docs/{path}") or "") if path else ""

    # -- the whole scenario ---------------------------------------------------------------------
    def run(self) -> SimResult:
        self.setup()
        days: list[DayResult] = []
        for spec in self.sc["days"]:
            d = int(spec["day"])
            res = DayResult(day=d, date=self.at(d).date().isoformat())
            review = spec.get("review")
            t = self.at(d, hour=9)
            if review == "merge":
                res.morning.append(
                    self.world.review_merge(self.docs, t) or "merge: no open Fidus PR"
                )
            elif review == "close":
                res.morning.append(
                    self.world.review_close(self.docs, t) or "close: no open Fidus PR"
                )
            for rel, text in (spec.get("edit_branch") or {}).items():
                res.morning.append(
                    self.world.edit(
                        self.docs, "fidus/sync", {rel: text}, t, f"Reviewer edits {rel}"
                    )
                )
            for rel, text in (spec.get("edit_main") or {}).items():
                res.morning.append(
                    self.world.edit(
                        self.docs,
                        self.gh.default_branch,
                        {rel: text},
                        t,
                        f"Someone edits {rel} on main",
                    )
                )
            if spec.get("outline"):
                outline = (self.base / spec["outline"]).read_text(encoding="utf-8")
                res.morning.append(
                    self.world.commit_files(
                        self.docs,
                        self.gh.default_branch,
                        {"fidus.outline.yaml": outline},
                        t,
                        "Reviewer updates the outline",
                    )
                )
            for i, m in enumerate(spec.get("merges") or []):
                pr = self.world.merge_pr(
                    m["repo"],
                    m["title"],
                    m.get("body", ""),
                    m.get("files", {}),
                    self.at(d, hour=10, minute=7 * i),
                )
                res.merged.append(
                    f"{m['repo']}#{pr['number']} {m['title']} ({len(pr['files'])} files)"
                )
            before = self.gh.ref_sha(self.docs, "fidus/sync")
            res.command = "bootstrap" if spec.get("bootstrap") else "run"
            code, out, err = self.night(d, res.command)
            res.exit_code = code
            res.summary = [
                ln.strip()
                for ln in (out + err).splitlines()
                if ln.strip().startswith(("-", "###", "Nothing")) and "INFO" not in ln
            ][-8:]
            res.stderr_tail = "\n".join(err.strip().splitlines()[-6:]) if code not in (0, 3) else ""
            self.snapshot(res, before)
            self.check(res, spec.get("expect") or {})
            days.append(res)
        final_ref = (
            "fidus/sync" if self.gh.ref_sha(self.docs, "fidus/sync") else self.gh.default_branch
        )
        files = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", final_ref, "docs/"],
            cwd=self.gh.bare(self.docs),
            capture_output=True,
            text=True,
        ).stdout.split()
        final = {f: self.world.read(self.docs, final_ref, f) or "" for f in files}
        self.server.shutdown()
        return SimResult(
            self.sc.get("name", self.scenario_path.stem),
            f"{self.llm.get('provider')}/{self.llm.get('model')}",
            self.strict,
            days,
            list(self.gh.unhandled),
            final,
        )
