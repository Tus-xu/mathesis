"""Skill 注册表（skills/registry.py）

启动时扫描已注册 skill → upsert 进 skills 表 → 注册进路由表。
下架 = 表内 status='disabled'，不用重启。
"""

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.skills.base import SkillExecutor

logger = structlog.get_logger()


class SkillRegistry:
    """Skill 注册表"""

    def __init__(self) -> None:
        self._skills: dict[str, SkillExecutor] = {}

    def register(self, skill: SkillExecutor) -> None:
        """注册一个 skill 实例"""
        skill_id = skill.skill_id
        self._skills[skill_id] = skill
        logger.info("skill.registered", skill_id=skill_id, name=skill.skill_name)

    def get(self, skill_id: str) -> SkillExecutor | None:
        """获取已注册的 skill"""
        return self._skills.get(skill_id)

    def get_active_ids(self) -> list[str]:
        """获取所有已注册的 skill ID"""
        return list(self._skills.keys())

    async def sync_to_db(self, db: AsyncSession) -> None:
        """将注册的 skill 同步到 skills 表（upsert）"""
        for skill_id, skill in self._skills.items():
            manifest = skill.manifest
            result = await db.execute(select(Skill).where(Skill.id == skill_id))
            existing = result.scalar_one_or_none()

            if existing:
                # 更新 manifest 和版本
                existing.manifest = manifest
                existing.name = manifest.get("name", skill_id)
                existing.version = manifest.get("version", "0.1.0")
            else:
                # 新建
                new_skill = Skill(
                    id=skill_id,
                    name=manifest.get("name", skill_id),
                    version=manifest.get("version", "0.1.0"),
                    manifest=manifest,
                    status="active",
                )
                db.add(new_skill)

        await db.flush()
        logger.info("skill.synced_to_db", count=len(self._skills))

    def get_manifests_for_routing(self) -> list[dict]:
        """获取所有 active skill 的 manifest（供路由 Function Calling 用）"""
        return [
            {"id": sid, "manifest": s.manifest}
            for sid, s in self._skills.items()
            if s.manifest.get("status", "active") != "disabled"
        ]


# ---- 全局单例 ----
_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    """获取全局 SkillRegistry 单例"""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry


def register_builtin_skills() -> None:
    """注册内置 skill（应用启动时调用）"""
    from app.skills.chat.main import ChatSkill
    from app.skills.qa_rag.main import QaRagSkill
    from app.skills.smart_quiz.main import SmartQuizExecutor
    from app.skills.socratic_solver.main import SocraticSolverExecutor

    registry = get_skill_registry()
    registry.register(ChatSkill())
    registry.register(QaRagSkill())
    registry.register(SocraticSolverExecutor())
    registry.register(SmartQuizExecutor())
