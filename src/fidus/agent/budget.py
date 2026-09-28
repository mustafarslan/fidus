"""Turn and token budgets. Episode budgets draw from a shared run budget."""

from __future__ import annotations

import threading

from fidus.errors import BudgetExceeded
from fidus.llm.types import Usage


class RunBudget:
    """Shared across parallel episodes."""

    def __init__(self, max_total_tokens: int) -> None:
        self.max_total_tokens = max_total_tokens
        self.used = 0
        self._lock = threading.Lock()

    def remaining(self) -> int:
        with self._lock:
            return self.max_total_tokens - self.used

    def charge(self, tokens: int) -> None:
        with self._lock:
            self.used += tokens

    @property
    def exhausted(self) -> bool:
        return self.remaining() <= 0


class Budget:
    def __init__(
        self,
        *,
        max_turns: int,
        max_input_tokens: int,
        max_output_tokens: int,
        run: RunBudget | None = None,
    ) -> None:
        self.max_turns = max_turns
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.run = run
        self.turns = 0
        self.usage = Usage()

    def check_before_call(self, estimated_input: int) -> None:
        if self.turns >= self.max_turns:
            raise BudgetExceeded(f"turn limit reached ({self.max_turns})")
        if self.usage.input_tokens + estimated_input > self.max_input_tokens:
            raise BudgetExceeded(f"episode input-token budget reached ({self.max_input_tokens})")
        if self.usage.output_tokens >= self.max_output_tokens:
            raise BudgetExceeded(f"episode output-token budget reached ({self.max_output_tokens})")
        if self.run is not None and self.run.remaining() < estimated_input:
            raise BudgetExceeded("run token budget exhausted")

    def record(self, usage: Usage) -> None:
        self.turns += 1
        self.usage = self.usage + usage
        if self.run is not None:
            self.run.charge(usage.input_tokens + usage.output_tokens)

    def near_limit(self, ratio: float = 0.8) -> bool:
        return (
            self.turns >= self.max_turns * ratio
            or self.usage.input_tokens >= self.max_input_tokens * ratio
            or self.usage.output_tokens >= self.max_output_tokens * ratio
        )


def estimate_tokens(chars: int) -> int:
    return chars // 4 + 1
