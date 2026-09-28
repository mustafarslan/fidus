"""Thread-safe usage accounting and optional dollar estimates."""

from __future__ import annotations

import threading

from fidus.config.models import Pricing
from fidus.llm.types import Usage


class UsageMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total = Usage()

    def add(self, usage: Usage) -> None:
        with self._lock:
            self.total = self.total + usage


def estimate_cost(usage: Usage, pricing: Pricing) -> float | None:
    if pricing.input_per_mtok is None or pricing.output_per_mtok is None:
        return None
    return round(
        usage.input_tokens / 1e6 * pricing.input_per_mtok
        + usage.output_tokens / 1e6 * pricing.output_per_mtok,
        4,
    )
