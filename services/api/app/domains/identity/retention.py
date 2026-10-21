"""Identity data retention classifications and scheduled maintenance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class RetentionService:
    security_retention = timedelta(days=180)
    identity_review_retention = timedelta(days=730)

    def __init__(self, now=None):
        self.now = now or (lambda: datetime.now(UTC))

    def classify_audit(self, created_at: datetime, event_type: str) -> str:
        age = self.now() - created_at
        if event_type.startswith(("role_application.", "role_binding.", "break_glass.")):
            return "retain" if age <= self.identity_review_retention else "delete_detail"
        return "hot_or_archive" if age <= self.security_retention else "delete_detail"
