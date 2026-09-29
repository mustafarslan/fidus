"""A local fake of the GitHub REST endpoints Fidus uses, backed by bare git repositories.

Runs as a real HTTP server so the unmodified `fidus` CLI can talk to it, exactly as it talks to
api.github.com in the GitHub Action. Pull requests for source repos are registered by the
simulation (see world.py); PRs on the docs repo are created and updated by Fidus via the API.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    """State + request handling. Thread-safe; the HTTP layer is in `serve()`."""

    def __init__(self, git_root: Path, default_branch: str = "main") -> None:
        self.git_root = git_root
        self.default_branch = default_branch
        self.prs: dict[str, list[dict[str, Any]]] = {}  # slug -> PRs (numbers are per repo)
        self.comments: list[tuple[str, int, str]] = []
        self.unhandled: list[str] = []  # requests we don't implement (surfaced in the report)
        self.now = datetime.now(UTC)  # the simulation clock; the runner moves it
        self.base_url = ""  # set by serve()
        self._lock = threading.RLock()

    # -- git helpers ----------------------------------------------------------------------------
    def bare(self, slug: str) -> Path:
        return self.git_root / f"{slug}.git"

    def ref_sha(self, slug: str, branch: str) -> str | None:
        out = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=self.bare(slug),
            capture_output=True,
            text=True,
        ).stdout.strip()
        return out or None

    # -- PR bookkeeping (used by the simulation) -------------------------------------------------
    def register_pr(self, slug: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            prs = self.prs.setdefault(slug, [])
            number = len(prs) + 1
            pr = {
                "number": number,
                "state": "open",
                "title": "",
                "body": "",
                "draft": False,
                "merged_at": None,
                "merge_commit_sha": None,
                "created_at": iso(self.now),
                "updated_at": iso(self.now),
                "html_url": f"https://github.example/{slug}/pull/{number}",
                "head": {"ref": "", "sha": None},
                "base": {"ref": self.default_branch},
                "labels": [],
                "requested_reviewers": [],
                "files": [],
                "changed_files": 0,
            }
            pr.update(fields)
            prs.append(pr)
            return pr

    def find_pr(self, slug: str, head: str, state: str = "open") -> dict[str, Any] | None:
        with self._lock:
            for pr in reversed(self.prs.get(slug, [])):
                if pr["head"]["ref"] == head and pr["state"] == state:
                    return pr
        return None

    def close_pr(self, slug: str, pr: dict[str, Any], *, merged: bool) -> None:
        """Close (or mark merged). Like GitHub, the head sha is frozen at this moment."""
        with self._lock:
            pr["head"]["sha"] = self.ref_sha(slug, pr["head"]["ref"]) or pr["head"]["sha"]
            pr["state"] = "closed"
            pr["updated_at"] = iso(self.now)
            if merged:
                pr["merged_at"] = iso(self.now)

    def _view(self, slug: str, pr: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in pr.items() if k != "files"}
        if pr["state"] == "open":  # open PRs report the live branch tip
            out["head"] = dict(pr["head"], sha=self.ref_sha(slug, pr["head"]["ref"]))
        return out

    # -- request handling -----------------------------------------------------------------------
    def handle(
        self, method: str, path: str, query: dict[str, str], body: Any
    ) -> tuple[int, Any, dict[str, str]]:
        with self._lock:
            return self._route(method, path, query, body)

    def _page(
        self, path: str, query: dict[str, str], items: list[Any]
    ) -> tuple[int, Any, dict[str, str]]:
        per_page = int(query.get("per_page", "30"))
        page = int(query.get("page", "1"))
        chunk = items[(page - 1) * per_page : page * per_page]
        headers = {}
        if page * per_page < len(items):
            q = dict(query, page=str(page + 1))
            qs = "&".join(f"{k}={v}" for k, v in q.items())
            headers["Link"] = f'<{self.base_url}{path}?{qs}>; rel="next"'
        return 200, chunk, headers

    def _route(
        self, method: str, path: str, q: dict[str, str], body: Any
    ) -> tuple[int, Any, dict[str, str]]:
        if method == "GET" and path == "/installation/repositories":
            return 403, {"message": "must authenticate with an installation token"}, {}
        m = re.fullmatch(r"/repos/([\w.-]+/[\w.-]+)(/.*)?", path)
        if not m:
            return self._unhandled(method, path)
        slug, rest = m.group(1), m.group(2) or ""
        if not self.bare(slug).exists():
            return 404, {"message": "Not Found"}, {}

        if method == "GET" and rest == "":
            return (
                200,
                {
                    "full_name": slug,
                    "default_branch": self.default_branch,
                    "permissions": {"admin": False, "push": True, "pull": True},
                },
                {},
            )
        if method == "GET" and (b := re.fullmatch(r"/branches/(.+)", rest)):
            sha = self.ref_sha(slug, b.group(1))
            if sha is None:
                return 404, {"message": "Branch not found"}, {}
            return 200, {"name": b.group(1), "commit": {"sha": sha}}, {}
        if method == "DELETE" and (r := re.fullmatch(r"/git/refs/heads/(.+)", rest)):
            if self.ref_sha(slug, r.group(1)) is None:
                return 422, {"message": "Reference does not exist"}, {}
            subprocess.run(
                ["git", "update-ref", "-d", f"refs/heads/{r.group(1)}"],
                cwd=self.bare(slug),
                check=True,
            )
            return 204, None, {}
        if method == "GET" and rest == "/pulls":
            return self._list_pulls(slug, path, q)
        if method == "POST" and rest == "/pulls":
            pr = self.register_pr(
                slug,
                title=body["title"],
                body=body.get("body", ""),
                draft=body.get("draft", False),
                head={"ref": body["head"].split(":")[-1], "sha": None},
                base={"ref": body["base"]},
            )
            return 201, self._view(slug, pr), {}
        if p := re.fullmatch(r"/pulls/(\d+)(/files)?", rest):
            prs = self.prs.get(slug, [])
            n = int(p.group(1))
            if not 1 <= n <= len(prs):
                return 404, {"message": "Not Found"}, {}
            pr = prs[n - 1]
            if method == "GET" and p.group(2):
                return self._page(path, q, pr["files"])
            if method == "GET":
                return 200, self._view(slug, pr), {}
            if method == "PATCH":
                for key in ("title", "body"):
                    if key in body:
                        pr[key] = body[key]
                pr["updated_at"] = iso(self.now)
                return 200, self._view(slug, pr), {}
        if method == "POST" and (i := re.fullmatch(r"/issues/(\d+)/(labels|comments)", rest)):
            pr = self.prs.get(slug, [])[int(i.group(1)) - 1]
            if i.group(2) == "labels":
                pr["labels"] = sorted(set(pr["labels"]) | set(body.get("labels", [])))
                return 200, [{"name": x} for x in pr["labels"]], {}
            self.comments.append((slug, pr["number"], body.get("body", "")))
            return 201, {"id": len(self.comments)}, {}
        if method == "POST" and (rv := re.fullmatch(r"/pulls/(\d+)/requested_reviewers", rest)):
            pr = self.prs.get(slug, [])[int(rv.group(1)) - 1]
            pr["requested_reviewers"] = body.get("reviewers", [])
            return 201, self._view(slug, pr), {}
        return self._unhandled(method, path)

    def _list_pulls(
        self, slug: str, path: str, q: dict[str, str]
    ) -> tuple[int, Any, dict[str, str]]:
        prs = list(self.prs.get(slug, []))
        state = q.get("state", "open")
        if state != "all":
            prs = [p for p in prs if p["state"] == state]
        if "base" in q:
            prs = [p for p in prs if p["base"]["ref"] == q["base"]]
        if "head" in q:
            prs = [p for p in prs if p["head"]["ref"] == q["head"].split(":")[-1]]
        key = "updated_at" if q.get("sort") == "updated" else "created_at"
        prs.sort(key=lambda p: (p[key], p["number"]), reverse=q.get("direction", "desc") == "desc")
        return self._page(path, q, [self._view(slug, p) for p in prs])

    def _unhandled(self, method: str, path: str) -> tuple[int, Any, dict[str, str]]:
        self.unhandled.append(f"{method} {path}")
        return 404, {"message": f"fidus-sim does not implement {method} {path}"}, {}


def serve(gh: FakeGitHub, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Start the HTTP server in a daemon thread; returns it (call .shutdown() to stop)."""

    class Handler(BaseHTTPRequestHandler):
        def _do(self) -> None:
            url = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else {}
            status, payload, headers = gh.handle(self.command, url.path, query, body)
            data = b"" if payload is None else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PATCH = do_DELETE = _do

        def log_message(self, format: str, *args: Any) -> None:  # quiet
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    gh.base_url = f"http://{host}:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
