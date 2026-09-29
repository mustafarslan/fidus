from __future__ import annotations

import html
import json
import re
import stat
import threading
import urllib.request
from pathlib import Path
from typing import Any

import httpx
import pytest

from fidus.cli import setup_app as sa
from fidus.errors import FidusError


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.read().decode()


def test_manifest_contents() -> None:
    m = sa.build_manifest(
        "acme-fidus", "https://github.com/acme/docs", "http://127.0.0.1:1/callback"
    )
    assert m["default_permissions"] == {
        "contents": "write",
        "pull_requests": "write",
        "metadata": "read",
    }
    assert m["hook_attributes"] == {"url": "https://github.com/acme/docs", "active": False}
    assert m["public"] is False and m["default_events"] == []
    assert sa.new_app_url("https://github.com", None) == "https://github.com/settings/apps/new"
    assert (
        sa.new_app_url("https://github.com", "acme")
        == "https://github.com/organizations/acme/settings/apps/new"
    )


def test_manifest_flow_end_to_end() -> None:
    server = sa.ManifestServer(
        "https://github.com/settings/apps/new",
        lambda redirect: sa.build_manifest("acme-fidus", "https://x", redirect),
    )
    base = server.start()
    page = _get(base + "/")
    state = re.search(r"state=([\w-]+)", page).group(1)  # type: ignore[union-attr]
    manifest = json.loads(
        html.unescape(re.search(r'name="manifest" value="([^"]+)"', page).group(1))
    )  # type: ignore[union-attr]
    assert manifest["redirect_url"] == f"{base}/callback"
    got: dict[str, str] = {}
    t = threading.Thread(target=lambda: got.update(code=server.wait(5)))
    t.start()
    assert "Done" in _get(f"{base}/callback?code=abc123&state={state}")
    t.join()
    assert got["code"] == "abc123"


def test_callback_rejects_wrong_state() -> None:
    server = sa.ManifestServer("https://github.com/settings/apps/new", lambda r: {})
    base = server.start()
    _get(f"{base}/callback?code=abc&state=forged")
    with pytest.raises(FidusError, match="state mismatch"):
        server.wait(5)


def test_exchange_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/app-manifests/abc/conversions"
        assert "authorization" not in request.headers
        return httpx.Response(
            201,
            json={
                "id": 7,
                "slug": "acme-fidus",
                "client_id": "Iv1.x",
                "pem": "KEY",
                "html_url": "https://github.com/apps/acme-fidus",
            },
        )

    creds = sa.exchange_code(
        "abc", "https://api.github.com", transport=httpx.MockTransport(handler)
    )
    assert creds["client_id"] == "Iv1.x"
    bad = httpx.MockTransport(lambda r: httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(FidusError, match="404"):
        sa.exchange_code("stale", "https://api.github.com", transport=bad)


def test_store_credentials_with_gh_and_file_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    creds = {
        "client_id": "Iv1.x",
        "pem": "-----BEGIN RSA PRIVATE KEY-----\n...",
        "slug": "acme-fidus",
    }
    calls: list[tuple[list[str], Any]] = []
    monkeypatch.setattr(sa.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        sa.subprocess, "run", lambda args, **kw: calls.append((args, kw.get("input")))
    )
    msgs = sa.store_credentials(creds, "acme/docs", use_gh=True, out_dir=tmp_path)
    assert calls[0][0][:3] == ["gh", "variable", "set"] and "Iv1.x" in calls[0][0]
    assert (
        calls[1][0][:4] == ["gh", "secret", "set", "FIDUS_APP_PRIVATE_KEY"]
        and calls[1][1] == creds["pem"]
    )
    assert "acme/docs" in msgs[0] and not list(tmp_path.iterdir())  # nothing written to disk

    msgs = sa.store_credentials(creds, None, use_gh=True, out_dir=tmp_path)  # no docs repo -> file
    key = tmp_path / "acme-fidus.private-key.pem"
    assert key.read_text() == creds["pem"] and stat.S_IMODE(key.stat().st_mode) == 0o600
    assert "FIDUS_APP_CLIENT_ID=Iv1.x" in msgs[1]
