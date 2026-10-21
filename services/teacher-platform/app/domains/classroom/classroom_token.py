# coding: utf-8
"""课堂作用域短时 token（学生 H5 无账号，02-ARCHITECTURE §10 特例）。

HS256 JWT（stdlib 实现，零外部依赖）；claims 绑定 session+participant，仅可调
该课堂的学生端点，有效期 = 会话时长（上限封顶）。越权调教师端点在鉴权层拒绝（403）。
B3 落 `app/core/security.py` 后，教师 JWT 走 B3；本模块只签发/校验课堂 token
（业务规则归 B4），签名算法与 claim 结构对齐 §10。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

MAX_TTL_SECONDS = 4 * 60 * 60  # 课堂会话时长上限封顶


class ClassroomTokenError(Exception):
    """课堂 token 无效/过期/作用域不符（路由层映射 401/403）。"""

    def __init__(self, message: str, code: int = 40101):
        self.code = code
        super().__init__(message)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def mint_classroom_token(
    *,
    secret: str,
    session_id: str,
    participant_id: str,
    anon_id: str,
    student_name: str,
    ttl_seconds: int,
) -> str:
    """签发课堂 token：{scope:'classroom', session_id, participant_id, anon_id, name}。"""
    now = int(time.time())
    header = {'alg': 'HS256', 'typ': 'JWT'}
    payload = {
        'scope': 'classroom',
        'session_id': session_id,
        'participant_id': participant_id,
        'anon_id': anon_id,
        'name': student_name,
        'iat': now,
        'exp': now + max(1, min(int(ttl_seconds), MAX_TTL_SECONDS)),
    }
    signing_input = f'{_b64url(json.dumps(header, separators=(",", ":")).encode())}.' \
                    f'{_b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())}'
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f'{signing_input}.{_b64url(sig)}'


def verify_classroom_token(token: str, *, secret: str, session_id: str | None = None) -> dict[str, Any]:
    """校验课堂 token；session_id 给定时强制匹配（防跨课堂越权）。"""
    try:
        head_b64, payload_b64, sig_b64 = token.split('.')
        signing_input = f'{head_b64}.{payload_b64}'
        expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            raise ClassroomTokenError('课堂 token 签名无效')
        header = json.loads(_b64url_decode(head_b64))
        payload: dict[str, Any] = json.loads(_b64url_decode(payload_b64))
        if header.get('alg') != 'HS256':
            raise ClassroomTokenError('课堂 token 算法不支持')
        if payload.get('scope') != 'classroom':
            raise ClassroomTokenError('非课堂作用域 token')
        if payload.get('exp', 0) < time.time():
            raise ClassroomTokenError('课堂 token 已过期')
        if session_id is not None and payload.get('session_id') != session_id:
            raise ClassroomTokenError('课堂 token 与会话不匹配', code=40301)
        return payload
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ClassroomTokenError('课堂 token 格式无效') from exc
