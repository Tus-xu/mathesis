"""PromptRelease：prompt 发布绑定（R11.5-B，DECISIONS §5 / 迁移 d17b2c3e4f5a）。

Prompt 必须绑定 approved release（content hash / schema / toolset / model policy）：
- `prompt_releases` 表由正式 Alembic 迁移所有（领域无关的发布台账）。
- 生产（fail_closed=True）：渲染前校验 name/version 存在且 status=APPROVED、
  content_hash 与模板当前内容 hash 一致；否则抛 PromptReleaseError（fail-closed，
  不静默用旧模板/未发布模板）。
- local/test：允许未校验（`None` store 或 fail_closed=False）→ InMemory 或跳过。
- ai_runs.lineage 记录 release id/version/hash，形成「prompt → release → run」完整链路。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text

from app.db.ownership import RuntimeOwnershipError

APPROVED = "APPROVED"
DRAFT = "DRAFT"
SUPERSEDED = "SUPERSEDED"


class PromptReleaseError(RuntimeError):
    """prompt 未绑定 approved release 或 hash/schema/toolset/model policy 不符 → fail-closed。"""


@dataclass
class PromptRelease:
    prompt_name: str
    version: str
    content_hash: str
    status: str = APPROVED
    schema: str | None = None
    toolset: dict[str, Any] | None = None
    model_policy: dict[str, Any] | None = None
    released_by: str | None = None
    id: str = "pending"
    created_at: float = 0.0

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt_name": self.prompt_name,
            "version": self.version,
            "content_hash": self.content_hash,
            "status": self.status,
            "schema": self.schema,
            "toolset": self.toolset,
            "model_policy": self.model_policy,
        }


class PromptReleaseStore(Protocol):
    async def resolve(self, prompt_name: str, version: str) -> PromptRelease | None: ...
    async def publish(self, release: PromptRelease) -> None: ...


class InMemoryPromptReleaseStore:
    """local/test。"""

    def __init__(self) -> None:
        self._rows: list[PromptRelease] = []

    async def resolve(self, prompt_name: str, version: str) -> PromptRelease | None:
        for r in reversed(self._rows):
            if r.prompt_name == prompt_name and r.version == version:
                return r
        return None

    async def publish(self, release: PromptRelease) -> None:
        self._rows.append(release)


class PostgresPromptReleaseStore:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def _ensure_owned(self, session) -> None:
        row = await session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'prompt_releases'"
            )
        )
        if row.scalar() is None:
            raise RuntimeOwnershipError(
                "prompt_releases 表缺失（需先执行 Alembic 迁移 d17b2c3e4f5a）"
            )

    async def resolve(self, prompt_name: str, version: str) -> PromptRelease | None:
        async with self._session_factory() as session:
            await self._ensure_owned(session)
            row = (
                await session.execute(
                    text(
                        "SELECT id, prompt_name, version, content_hash, status, schema, toolset, model_policy,"
                        " released_by FROM prompt_releases WHERE prompt_name = :n AND version = :v"
                        " ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"n": prompt_name, "v": version},
                )
            ).mappings().first()
        if row is None:
            return None

        def _load(value):
            if isinstance(value, dict):
                return value
            return json.loads(value) if value else None

        return PromptRelease(
            id=str(row["id"]),
            prompt_name=row["prompt_name"],
            version=row["version"],
            content_hash=row["content_hash"],
            status=row["status"],
            schema=row["schema"],
            toolset=_load(row["toolset"]),
            model_policy=_load(row["model_policy"]),
            released_by=row["released_by"],
        )

    async def publish(self, release: PromptRelease) -> None:
        async with self._session_factory() as session:
            await self._ensure_owned(session)
            release_id = release.id if release.id != "pending" else str(uuid.uuid4())
            await session.execute(
                text(
                    "INSERT INTO prompt_releases (id, prompt_name, version, content_hash, schema, toolset,"
                    " model_policy, status, released_by, created_at)"
                    " VALUES (:id, :n, :v, :h, :schema, :toolset, :policy, :status, :by, to_timestamp(:ts))"
                    " ON CONFLICT (prompt_name, version) DO UPDATE SET content_hash=EXCLUDED.content_hash,"
                    " status=EXCLUDED.status, schema=EXCLUDED.schema, toolset=EXCLUDED.toolset,"
                    " model_policy=EXCLUDED.model_policy, released_by=EXCLUDED.released_by"
                ),
                {
                    "id": uuid.UUID(release_id),
                    "n": release.prompt_name,
                    "v": release.version,
                    "h": release.content_hash,
                    "schema": release.schema,
                    "toolset": json.dumps(release.toolset, ensure_ascii=False) if release.toolset else None,
                    "policy": json.dumps(release.model_policy, ensure_ascii=False) if release.model_policy else None,
                    "status": release.status,
                    "by": release.released_by,
                    "ts": release.created_at,
                },
            )
            await session.commit()


async def require_approved(
    store: PromptReleaseStore | None,
    *,
    prompt_name: str,
    version: str,
    content_hash: str,
    fail_closed: bool,
    toolset: list[str] | None = None,
    model_policy: dict[str, Any] | None = None,
) -> PromptRelease | None:
    """渲染前 binding：校验 approved release + content hash + toolset/model policy 一致。

    返回 release（供 ai_runs.lineage 引用）。store 为 None（未注入）且 fail_closed=True
    → 抛错（生产必须显式注入 store）。store.resolve 支持 sync/async。
    """
    if store is None:
        if fail_closed:
            raise PromptReleaseError(
                f"生产未注入 PromptReleaseStore，无法校验 prompt 发布（{prompt_name}@{version}）"
            )
        return None
    resolved = store.resolve(prompt_name, version)
    if hasattr(resolved, "__await__"):
        release = await resolved
    else:
        release = resolved
    if release is None:
        if fail_closed:
            raise PromptReleaseError(f"prompt {prompt_name}@{version} 无任何 release 记录（fail-closed）")
        return None
    if release.status != APPROVED:
        if fail_closed:
            raise PromptReleaseError(f"prompt {prompt_name}@{version} release 未 APPROVED（{release.status}）")
        return None
    if release.content_hash != content_hash:
        if fail_closed:
            raise PromptReleaseError(
                f"prompt {prompt_name}@{version} 模板内容 hash 漂移：release={release.content_hash[:12]}，当前={content_hash[:12]}"
            )
        return None
    if toolset is not None and release.toolset:
        expected = set(release.toolset.get("tools") or [])
        actual = set(toolset)
        if expected and actual != expected:
            if fail_closed:
                raise PromptReleaseError(f"prompt {prompt_name}@{version} toolset 不匹配 release")
            return None
    if model_policy is not None and release.model_policy:
        if release.model_policy.get("policy") not in (None, model_policy.get("policy")):
            if fail_closed:
                raise PromptReleaseError(f"prompt {prompt_name}@{version} model policy 不匹配 release")
            return None
    return release


def _default_fail_closed() -> bool:
    """生产默认 fail-closed（与 checkpoint/ai_runs 同判定）。"""
    from app.agent.checkpoint import is_production

    return is_production()