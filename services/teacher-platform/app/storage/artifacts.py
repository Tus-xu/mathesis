"""产物投递服务（T24）：注册 / 短时票据 / 下载即鉴权 / 审计。

关键决定：
- 浏览器永远拿不到 bucket/key（平台契约 ADR-BROWSER-BOUNDARY），票据 URL 只承载不透明 token；
- 票据签名绑定 (jti, artifact, actor, exp, acl_epoch)，改一位即签名不符 → 403；
- 过期/单次使用/撤权都在 redeem 时**重新查权**并写审计（allow 与 deny 都留痕）。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.storage.models import (
    TeacherArtifact,
    TeacherArtifactDownloadAudit,
    TeacherArtifactTicket,
)

_EXT_BY_KIND = {"pptx": "pptx", "pdf": "pdf", "docx": "docx", "markdown": "md", "qa_json": "json"}


class BytesStore(Protocol):
    def put_bytes(self, bucket: str, object_name: str, data: bytes, content_type: str = ...) -> None: ...
    def get_bytes(self, bucket: str, object_name: str) -> bytes: ...


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_disposition(filename: str) -> str:
    """RFC 5987：中文文件名不能直接进 latin-1 响应头，必须给 ASCII 回退 + filename* 编码形式。"""
    from urllib.parse import quote

    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename).strip("_") or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


# 文件系统非法字符 + 控制字符（Windows/macOS/Linux 并集）
_FILENAME_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def safe_download_name(title: str, *, fallback: str, max_len: int = 60) -> str:
    """教师可读的下载名片段：去文件系统非法字符、折叠空白、限长；空 → fallback。

    中文保留（`content_disposition` 已按 RFC 5987 编码，各 OS 均正确还原）。
    """
    s = _FILENAME_ILLEGAL.sub("", str(title or ""))
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > max_len:
        s = s[:max_len].strip()
    return s or fallback


def _sign(secret: str, claims: str) -> str:
    return hmac.new(secret.encode("utf-8"), claims.encode("utf-8"), hashlib.sha256).hexdigest()


def _epoch(dt: datetime) -> int:
    """UTC 秒级 epoch。SQLite 会把 tz-aware 值读成 naive —— 不归一化会让签名在往返后失效。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.replace(microsecond=0).timestamp())


def _claims(artifact_id: uuid.UUID, actor_id: str, expires_at: datetime, acl_epoch: int) -> str:
    return f"{artifact_id}|{actor_id}|{_epoch(expires_at)}|{acl_epoch}"


async def register_artifact(
    session: AsyncSession,
    *,
    store: BytesStore,
    spec_kind: str,
    spec_id: str,
    spec_revision: int,
    kind: str,
    media_type: str,
    data: bytes,
    filename: str,
    owner_id: uuid.UUID | None,
    created_by_run_id: str,
    source_lineage: dict[str, Any],
    qa_status: dict[str, Any] | None = None,
    visibility: str = "PRIVATE",
    subject_ids: list[str] | None = None,
    retention_policy: str = "ACADEMIC_CYCLE",
    retention_ttl_days: int | None = None,
    bucket: str = "teacher-exports",
) -> TeacherArtifact:
    """字节先落对象存储、再登记事实；登记的 hash 与落盘字节同源，杜绝"登记的和发出去的不是一份"。"""
    if not data:
        raise ApiError(ErrorCode.INTERNAL, "拒绝登记 0 字节产物")
    digest = sha256_of(data)
    artifact_id = uuid.uuid4()
    ext = _EXT_BY_KIND.get(kind, "bin")
    object_key = f"t24/{spec_kind}/{spec_id}/r{spec_revision}/{artifact_id}.{ext}"
    store.put_bytes(bucket, object_key, data, media_type)

    artifact = TeacherArtifact(
        id=artifact_id,
        owner_id=owner_id,
        spec_kind=spec_kind,
        spec_id=spec_id,
        spec_revision=spec_revision,
        kind=kind,
        media_type=media_type,
        bucket=bucket,
        object_key=object_key,
        content_sha256=digest,
        size_bytes=len(data),
        version=1,
        created_by_run_id=created_by_run_id,
        source_lineage=source_lineage,
        acl={
            "visibility": visibility,
            "subject_ids": list(subject_ids or []),
            "tenant_id": None,
            "filename": filename,
        },
        retention={"policy": retention_policy, "ttl_days": retention_ttl_days, "owner_delete_allowed": False},
        qa_status=qa_status,
        acl_epoch=0,
    )
    session.add(artifact)
    await session.flush()
    return artifact


