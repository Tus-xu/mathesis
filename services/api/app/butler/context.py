"""Butler Kernel v2 ContextAssembler（设计规格 §5 + 阶段 3B）

一次运行开始时组装不可变快照：
- 同一次运行每类上下文只读取一次；
- 同一 AsyncSession 顺序执行（禁止并发查询同一 session）；
- Snapshot 创建后按不可变数据使用（extra="forbid"）；
- 不把完整隐私文本 / API Key / 隐藏思维链放入 Snapshot（内容截断 + 只取脱敏字段）；
- 无数据时返回明确空结构，不抛 500。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.butler.contracts import (
    ActorContext,
    ButlerContextSnapshot,
    ButlerRequest,
)
from app.models.conversation import Conversation
from app.models.coursework import Assignment
from app.models.message import Message
from app.models.student_profile import StudentProfile
from app.models.system_config import SystemConfig
from app.models.user_model_config import UserModelConfig

#: 单字段文本进 Snapshot 的最大长度（脱敏：不携带完整隐私文本）
_TEXT_LIMIT = 200
#: 最近消息条数
_RECENT_MESSAGES = 5
#: 参与有效配置提取的 system_config key 前缀（避免整表配置进快照）
_CONFIG_KEY_PREFIXES = ("model", "xingchen", "web_search", "butler", "feature")
#: 递归脱敏：命中即剔除的敏感键名（任意深度 dict/list/tuple）
_SENSITIVE_KEY_FRAGMENTS = ("api_key", "api_secret", "secret", "password", "token", "credential")
#: 递归脱敏最大深度（防恶意/病态嵌套）
_REDACT_MAX_DEPTH = 12


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(frag in lowered for frag in _SENSITIVE_KEY_FRAGMENTS)


def _redact(value: Any, depth: int = 0) -> Any:
    """递归脱敏：任意深度 dict/list/tuple 中命中敏感键的整个 entry 剔除（键+值均不保留）。"""
    if depth > _REDACT_MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            k: _redact(v, depth + 1)
            for k, v in value.items()
            if not _is_sensitive_key(k)
        }
    if isinstance(value, list):
        return [_redact(v, depth + 1) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact(v, depth + 1) for v in value)
    return value


def _truncate(text: str | None, limit: int = _TEXT_LIMIT) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


class ContextAssembler:
    """上下文快照组装器（进程内单例，状态无关）。"""

    async def build(
        self, request: ButlerRequest, db: AsyncSession
    ) -> ButlerContextSnapshot:
        user_id = request.actor.user_id

        # 顺序读取（同一 session 严禁并发查询；无数据返回空结构）
        profile = await self._load_profile(db, user_id)
        conversation = await self._load_conversation(db, user_id)
        assignments = await self._load_assignments(db, request.actor, user_id)
        effective_config = await self._load_effective_config(db, user_id)
        feature_flags = self._derive_feature_flags(effective_config)

        return ButlerContextSnapshot(
            actor=request.actor,
            scene=request.scene,
            profile=profile,
            conversation=conversation,
            assignments=assignments,
            effective_config=effective_config,
            feature_flags=feature_flags,
        )

    async def _load_profile(self, db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
        row = (
            await db.execute(
                select(StudentProfile).where(StudentProfile.user_id == user_id)
            )
        ).scalars().first()
        if row is None:
            return {}
        return {
            "learning_style": row.learning_style or "",
            "current_stage": row.current_stage or "",
            "tags": (list(row.tags or [])[:10]),
            "weak_point_rank": (list(row.weak_point_rank or [])[:10]),
            "profile_card": _truncate(row.profile_card),
        }

    async def _load_conversation(self, db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
        conv = (
            await db.execute(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if conv is None:
            return {}
        recent = (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at.desc())
                .limit(_RECENT_MESSAGES)
            )
        ).scalars().all()
        return {
            "title": _truncate(conv.title),
            "summary": _truncate(conv.summary),
            "recent_messages": [
                {"role": m.role, "content": _truncate(m.content)}
                for m in reversed(recent)
            ],
        }

    async def _load_assignments(
        self, db: AsyncSession, actor: ActorContext, user_id: uuid.UUID
    ) -> tuple[dict[str, Any], ...]:
        class_ids = actor.class_ids
        if not class_ids:
            return ()
        rows = (
            await db.execute(
                select(Assignment).where(
                    Assignment.class_id.in_(class_ids),
                    Assignment.status != "archived",
                )
            )
        ).scalars().all()
        return tuple(
            {
                "id": str(a.id),
                "title": _truncate(a.title),
                "type": a.type,
                "deadline": (
                    a.deadline.isoformat() if isinstance(a.deadline, datetime) else None
                ),
                "status": a.status,
            }
            for a in rows[:20]
        )

    async def _load_effective_config(
        self, db: AsyncSession, user_id: uuid.UUID
    ) -> dict[str, Any]:
        cfg: dict[str, Any] = {}
        umc = (
            await db.execute(
                select(UserModelConfig).where(UserModelConfig.user_id == user_id)
            )
        ).scalars().first()
        if umc is not None:
            # 只取模型名等非密钥字段，绝不携带 primary/secondary_api_key
            cfg["primary_model"] = umc.primary_model or ""
            cfg["secondary_model"] = umc.secondary_model or ""
        sys_rows = (
            await db.execute(select(SystemConfig))
        ).scalars().all()
        for row in sys_rows:
            key = row.key
            if any(key.startswith(p) for p in _CONFIG_KEY_PREFIXES):
                cfg[f"sys:{key}"] = _redact(row.value)
        return cfg

    def _derive_feature_flags(self, effective_config: dict[str, Any]) -> frozenset[str]:
        flags: set[str] = set()
        for key, value in effective_config.items():
            if key.startswith("sys:") and isinstance(value, dict):
                enabled = value.get("enabled", False)
                if enabled:
                    flags.add(key.removeprefix("sys:"))
        return frozenset(flags)


context_assembler = ContextAssembler()
