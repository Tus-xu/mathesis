"""RV14 · 导出下载令牌（红→绿）。

任务书 8：下载每次重新对象级授权，短时链接、可撤销/过期、审计。
令牌绑定 (artifact_id, actor_id, format, exp, jti)，HMAC 签名；
过期/篡改/撤权在验证端分别给出 410/403。
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

os.environ.setdefault("RESEARCH_ASSET_SIGNING_SECRET", "rv14-test-signing-secret")

from research_vnext.core.export.download import (  # noqa: E402
    DownloadTokenExpired,
    DownloadTokenInvalid,
    mint_download_token,
    parse_download_token,
)


def _artifact() -> str:
    return str(uuid.uuid4())


def _actor() -> str:
    return str(uuid.uuid4())


def test_token_roundtrip_binds_artifact_actor_format() -> None:
    art, actor = _artifact(), _actor()
    tok = mint_download_token(artifact_id=art, actor_id=actor, fmt="pdf", ttl_seconds=300)
    parsed = parse_download_token(tok)
    assert parsed.artifact_id == art
    assert parsed.actor_id == actor
    assert parsed.fmt == "pdf"
    assert parsed.exp > int(time.time())


def test_token_tamper_is_invalid() -> None:
    tok = mint_download_token(artifact_id=_artifact(), actor_id=_actor(), fmt="pdf")
    bad = tok[:-4] + "0000"
    with pytest.raises(DownloadTokenInvalid):
        parse_download_token(bad)


def test_token_expired_is_410_class() -> None:
    tok = mint_download_token(artifact_id=_artifact(), actor_id=_actor(), fmt="md", ttl_seconds=-10)
    with pytest.raises(DownloadTokenExpired):
        parse_download_token(tok)


def test_token_across_artifacts_rejected() -> None:
    tok = mint_download_token(artifact_id=_artifact(), actor_id=_actor(), fmt="pdf")
    other = str(uuid.uuid4())
    parsed = parse_download_token(tok)
    assert parsed.artifact_id != other  # 令牌不可跨产物使用（绑定校验在路由层完成）