async def get_artifact(session: AsyncSession, artifact_id: str) -> TeacherArtifact:
    try:
        uid = uuid.UUID(artifact_id)
    except (ValueError, AttributeError) as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"产物 {artifact_id} 不存在") from exc
    row = (
        await session.execute(select(TeacherArtifact).where(TeacherArtifact.id == uid))
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"产物 {artifact_id} 不存在")
    return row


def artifact_view(row: TeacherArtifact) -> dict[str, Any]:
    return {
        "artifact_id": str(row.id),
        "version": row.version,
        "kind": row.kind,
        "media_type": row.media_type,
        "size_bytes": row.size_bytes,
        "content_sha256": row.content_sha256,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by_run_id": row.created_by_run_id,
        "spec_kind": row.spec_kind,
        "spec_id": row.spec_id,
        "spec_revision": row.spec_revision,
        "source_lineage": row.source_lineage,
        "acl": {k: v for k, v in (row.acl or {}).items() if k != "filename"},
        "file_name": (row.acl or {}).get("filename"),
        "retention": row.retention,
        "qa_status": row.qa_status,
        "download": {"ticket_required": True},
    }


def acl_allows(row: TeacherArtifact, actor_id: str | None, roles: list[str] | None = None) -> tuple[bool, str]:
    """下载时重新查权（不看票据里的旧结论）。"""
    acl = row.acl or {}
    visibility = acl.get("visibility", "PRIVATE")
    subjects = set(acl.get("subject_ids") or [])
    if actor_id is None:
        return False, "anonymous"
    if row.owner_id is not None and str(row.owner_id) == actor_id:
        return True, "owner"
    if visibility == "PUBLIC_INTERNAL":
        return True, "public_internal"
    if actor_id in subjects:
        return True, "subject"
    if roles and "admin" in roles:
        return True, "admin"
    return False, "acl_denied"


