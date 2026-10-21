"""Typed values shared by identity services and gateway dependencies."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CurrentIdentity(Mapping[str, Any]):
    """An identity already validated against current database state."""

    user_id: uuid.UUID
    session_id: uuid.UUID | None
    active_role: str
    security_version: int
    legacy_token: bool = False

    def _legacy_view(self) -> dict[str, Any]:
        return {
            "sub": str(self.user_id),
            "session_id": str(self.session_id) if self.session_id else None,
            "active_role": self.active_role,
            "roles": [self.active_role],
            "verified": True,
            "security_version": self.security_version,
            "legacy_token": self.legacy_token,
        }

    def __getitem__(self, key: str) -> Any:
        return self._legacy_view()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._legacy_view())

    def __len__(self) -> int:
        return len(self._legacy_view())
