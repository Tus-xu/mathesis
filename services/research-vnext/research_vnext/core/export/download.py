"""RV14 · 导出产物下载令牌（HMAC + exp + jti + security_version）。

任务书 8：下载**每次**重新对象级授权 —— 令牌绑定
`(artifact_id, actor_id, format, exp, jti, sec)`；短 TTL；验证端区分
篡改（403）/ 过期（410）/ 撤权（410，由路由复检产物与 security floor）。
签名密钥与 RV04 资产签名同源（RESEARCH_ASSET_SIGNING_SECRET）。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass


class DownloadTokenInvalid(Exception):
    code = "DOWNLOAD_TOKEN_INVALID"
    http_status = 403


class DownloadTokenExpired(Exception):
    code = "DOWNLOAD_TOKEN_EXPIRED"
    http_status = 410


def default_ttl_seconds() -> int:
    return int(os.getenv("RESEARCH_DOWNLOAD_TTL_SECONDS", "300"))


def _secret() -> bytes:
    s = os.getenv("RESEARCH_ASSET_SIGNING_SECRET") or os.getenv("RESEARCH_GATEWAY_HMAC_SECRET") or ""
    if not s:
        raise DownloadTokenInvalid("下载令牌签名密钥未配置")
    return s.encode()


@dataclass(frozen=True)
class DownloadToken:
    jti: str
    artifact_id: str
    actor_id: str
    fmt: str
    exp: int
    security_version: int
    signature: str


def _sign(msg: str) -> str:
    return hmac.new(_secret(), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def mint_download_token(
    *,
    artifact_id: str,
    actor_id: str,
    fmt: str,
    ttl_seconds: int | None = None,
    security_version: int = 1,
    now: int | None = None,
) -> str:
    now_i = int(now if now is not None else time.time())
    exp = now_i + int(ttl_seconds if ttl_seconds is not None else default_ttl_seconds())
    jti = secrets.token_urlsafe(12)
    msg = f"{jti}.{artifact_id}.{actor_id}.{fmt}.{exp}.{int(security_version)}"
    return f"{msg}.{_sign(msg)}"


def parse_download_token(token: str, *, now: int | None = None) -> DownloadToken:
    parts = (token or "").split(".")
    if len(parts) != 7:
        raise DownloadTokenInvalid("下载令牌格式错误")
    jti, artifact_id, actor_id, fmt, exp_s, sec_s, sig = parts
    try:
        exp = int(exp_s)
        sec = int(sec_s)
    except ValueError as e:
        raise DownloadTokenInvalid("下载令牌字段非法") from e
    msg = f"{jti}.{artifact_id}.{actor_id}.{fmt}.{exp}.{sec}"
    if not hmac.compare_digest(_sign(msg), sig.lower()):
        raise DownloadTokenInvalid("下载令牌签名校验失败")
    now_i = int(now if now is not None else time.time())
    if now_i >= exp:
        raise DownloadTokenExpired("下载令牌已过期")
    return DownloadToken(
        jti=jti, artifact_id=artifact_id, actor_id=actor_id, fmt=fmt,
        exp=exp, security_version=sec, signature=sig,
    )
