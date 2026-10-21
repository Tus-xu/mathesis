"""service_auth 单元测试：JWT / internal token / 缺失 / 过期。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from starlette.requests import Request

from app.config import get_settings
from app.errors import ApiError, ErrorCode
from app.service_auth import authenticate


def _req(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "path": "/api/ppt-studio-v2/v1/jobs",
            "method": "GET",
            "query_string": b"",
            "headers": headers,
            "server": ("127.0.0.1", 8200),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )


def test_missing_header_rejected() -> None:
    with pytest.raises(ApiError) as ei:
        authenticate(_req([]))
    assert ei.value.code == ErrorCode.UNAUTHORIZED


def test_internal_token_ok() -> None:
    settings = get_settings()
    assert settings.internal_token == "test-internal-token"
    user = authenticate(_req([(b"authorization", f"Bearer {settings.internal_token}".encode())]))
    assert user.source == "internal-token"
    assert user.principal == "internal:ppt-studio"


def test_valid_jwt_ok() -> None:
    settings = get_settings()
    token = jwt.encode(
        {"sub": "teacher-1", "name": "t", "exp": datetime.now(UTC) + timedelta(hours=1)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    user = authenticate(_req([(b"authorization", f"Bearer {token}".encode())]))
    assert user.source == "teacher-jwt"
    assert user.principal == "teacher-1"


def test_expired_jwt_rejected() -> None:
    settings = get_settings()
    token = jwt.encode(
        {"sub": "teacher-1", "exp": datetime.now(UTC) - timedelta(seconds=10)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(ApiError) as ei:
        authenticate(_req([(b"authorization", f"Bearer {token}".encode())]))
    assert ei.value.error_key == "expired"


def test_wrong_signature_rejected() -> None:
    token = jwt.encode({"sub": "x"}, "wrong-secret", algorithm="HS256")
    with pytest.raises(ApiError):
        authenticate(_req([(b"authorization", f"Bearer {token}".encode())]))
