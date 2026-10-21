"""Offline, default-disabled administrator recovery credential issuer."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import async_session_factory
from app.models.identity import IdentityAuditLog, UserCredential
from app.models.user import User


class BreakGlassError(Exception):
    pass


async def issue_break_glass(
    db: AsyncSession,
    *,
    enabled: bool,
    trusted_environment: bool,
    deployment_secret: str,
    supplied_secret: str,
    phone: str,
    work_order: str,
    operator_one: str,
    operator_two: str,
) -> str:
    if not enabled:
        raise BreakGlassError("break-glass is disabled")
    if not trusted_environment:
        raise BreakGlassError("untrusted environment")
    if not deployment_secret or not hmac.compare_digest(deployment_secret, supplied_secret):
        raise BreakGlassError("secret verification failed")
    if not work_order or not operator_one or not operator_two or operator_one == operator_two:
        raise BreakGlassError("work order and two distinct operators are required")
    user = await db.scalar(
        select(User).where(
            User.phone == phone,
            User.phone_verified_at.is_not(None),
            User.deleted_at.is_(None),
        )
    )
    if user is None:
        raise BreakGlassError("verified target account not found")
    token = secrets.token_urlsafe(32)
    token_hash = hmac.new(deployment_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    now = datetime.now(UTC)
    await db.execute(
        insert(UserCredential)
        .values(
            user_id=user.id,
            credential_type="break_glass",
            secret_hash=token_hash,
            password_changed_at=now,
            failed_attempts=0,
            locked_until=now + timedelta(minutes=15),
        )
        .on_conflict_do_update(
            index_elements=[UserCredential.user_id, UserCredential.credential_type],
            set_={
                "secret_hash": token_hash,
                "password_changed_at": now,
                "failed_attempts": 0,
                "locked_until": now + timedelta(minutes=15),
            },
        )
    )
    db.add(
        IdentityAuditLog(
            event_type="break_glass.issued",
            actor_user_id=None,
            subject_user_id=user.id,
            result="success",
            details={
                "work_order": work_order,
                "operators": [operator_one, operator_two],
                "expires_at": (now + timedelta(minutes=15)).isoformat(),
            },
        )
    )
    await db.flush()
    return token


async def _run(args: argparse.Namespace) -> str:
    secret = sys.stdin.readline().rstrip("\r\n")
    async with async_session_factory() as db:
        token = await issue_break_glass(
            db,
            enabled=os.getenv("AUTH_BREAK_GLASS_ENABLED", "false").lower() == "true",
            trusted_environment=os.getenv("AUTH_TRUSTED_OPERATIONS_ENV", "false").lower() == "true",
            deployment_secret=os.getenv("AUTH_BREAK_GLASS_SECRET", ""),
            supplied_secret=secret,
            phone=args.phone,
            work_order=args.work_order,
            operator_one=args.operator_one,
            operator_two=args.operator_two,
        )
        await db.commit()
        return token


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue a 15-minute one-time recovery token")
    parser.add_argument("--phone", required=True)
    parser.add_argument("--work-order", required=True)
    parser.add_argument("--operator-one", required=True)
    parser.add_argument("--operator-two", required=True)
    args = parser.parse_args()
    try:
        print(asyncio.run(_run(args)))
    except BreakGlassError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