async def spec_access(
    session: AsyncSession,
    *,
    spec_id: str,
    actor_id: str,
    roles: list[str] | None = None,
) -> tuple[bool, str]:
    """deck / 教案 spec 的归属裁定（T25 P0-5 读越权、P0-6 回滚写越权）。

    为什么不直接查 spec 表：`ppt_v2_deck_specs` 只有 `school_id`(=class_id)，**没有 owner 列**，
    归属必须反查。证据链任一成立即放行，**两者皆无即拒**（不猜测、不放行）：
      1. `tp_spec_projections` → `tp_sessions.teacher_id` == actor（正常流程产出的 spec 都有此行）
      2. `teacher_artifacts(spec_id=…)` 的 ACL 允许（导出过的 spec）
      3. 角色含 admin

    下载闸门（`acl_allows`）与读写闸门共用同一 ACL 语义，避免"下载拒绝但读取放行"的不一致。
    """
    try:
        uid = str(uuid.UUID(str(spec_id)))
    except (ValueError, AttributeError, TypeError):
        return False, "spec_id_invalid"
    if roles and "admin" in roles:
        return True, "admin"

    owner = (
        await session.execute(
            text(
                "select cast(s.teacher_id as text) from tp_spec_projections p "
                "join tp_sessions s on s.id = p.session_id "
                "where cast(p.spec_target_id as text) = cast(:sid as text) limit 1"
            ),
            {"sid": uid},
        )
    ).first()
    if owner is not None:
        return (True, "session_owner") if str(owner[0]) == actor_id else (False, "other_teacher")

    # 注意：teacher_artifacts.spec_id 是 TEXT 列（非 UUID），必须用字符串比较，
    # 否则 asyncpg 报 `operator does not exist: text = uuid`（实测 500 而非 403）。
    row = (
        await session.execute(
            select(TeacherArtifact).where(TeacherArtifact.spec_id == uid).limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        return acl_allows(row, actor_id, roles)

    return False, "no_ownership_record"


async def issue_ticket(
    session: AsyncSession,
    row: TeacherArtifact,
    *,
    actor_id: str,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    ttl = ttl_seconds or settings.artifact_ticket_ttl_seconds
    expires_at = (datetime.now(UTC) + timedelta(seconds=ttl)).replace(microsecond=0)
    jti = secrets.token_urlsafe(18)
    sig = _sign(settings.jwt_secret, _claims(row.id, actor_id, expires_at, row.acl_epoch))
    ticket = f"{jti}.{sig}"
    session.add(
        TeacherArtifactTicket(
            ticket=ticket,
            artifact_id=row.id,
            actor_id=actor_id,
            expires_at=expires_at,
            acl_epoch=row.acl_epoch,
        )
    )
    await session.flush()
    return {
        "ticket": ticket,
        "url": f"/api/teacher-v3/artifacts/downloads/{ticket}",
        "expires_at": expires_at.isoformat(),
        "ttl_seconds": ttl,
        "single_use": True,
    }


async def _audit(
    session: AsyncSession,
    *,
    ticket: str | None,
    artifact_id: uuid.UUID | None,
    actor_id: str | None,
    decision: str,
    reason: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    session.add(
        TeacherArtifactDownloadAudit(
            ticket=ticket,
            artifact_id=artifact_id,
            actor_id=actor_id,
            decision=decision,
            reason=reason,
            ip=ip,
            user_agent=user_agent,
        )
    )
    await session.flush()


async def _deny(
    session: AsyncSession,
    *,
    ticket: str | None,
    artifact_id: uuid.UUID | None,
    actor_id: str | None,
    reason: str,
    status: int,
    code: ErrorCode,
    message: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """拒绝 = 写审计 + 提交 + 抛错。

    必须先提交：调用方的 session 在异常路径会被回滚，若审计跟着回滚，
    "谁在什么时候被拒绝过" 就永远查不到 —— 而这正是撤权/越权排查的唯一依据。
    """
    await _audit(
        session,
        ticket=ticket,
        artifact_id=artifact_id,
        actor_id=actor_id,
        decision="deny",
        reason=reason,
        ip=ip,
        user_agent=user_agent,
    )
    await session.commit()
    raise ApiError(code, message, status_code=status)


async def redeem_ticket(
    session: AsyncSession,
    ticket: str,
    *,
    actor_id: str | None,
    store: BytesStore,
    ip: str | None = None,
    user_agent: str | None = None,
    roles: list[str] | None = None,
) -> tuple[bytes, TeacherArtifact]:
    """下载即鉴权：签名 → 票据状态 → 重新查 ACL → 复算字节 hash → 记审计 → 置为已用。"""
    settings = get_settings()
    row = (
        await session.execute(
            select(TeacherArtifactTicket).where(TeacherArtifactTicket.ticket == ticket)
        )
    ).scalar_one_or_none()
    if row is None:
        await _deny(
            session,
            ticket=ticket,
            artifact_id=None,
            actor_id=actor_id,
            reason="unknown_ticket",
            status=403,
            code=ErrorCode.FORBIDDEN,
            message="下载票据无效",
            ip=ip,
            user_agent=user_agent,
        )

    expected_sig = _sign(
        settings.jwt_secret, _claims(row.artifact_id, row.actor_id, row.expires_at, row.acl_epoch)
    )
    provided_sig = ticket.rsplit(".", 1)[-1]
    if not hmac.compare_digest(expected_sig, provided_sig):
        await _deny(
            session, ticket=ticket, artifact_id=row.artifact_id, actor_id=actor_id,
            reason="signature_mismatch", status=403, code=ErrorCode.FORBIDDEN,
            message="下载票据签名不匹配", ip=ip, user_agent=user_agent,
        )

    now = datetime.now(UTC)
    expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    if row.revoked_at is not None:
        await _deny(
            session, ticket=ticket, artifact_id=row.artifact_id, actor_id=actor_id,
            reason="revoked", status=403, code=ErrorCode.FORBIDDEN,
            message="下载权限已被撤销", ip=ip, user_agent=user_agent,
        )
    if row.used_at is not None:
        await _deny(
            session, ticket=ticket, artifact_id=row.artifact_id, actor_id=actor_id,
            reason="already_used", status=410, code=ErrorCode.NOT_FOUND,
            message="下载票据已使用", ip=ip, user_agent=user_agent,
        )
    if expires_at <= now:
        await _deny(
            session, ticket=ticket, artifact_id=row.artifact_id, actor_id=actor_id,
            reason="expired", status=410, code=ErrorCode.NOT_FOUND,
            message="下载票据已过期", ip=ip, user_agent=user_agent,
        )
    if actor_id != row.actor_id:
        await _deny(
            session, ticket=ticket, artifact_id=row.artifact_id, actor_id=actor_id,
            reason="actor_mismatch", status=403, code=ErrorCode.FORBIDDEN,
            message="下载票据与当前身份不匹配", ip=ip, user_agent=user_agent,
        )

    artifact = await get_artifact(session, str(row.artifact_id))
    allowed, reason = acl_allows(artifact, actor_id, roles)
    if not allowed:
        await _deny(
            session, ticket=ticket, artifact_id=artifact.id, actor_id=actor_id,
            reason=reason, status=403, code=ErrorCode.FORBIDDEN,
            message="无权访问该产物", ip=ip, user_agent=user_agent,
        )

    data = store.get_bytes(artifact.bucket, artifact.object_key)
    if sha256_of(data) != artifact.content_sha256:
        await _deny(
            session, ticket=ticket, artifact_id=artifact.id, actor_id=actor_id,
            reason="content_hash_mismatch", status=500, code=ErrorCode.INTERNAL,
            message="产物字节与登记哈希不一致，已拒绝投递", ip=ip, user_agent=user_agent,
        )

    await _audit(
        session, ticket=ticket, artifact_id=artifact.id, actor_id=actor_id,
        decision="allow", reason="ok", ip=ip, user_agent=user_agent,
    )
    row.used_at = now
    await session.flush()
    return data, artifact


async def revoke_artifact_access(
    session: AsyncSession, artifact: TeacherArtifact, *, reason: str = "manual_revoke"
) -> dict[str, Any]:
    """撤权：作废全部未用票据并提升 acl_epoch —— 旧票据立刻失效（不是等它自然过期）。"""
    now = datetime.now(UTC)
    result = await session.execute(
        update(TeacherArtifactTicket)
        .where(
            TeacherArtifactTicket.artifact_id == artifact.id,
            TeacherArtifactTicket.used_at.is_(None),
            TeacherArtifactTicket.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    artifact.acl_epoch = artifact.acl_epoch + 1
    await session.flush()
    return {"artifact_id": str(artifact.id), "revoked_tickets": result.rowcount or 0, "acl_epoch": artifact.acl_epoch, "reason": reason}


async def list_audit(session: AsyncSession, artifact_id: str) -> list[dict[str, Any]]:
    uid = uuid.UUID(artifact_id)
    rows = (
        await session.execute(
            select(TeacherArtifactDownloadAudit)
            .where(TeacherArtifactDownloadAudit.artifact_id == uid)
            .order_by(TeacherArtifactDownloadAudit.at)
        )
    ).scalars().all()
    return [
        {
            "ticket": r.ticket,
            "actor_id": r.actor_id,
            "decision": r.decision,
            "reason": r.reason,
            "at": r.at.isoformat() if r.at else None,
        }
        for r in rows
    ]
