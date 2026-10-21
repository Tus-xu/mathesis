"""API 依赖：session + 鉴权依赖（8200 自持）。"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.service_auth import ServiceUser, authenticate


def require_auth(request: Request, session: Annotated[AsyncSession, Depends(get_session)]) -> AsyncSession:
    user: ServiceUser = authenticate(request)
    request.state.user = user
    request.state.principal = user.principal
    return session
