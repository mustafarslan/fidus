"""`fidus setup-app`: create the Fidus GitHub App with GitHub's App-manifest flow.

1. Serve a local page that POSTs a pre-filled manifest to GitHub (permissions, no webhook).
2. The user clicks "Create GitHub App"; GitHub redirects to our local callback with a code.
3. Exchange the code (POST /app-manifests/{code}/conversions) for the App's credentials.
4. Store them as repo configuration with `gh`, then open the App's install page.
"""

from __future__ import annotations

import html
import json
import os
import secrets
import shutil
import subprocess
import threading
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from fidus.errors import FidusError

PERMISSIONS = {"contents": "write", "pull_requests": "write", "metadata": "read"}


def build_manifest(name: str, homepage: str, redirect_url: str) -> dict[str, Any]:
    return {
        "name": name,
        "url": homepage,
        "description": "Fidus keeps our documentation in sync with the code via nightly pull requests.",
        "public": False,
        # Fidus runs on a schedule and needs no webhook; GitHub still requires a URL.
        "hook_attributes": {"url": homepage, "active": False},
        "redirect_url": redirect_url,
        "default_permissions": PERMISSIONS,
        "default_events": [],
    }


def new_app_url(github_url: str, org: str | None) -> str:
    base = github_url.rstrip("/")
    return f"{base}/organizations/{org}/settings/apps/new" if org else f"{base}/settings/apps/new"


def form_page(action: str, manifest: dict[str, Any], state: str) -> str:
    """Auto-submitting page: browsers can only POST forms, and GitHub wants a POSTed manifest."""
    return f"""<!doctype html><meta charset="utf-8"><title>Create the Fidus GitHub App</title>
<body style="font-family:system-ui;max-width:40em;margin:4em auto">
<h1>Creating the Fidus GitHub App…</h1>
<p>You'll be taken to GitHub. Review the settings and click <b>Create GitHub App</b>.</p>
<form id="f" method="post" action="{html.escape(action)}?state={state}">
<input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest))}">
<button type="submit">Continue to GitHub</button></form>
<script>document.getElementById("f").submit()</script>"""


@dataclass
class ManifestServer:
    """Local server for the form page and GitHub's redirect back."""

    action: str
    manifest_for: Any  # callable(redirect_url) -> manifest
    state: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    code: str | None = None
    error: str | None = None
    _done: threading.Event = field(default_factory=threading.Event)

    def start(self) -> str:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urlparse(self.path)
                q = {k: v[-1] for k, v in parse_qs(url.query).items()}
                if url.path == "/":
                    body = form_page(
                        outer.action, outer.manifest_for(outer.redirect_url), outer.state
                    )
                elif url.path == "/callback":
                    if q.get("state") != outer.state:
                        outer.error = "state mismatch (possible CSRF); start again"
                        body = (
                            "<p>State mismatch. Please run <code>fidus setup-app</code> again.</p>"
                        )
                    elif "code" not in q:
                        outer.error = "GitHub did not return a code"
                        body = "<p>No code received.</p>"
                    else:
                        outer.code = q["code"]
                        body = (
                            "<h1>Done ✔</h1><p>Return to your terminal; you can close this tab.</p>"
                        )
                    outer._done.set()
                else:
                    self.send_error(404)
                    return
                data = body.encode()
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self.redirect_url = f"{self.url}/callback"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self.url

    def wait(self, timeout: float) -> str:
        try:
            if not self._done.wait(timeout):
                raise FidusError("timed out waiting for GitHub; run `fidus setup-app` again")
            if self.error or not self.code:
                raise FidusError(self.error or "no code received from GitHub")
            return self.code
        finally:
            self._server.shutdown()


def exchange_code(
    code: str, api_url: str, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    with httpx.Client(transport=transport, timeout=30) as client:
        resp = client.post(
            f"{api_url.rstrip('/')}/app-manifests/{code}/conversions",
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
    if resp.status_code >= 400:
        raise FidusError(
            f"GitHub rejected the manifest code ({resp.status_code}): {resp.text[:200]}"
        )
    data: dict[str, Any] = resp.json()
    return data


def store_credentials(
    creds: dict[str, Any], docs_repo: str | None, *, use_gh: bool, out_dir: Path
) -> list[str]:
    """Save the client id + private key where the workflow expects them. Returns messages."""
    client_id, pem, slug = str(creds["client_id"]), str(creds["pem"]), str(creds["slug"])
    if use_gh and docs_repo and shutil.which("gh"):
        subprocess.run(
            [
                "gh",
                "variable",
                "set",
                "FIDUS_APP_CLIENT_ID",
                "--repo",
                docs_repo,
                "--body",
                client_id,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["gh", "secret", "set", "FIDUS_APP_PRIVATE_KEY", "--repo", docs_repo],
            input=pem,
            check=True,
            capture_output=True,
            text=True,
        )
        return [
            f"saved variable FIDUS_APP_CLIENT_ID and secret FIDUS_APP_PRIVATE_KEY on {docs_repo}"
        ]
    key = out_dir / f"{slug}.private-key.pem"
    key.write_text(pem, encoding="utf-8")
    os.chmod(key, 0o600)
    return [
        f"private key written to {key} (keep it secret; delete it once stored)",
        f"add to the docs repo: variable FIDUS_APP_CLIENT_ID={client_id}, "
        f"secret FIDUS_APP_PRIVATE_KEY=<contents of {key.name}>",
    ]


def run_setup_app(
    *,
    org: str | None,
    docs_repo: str | None,
    name: str | None,
    github_url: str,
    api_url: str,
    open_browser: bool,
    use_gh: bool,
    out_dir: Path,
    echo: Any,
    timeout: float = 900,
) -> dict[str, Any]:
    owner = org or (docs_repo.split("/")[0] if docs_repo else "my")
    app_name = name or f"{owner}-fidus"
    homepage = (
        f"{github_url.rstrip('/')}/{docs_repo}"
        if docs_repo
        else "https://github.com/mustafarslan/fidus"
    )
    server = ManifestServer(
        new_app_url(github_url, org), lambda redirect: build_manifest(app_name, homepage, redirect)
    )
    url = server.start()
    echo(f"Open {url} to create the GitHub App '{app_name}' (a browser window should open).")
    if open_browser:
        webbrowser.open(url)
    code = server.wait(timeout)
    creds = exchange_code(code, api_url)
    echo(f"Created GitHub App '{creds['slug']}' (id {creds['id']}): {creds.get('html_url', '')}")
    for msg in store_credentials(creds, docs_repo, use_gh=use_gh, out_dir=out_dir):
        echo(msg)
    install = f"{creds.get('html_url', '').rstrip('/')}/installations/new"
    echo(f"Next: install the App on the docs repo and every source repo: {install}")
    if open_browser and creds.get("html_url"):
        webbrowser.open(install)
    return creds
