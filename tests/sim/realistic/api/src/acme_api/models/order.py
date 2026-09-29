"""The Order entity and its status lifecycle: pending -> paid -> shipped."""

from dataclasses import dataclass, field
from datetime import datetime

STATUSES = ("pending", "paid", "shipped")


@dataclass
class Order:
    id: int
    user_id: int
    total_cents: int
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.utcnow)

    def advance(self) -> None:
        """Move to the next status; shipped is final."""
        i = STATUSES.index(self.status)
        if i < len(STATUSES) - 1:
            self.status = STATUSES[i + 1]
