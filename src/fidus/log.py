"""Logging with secret redaction, plus the GitHub Actions step-summary writer."""

from __future__ import annotations

import logging
import os
import re
import threading

from rich.logging import RichHandler

_SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"(?i)(authorization:\s*(?:basic|bearer|token)\s+)\S+"),
]

_lock = threading.Lock()
_extra_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Redact this literal value from every log line from now on."""
    if value and len(value) >= 8:
        with _lock:
            _extra_secrets.add(value)


def redact(text: str) -> str:
    with _lock:
        literals = list(_extra_secrets)
    for lit in literals:
        text = text.replace(lit, "***")
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: m.group(1) + "***", text) if pat.groups else pat.sub("***", text)
    return text


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def setup_logging(verbose: bool = False) -> None:
    handler = RichHandler(show_path=False, rich_tracebacks=verbose, markup=False)
    handler.addFilter(_RedactFilter())
    root = logging.getLogger("fidus")
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"fidus.{name}")


def write_step_summary(markdown: str) -> None:
    """Append to $GITHUB_STEP_SUMMARY when running inside GitHub Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(redact(markdown) + "\n")
