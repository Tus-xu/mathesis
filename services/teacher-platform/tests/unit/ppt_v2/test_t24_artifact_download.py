"""T24 下载票据：短时 / 单次 / 绑定 actor+artifact+ACL / 撤权即失效 / 审计留痕。

威胁模型：链接被转发、链接被篡改、产物被换字节、教师被移出可见范围。
每条都对应一个断言，而不是只测 happy path。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import ApiError
from app.storage import artifacts as svc
from app.storage.models import TeacherArtifactDownloadAudit, TeacherArtifactTicket


class _FakeStore:
    """内存对象存储：只实现 put/get，避免单测依赖 MinIO。"""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_bytes(self, bucket: str, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.objects[(bucket, object_name)] = data

    def get_bytes(self, bucket: str, object_name: str) -> bytes:
        return self.objects[(bucket, object_name)]


async def _artifact(db, store, *, owner: str | None = None, data: bytes = b"PDF-1.7 body") -> tuple:
    owner_id = uuid.UUID(owner) if owner else None
    row = await svc.register_artifact(
        db,
        store=store,
        spec_kind="teaching_deck",
        spec_id=str(uuid.uuid4()),
        spec_revision=2,
        kind="pdf",
        media_type="application/pdf",
        data=data,
        filename="课件.pdf",
        owner_id=owner_id,
        created_by_run_id="task:test-run",
        source_lineage={"spec_kind": "teaching_deck", "derived_from": ["8200:x"]},
    )
    return row


async def test_ticket_is_signed_and_redeems_once(db) -> None:
    store = _FakeStore()
    teacher = uuid.uuid4()
    artifact = await _artifact(db, store, owner=str(teacher))

    ticket = await svc.issue_ticket(db, artifact, actor_id=str(teacher), ttl_seconds=300)
    assert ticket["single_use"] is True
    assert ticket["url"].endswith(ticket["ticket"])
    assert "." in ticket["ticket"], "票据必须带签名段（jti.sig）"

    data, got = await svc.redeem_ticket(db, ticket["ticket"], actor_id=str(teacher), store=store)
    assert data == b"PDF-1.7 body"
    assert got.id == artifact.id

    with pytest.raises(ApiError) as exc:
        await svc.redeem_ticket(db, ticket["ticket"], actor_id=str(teacher), store=store)
    assert exc.value.status_code == 410

    audit = (await db.execute(select(TeacherArtifactDownloadAudit).order_by(TeacherArtifactDownloadAudit.at))).scalars().all()
    assert [a.decision for a in audit] == ["allow", "deny"]
    assert audit[-1].reason == "already_used"


async def test_tampered_ticket_is_forbidden(db) -> None:
    store = _FakeStore()
    teacher = uuid.uuid4()
    artifact = await _artifact(db, store, owner=str(teacher))
    ticket = await svc.issue_ticket(db, artifact, actor_id=str(teacher))
    jti, _sig = ticket["ticket"].split(".", 1)
    forged = f"{jti}." + "0" * 64

    with pytest.raises(ApiError) as exc:
        await svc.redeem_ticket(db, forged, actor_id=str(teacher), store=store)
    assert exc.value.status_code == 403


async def test_expired_ticket_is_410(db) -> None:
    """过期必须由"时间真的过去了"造成，而不是改库里的 expires_at —— 后者会先被签名拒绝。"""
    store = _FakeStore()
    teacher = uuid.uuid4()
    artifact = await _artifact(db, store, owner=str(teacher))

    from app.core.config import get_settings

    past = (datetime.now(UTC) - timedelta(seconds=5)).replace(microsecond=0)
    sig = svc._sign(  # noqa: SLF001
        get_settings().jwt_secret,
        svc._claims(artifact.id, str(teacher), past, artifact.acl_epoch),  # noqa: SLF001
    )
    ticket = f"expiredjti.{sig}"
    db.add(
        TeacherArtifactTicket(
            ticket=ticket,
            artifact_id=artifact.id,
            actor_id=str(teacher),
            expires_at=past,
            acl_epoch=artifact.acl_epoch,
        )
    )
    await db.flush()

    with pytest.raises(ApiError) as exc:
        await svc.redeem_ticket(db, ticket, actor_id=str(teacher), store=store)
    assert exc.value.status_code == 410
    deny = [a for a in (await db.execute(select(TeacherArtifactDownloadAudit))).scalars().all() if a.decision == "deny"]
    assert deny and deny[-1].reason == "expired"


async def test_other_actor_cannot_use_forwarded_link(db) -> None:
    store = _FakeStore()
    teacher = uuid.uuid4()
    other = uuid.uuid4()
    artifact = await _artifact(db, store, owner=str(teacher))
    ticket = await svc.issue_ticket(db, artifact, actor_id=str(teacher))

    with pytest.raises(ApiError) as exc:
        await svc.redeem_ticket(db, ticket["ticket"], actor_id=str(other), store=store)
    assert exc.value.status_code == 403
    deny = [a for a in (await db.execute(select(TeacherArtifactDownloadAudit))).scalars().all() if a.decision == "deny"]
    assert deny[-1].reason == "actor_mismatch"


async def test_revoke_invalidates_outstanding_tickets(db) -> None:
    store = _FakeStore()
    teacher = uuid.uuid4()
    artifact = await _artifact(db, store, owner=str(teacher))
    ticket = await svc.issue_ticket(db, artifact, actor_id=str(teacher))

    out = await svc.revoke_artifact_access(db, artifact)
    assert out["revoked_tickets"] == 1 and out["acl_epoch"] == 1

    with pytest.raises(ApiError) as exc:
        await svc.redeem_ticket(db, ticket["ticket"], actor_id=str(teacher), store=store)
    assert exc.value.status_code == 403
    deny = [a for a in (await db.execute(select(TeacherArtifactDownloadAudit))).scalars().all() if a.decision == "deny"]
    assert deny[-1].reason == "revoked"


async def test_acl_denied_when_owner_changed(db) -> None:
    store = _FakeStore()
    owner = uuid.uuid4()
    outsider = uuid.uuid4()
    artifact = await _artifact(db, store, owner=str(owner))
    ticket = await svc.issue_ticket(db, artifact, actor_id=str(outsider))

    # 票据签发给 outsider，但 artifact 的 ACL 只允许 owner → 重新查权必须拒绝
    with pytest.raises(ApiError) as exc:
        await svc.redeem_ticket(db, ticket["ticket"], actor_id=str(outsider), store=store)
    assert exc.value.status_code == 403
    deny = [a for a in (await db.execute(select(TeacherArtifactDownloadAudit))).scalars().all() if a.decision == "deny"]
    assert deny[-1].reason == "acl_denied"


async def test_content_hash_drift_refuses_delivery(db) -> None:
    store = _FakeStore()
    teacher = uuid.uuid4()
    artifact = await _artifact(db, store, owner=str(teacher), data=b"real bytes")
    ticket = await svc.issue_ticket(db, artifact, actor_id=str(teacher))

    store.objects[(artifact.bucket, artifact.object_key)] = b"tampered bytes"
    with pytest.raises(ApiError) as exc:
        await svc.redeem_ticket(db, ticket["ticket"], actor_id=str(teacher), store=store)
    assert exc.value.status_code == 500
    deny = [a for a in (await db.execute(select(TeacherArtifactDownloadAudit))).scalars().all() if a.decision == "deny"]
    assert deny[-1].reason == "content_hash_mismatch"


async def test_zero_byte_artifact_is_refused(db) -> None:
    store = _FakeStore()
    with pytest.raises(ApiError) as exc:
        await _artifact(db, store, data=b"")
    assert exc.value.code.name == "INTERNAL"
