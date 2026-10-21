"""Typed failures. Every one of these is *fail closed*: nothing is executed."""

from __future__ import annotations


class SandboxError(Exception):
    """Base class. Inherits from Exception only -- never swallowed silently."""


class PayloadError(SandboxError):
    """The request's code/input payload could not be resolved or failed its digest check.

    A digest mismatch is never downgraded to a warning: an unverified payload makes
    ``code_hash``/``input_hash`` meaningless, which would destroy reproducibility.
    """


class IsolationRefusedError(SandboxError):
    """Policy demanded real isolation and the selected backend cannot provide it.

    Raised *before* any code is executed. The result carries
    ``isolation_mode == REFUSED`` and is never reported as a pass.
    """

    def __init__(self, message: str, *, missing_controls: list[str], backend: str) -> None:
        super().__init__(message)
        self.missing_controls = missing_controls
        self.backend = backend


class BackendUnavailableError(SandboxError):
    """The requested backend is not usable in this environment (e.g. no Docker)."""


class RuntimeUnavailableError(SandboxError):
    """A required runtime dependency is missing (Valkey, object store, ...).

    The worker raises this instead of degrading to in-process execution.
    """
