"""Exponential backoff for retryable provider errors."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from fidus.errors import ProviderError
from fidus.log import get_logger

T = TypeVar("T")
log = get_logger("llm.retry")


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ProviderError as e:
            if not e.retryable or attempt == attempts:
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * (0.5 + random.random() / 2)
            log.warning(
                "provider error (%s); retry %d/%d in %.1fs", e, attempt, attempts - 1, delay
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def is_retryable_status(status: int | None) -> bool:
    return status is not None and (status == 408 or status == 429 or status >= 500)
