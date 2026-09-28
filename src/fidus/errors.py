"""Exception hierarchy. Every error Fidus raises on purpose derives from FidusError."""

from __future__ import annotations


class FidusError(Exception):
    """Base class; `exit_code` is what the CLI exits with."""

    exit_code = 1


class ConfigError(FidusError):
    """fidus.yaml or fidus.outline.yaml is missing or invalid."""

    exit_code = 2


class NotBootstrappedError(FidusError):
    """No state exists yet; the user must run `fidus bootstrap` (or pass --since)."""


class BudgetExceeded(FidusError):
    """A token or turn budget was exhausted."""


class GuardViolation(FidusError):
    """A tool call tried to touch something outside its sandbox."""


class GitHubError(FidusError):
    """GitHub API call failed."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitError(FidusError):
    """A git subprocess failed."""


class ProviderError(FidusError):
    """The LLM provider failed. `retryable` marks transient failures (429/5xx/overloaded)."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
