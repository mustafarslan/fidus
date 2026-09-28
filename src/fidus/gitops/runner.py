"""Run git as a subprocess. Tokens travel via an http.extraheader, never in .git/config."""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

from fidus.errors import GitError
from fidus.log import get_logger, redact, register_secret

log = get_logger("git")


def auth_args(token: str | None, host: str = "github.com") -> list[str]:
    if not token:
        return []
    register_secret(token)
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    register_secret(basic)
    key = f"http.https://{host}/.extraheader"
    # An empty value resets the list first: actions/checkout persists its own header, and two
    # Authorization headers make GitHub reject the request.
    return ["-c", f"{key}=", "-c", f"{key}=AUTHORIZATION: basic {basic}"]


def git(
    args: list[str],
    cwd: Path,
    *,
    token: str | None = None,
    host: str = "github.com",
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", *auth_args(token, host), *args]
    full_env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        **(env or {}),
    }
    log.debug("git %s (cwd=%s)", redact(" ".join(args)), cwd)
    proc = subprocess.run(cmd, cwd=cwd, env=full_env, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(
            f"git {redact(' '.join(args))} failed ({proc.returncode}): {redact(proc.stderr.strip())}"
        )
    return proc
