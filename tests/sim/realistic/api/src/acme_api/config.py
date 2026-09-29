"""Settings loaded from environment variables."""

import os
from dataclasses import dataclass


@dataclass
class Settings:
    database_url: str
    redis_url: str
    token_ttl_seconds: int = 3600

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ["ACME_DATABASE_URL"],
            redis_url=os.environ.get("ACME_REDIS_URL", "redis://localhost:6379/0"),
            token_ttl_seconds=int(os.environ.get("ACME_TOKEN_TTL", "3600")),
        )
