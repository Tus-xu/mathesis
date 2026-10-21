"""RV04 · 签名交付令牌（HMAC + exp + jti）。

签名 URL 只经 Gateway 暴露；**不**返回 MinIO presigned URL。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
import uuid
from dataclasses import dataclass


class SignedUrlInvalid(Exception):
    code = "SIGNED_URL_INVALID"
    http_status = 403


class SignedUrlExpired(Exception):
    code = "SIGNED_URL_EXPIRED"
    http_status = 410


class SignedUrlRevoked(Exception):
    code = "SIGNED_URL_REVOKED"
    http_status = 410


@dataclass(frozen=True)
class SignedToken:
    jti: str
    asset_id: str
    version: int
    exp: int
    signature: str

    def to_token(self) -> str:
        return f"{self.jti}.{self.asset_id}.{self.version}.{self.exp}.{self.signature}"


def _secret() -> bytes:
    s = os.getenv("RESEARCH_ASSET_SIGNING_SECRET") or os.getenv("RESEARCH_GATEWAY_HMAC_SECRET") or ""
    if not s:
        raise SignedUrlInvalid("资产签名密钥未配置")
    return s.encode()


def default_ttl_seconds() -> int:
    return int(os.getenv("RESEARCH_SIGNED_URL_TTL_SECONDS", "300"))


def mint_token(
    *,
    asset_id: uuid.UUID | str,
    version: int,
    ttl_seconds: int | None = None,
    now: int | None = None,
) -> SignedToken:
    now_i = int(now if now is not None else time.time())
    exp = now_i + int(ttl_seconds if ttl_seconds is not None else default_ttl_seconds())
    jti = secrets.token_urlsafe(12)
    return _sign(jti=str(jti), asset_id=str(asset_id), version=int(version), exp=exp)


def _sign(*, jti: str, asset_id: str, version: int, exp: int) -> SignedToken:
    msg = f"{jti}.{asset_id}.{version}.{exp}".encode()
    sig = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()
    return SignedToken(jti=jti, asset_id=asset_id, version=version, exp=exp, signature=sig)


def parse_and_verify(token: str, *, now: int | None = None) -> SignedToken:
    parts = (token or "").split(".")
    if len(parts) != 5:
        raise SignedUrlInvalid("签名令牌格式错误")
    jti, asset_id, ver_s, exp_s, sig = parts
    try:
        version = int(ver_s)
        exp = int(exp_s)
    except ValueError as e:
        raise SignedUrlInvalid("签名令牌字段非法") from e
    expected = _sign(jti=jti, asset_id=asset_id, version=version, exp=exp)
    if not hmac.compare_digest(expected.signature, sig.lower()):
        raise SignedUrlInvalid("签名校验失败")
    now_i = int(now if now is not None else time.time())
    if now_i >= exp:
        raise SignedUrlExpired("签名 URL 已过期")
    return expected
