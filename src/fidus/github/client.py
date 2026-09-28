"""Minimal GitHub REST client: auth, pagination, rate limits, one retry on 5xx."""

from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from fidus import __version__
from fidus.errors import GitHubError
from fidus.log import get_logger, register_secret

log = get_logger("github")
LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        api_url: str = "https://api.github.com",
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_rate_limit_wait: float = 900,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"fidus/{__version__}",
        }
        if token:
            register_secret(token)
            headers["Authorization"] = f"Bearer {token}"
        self.token = token
        self._http = httpx.Client(
            base_url=api_url.rstrip("/"), headers=headers, timeout=30.0, transport=transport
        )
        self._sleep = sleep
        self._max_wait = max_rate_limit_wait

    def close(self) -> None:
        self._http.close()

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(3):
            try:
                resp = self._http.request(method, path, **kwargs)
            except httpx.TransportError as e:
                if attempt == 2:
                    raise GitHubError(f"{method} {path}: {e}") from e
                self._sleep(2)
                continue
            if resp.status_code in (403, 429) and self._rate_limited(resp):
                wait = self._wait_seconds(resp)
                if wait > self._max_wait:
                    raise GitHubError(
                        f"GitHub rate limit exceeded; resets in {wait:.0f}s", resp.status_code
                    )
                log.warning("GitHub rate limit hit; sleeping %.0fs", wait)
                self._sleep(wait)
                continue
            if resp.status_code in (502, 503, 504) and attempt < 2:
                self._sleep(2)
                continue
            if resp.status_code >= 400:
                msg = resp.text[:500]
                with contextlib.suppress(ValueError):
                    msg = resp.json().get("message", msg)
                raise GitHubError(f"{method} {path} -> {resp.status_code}: {msg}", resp.status_code)
            return resp
        raise GitHubError(f"{method} {path}: retries exhausted")

    @staticmethod
    def _rate_limited(resp: httpx.Response) -> bool:
        return resp.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in resp.headers

    @staticmethod
    def _wait_seconds(resp: httpx.Response) -> float:
        if "retry-after" in resp.headers:
            try:
                return float(resp.headers["retry-after"])
            except ValueError:
                return 60.0
        reset = resp.headers.get("x-ratelimit-reset")
        if reset:
            return max(1.0, float(reset) - time.time() + 1)
        return 60.0

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None).json()

    def post(self, path: str, json: Any) -> Any:
        return self.request("POST", path, json=json).json()

    def patch(self, path: str, json: Any) -> Any:
        return self.request("PATCH", path, json=json).json()

    def delete(self, path: str) -> None:
        self.request("DELETE", path)

    def paginate(self, path: str, **params: Any) -> Iterator[Any]:
        params.setdefault("per_page", 100)
        url: str | None = path
        first = True
        while url:
            resp = self.request("GET", url, params=params if first else None)
            first = False
            data = resp.json()
            yield from (data if isinstance(data, list) else [data])
            m = LINK_NEXT.search(resp.headers.get("link", ""))
            url = m.group(1) if m else None
