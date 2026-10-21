"""学情画像卡聚合（P1，AI 管家核心）

将散落在 mastery_records / error_records / mastery_snapshots / streaks / user_profiles
的学情信号，聚合为一张「学情画像卡」（结构化文本），注入模型 system prompt（P0 槽位），
让模型"全局知晓"学生的掌握度、薄弱点、错题、节奏与偏好。

隔离（v1.2）：按 (user_id, role, domain) 聚合。
- role：端隔离（student/teacher/...），本服务主要服务 student 端。
- domain：学科/技能隔离——目前知识体系为数学（MATH- 前缀），新增学科按 kp_code 前缀区分。
- 聚合结果缓存 60s（Redis），聊天高频场景不重复查库。

任何异常都降级为 None（由装配器兜底），绝不抛给对话主链路。
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_profile import UserProfile

logger = structlog.get_logger(__name__)

# 画像卡注入预算（token，P0 槽位；超限截断。含错题明细段，400 不够用）
_PROFILE_CARD_TOKEN_BUDGET = 700
# 学情窗口（天）
_LOOKBACK_DAYS = 7
# 薄弱点 Top N
_WEAK_TOP_N = 3
# 近期错题明细条数（注入画像卡，供"错题规律分析"类提问引用真实数据，防模型编造）
_ERROR_DETAIL_TOP_N = 8
# 聚合缓存 TTL（秒）
_CACHE_TTL_SECONDS = 60
# 真实知识点前缀（与 mock_exam 白名单一致；domain 隔离依据）
_REAL_KP_PREFIXES = ("MATH-", "MX", "BK")


def _est_tokens(text: str) -> int:
    """中英文混合 token 估算（与 context.py 同规则，避免循环依赖）"""
    if not text:
        return 0
    cn = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return int(cn / 1.5 + (len(text) - cn) / 4.0) + 1


class LearningProfileService:
    """学情画像卡聚合服务（全局单例，见 get_learning_profile_service）"""

    async def build_profile_card(
        self,
        db: AsyncSession,
        user_id: str | uuid.UUID,
        *,
        role: str = "student",
        domain: str = "math",
        days: int = _LOOKBACK_DAYS,
        use_cache: bool = True,
    ) -> dict | None:
        """聚合学情信号 → 画像卡 dict。

        返回 None（无任何数据/异常降级）时，装配器不注入 P0 段。
        dict 结构：
        {
          "grade": str, "level": str,
          "mastery_avg": float|None, "mastery_delta_7d": float|None,
          "weak_top3": [{"name","mastery","error_count"}],
          "error_total": int, "error_recent": int,
          "streak_current": int, "checked_today": bool,
          "preferences": str,
        }
        """
        try:
            uid = str(user_id)
            # 缓存命中（60s）
            if use_cache:
                cached = await self._get_cached(uid, role, domain)
                if cached is not None:
                    return cached

            card = await self._aggregate(db, uid, role, domain, days)
            if card is None:
                return None
            if use_cache:
                await self._set_cache(uid, role, domain, card)
            return card
        except Exception as e:
            logger.warning("learning_profile.build_failed", error=str(e)[:150])
            return None

    async def build_profile_card_text(self, db, user_id, **kw) -> str:
        """画像卡 → 注入文本（P0 槽位）。无数据/异常 → 空串（装配器跳过）"""
        card = await self.build_profile_card(db, user_id, **kw)
        if not card:
            return ""
        text_card = self._format_card(card)
        # 预算内截断：超 token 时逐段丢弃（偏好→弱项→节奏→掌握）
        if _est_tokens(text_card) > _PROFILE_CARD_TOKEN_BUDGET:
            parts = text_card.split("\n")
            while len(parts) > 2 and _est_tokens("\n".join(parts)) > _PROFILE_CARD_TOKEN_BUDGET:
                parts.pop()
            text_card = "\n".join(parts)
        return text_card

    # ------------------------------------------------------------------ #
    #  内部
    # ------------------------------------------------------------------ #

    async def _aggregate(self, db, uid: str, role: str, domain: str, days: int) -> dict | None:
        """聚合（≤6 条查询，全部走索引；domain 过滤由 kp_code 前缀实现）"""
        cutoff = datetime.now(UTC) - timedelta(days=days)

        # 1. 用户档案（grade/level/preferences）
        profile = (
            await db.execute(select(UserProfile).where(UserProfile.user_id == uid))
        ).scalar_one_or_none()

        # 2. 掌握度均值 + 7 天前均值（mastery_snapshots：每日快照，date 类型参数）
        rows = (
            await db.execute(
                text(
                    "SELECT date, AVG(mastery) FROM mastery_snapshots "
                    "WHERE user_id = :uid AND date >= :cut GROUP BY date ORDER BY date"
                ),
                {"uid": uid, "cut": cutoff.date()},
            )
        ).all()
        mastery_avg = None
        mastery_delta = None
        if rows:
            recent = [r[1] for r in rows if r[1] is not None]
            if recent:
                mastery_avg = round(sum(recent) / len(recent), 3)
            # 7 天 delta：首日 vs 末日（有 ≥2 天数据才可比）
            if len(rows) >= 2 and rows[0][1] is not None and rows[-1][1] is not None:
                mastery_delta = round(float(rows[-1][1]) - float(rows[0][1]), 3)

        # 3. 薄弱 Top3（mastery_records 按 kp 聚合，join knowledge_points 拿 code/name，
        #    仅真实前缀；错题数关联 error_records）
        weak_rows = (
            await db.execute(
                text(
                    "SELECT kp.code, kp.name, mr.mastery, mr.practice_count, "
                    "(SELECT COUNT(*) FROM error_records er WHERE er.user_id = mr.user_id "
                    " AND er.kp_code = kp.code AND er.deleted_at IS NULL) AS err_cnt "
                    "FROM mastery_records mr "
                    "JOIN knowledge_points kp ON kp.id = mr.kp_id "
                    "WHERE mr.user_id = :uid AND kp.code IS NOT NULL "
                    "ORDER BY mr.mastery ASC LIMIT :limit"
                ),
                {"uid": uid, "limit": _WEAK_TOP_N * 2},
            )
        ).all()
        weak_top3: list[dict] = []
        for code, kp_name, mastery, _pc, err_cnt in weak_rows:
            if not code or not str(code).startswith(_REAL_KP_PREFIXES):
                continue
            weak_top3.append(
                {"code": str(code), "name": str(kp_name or code), "mastery": float(mastery or 0), "error_count": int(err_cnt or 0)}
            )
            if len(weak_top3) >= _WEAK_TOP_N:
                break

        # 4. 错题统计（总数 + 近 days 天新增）
        err_total = (
            await db.execute(
                text("SELECT COUNT(*) FROM error_records WHERE user_id = :uid AND deleted_at IS NULL"),
                {"uid": uid},
            )
        ).scalar() or 0
        err_recent = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM error_records "
                    "WHERE user_id = :uid AND deleted_at IS NULL AND created_at >= :cut"
                ),
                {"uid": uid, "cut": cutoff},
            )
        ).scalar() or 0

        # 4b. 近期错题明细（真实记录；"分析错题规律"等提问据此作答，杜绝编造）
        error_items: list[dict] = []
        with self._safe():
            try:
                from app.models.coursework import ErrorRecord

                rows = (
                    await db.execute(
                        select(
                            ErrorRecord.kp_code,
                            ErrorRecord.error_type,
                            ErrorRecord.question_text,
                            ErrorRecord.wrong_count,
                            ErrorRecord.created_at,
                        )
                        .where(
                            ErrorRecord.user_id == uuid.UUID(uid),
                            ErrorRecord.deleted_at.is_(None),
                        )
                        .order_by(ErrorRecord.created_at.desc())
                        .limit(_ERROR_DETAIL_TOP_N)
                    )
                ).all()
                for kp, etype, qtext, wrong, created in rows:
                    brief = str(qtext or "").replace("\n", " ").strip()[:60]
                    if not brief:
                        continue
                    error_items.append(
                        {
                            "kp": str(kp or ""),
                            "etype": str(etype or ""),
                            "brief": brief,
                            "wrong": int(wrong or 1),
                            "at": str(created)[:10] if created else "",
                        }
                    )
            except Exception:
                pass

        # 5. 打卡（streaks 表结构未知，尽力而为：查 streak 表当前连续天数）
        streak_current = 0
        checked_today = False
        with self._safe():
            try:
                sr = (
                    await db.execute(
                        text("SELECT current_streak, last_active_date FROM streaks WHERE user_id = :uid"),
                        {"uid": uid},
                    )
                ).first()
                if sr:
                    streak_current = int(sr[0] or 0)
                    last = sr[1]
                    if last is not None:
                        checked_today = str(last)[:10] == datetime.now(UTC).date().isoformat()
            except Exception:
                pass

        # 6. 偏好（user_profiles.preferences JSONB → 简洁文本）
        prefs_text = ""
        prefs = (profile.preferences if profile else None) or {}
        if isinstance(prefs, dict) and prefs:
            prefs_text = "、".join(f"{k}={v}" for k, v in list(prefs.items())[:3])

        if (
            not profile
            and mastery_avg is None
            and not weak_top3
            and err_total == 0
            and streak_current == 0
        ):
            return None  # 完全无数据，不注入

        return {
            "grade": (profile.grade if profile else "") or "",
            "level": (profile.level if profile else "unknown") or "unknown",
            "mastery_avg": mastery_avg,
            "mastery_delta_7d": mastery_delta,
            "weak_top3": weak_top3,
            "error_total": err_total,
            "error_recent": err_recent,
            "error_items": error_items,
            "streak_current": streak_current,
            "checked_today": checked_today,
            "preferences": prefs_text,
            "role": role,
            "domain": domain,
        }

    def _format_card(self, card: dict) -> str:
        """画像卡 dict → 注入文本（中文，紧凑）"""
        _ETYPE_ZH = {
            "concept": "概念", "formula": "公式", "calculation": "计算",
            "logic": "逻辑", "reading": "审题",
        }
        lines = ["【学生学情画像】"]
        seg = []
        if card.get("grade") or card.get("level") != "unknown":
            seg.append(f"{card['grade']} · {card['level']}")
        if card.get("mastery_avg") is not None:
            m = card["mastery_avg"]
            d = card.get("mastery_delta_7d")
            delta = f"，较7天前{'+' if d and d > 0 else ''}{round((d or 0) * 100)}pp" if d is not None else ""
            seg.append(f"掌握度均值 {round(m * 100)}%{delta}")
        if card.get("weak_top3"):
            weak = "、".join(
                f"{w['name'].split('-')[-1]}({round(w['mastery'] * 100)}%)" for w in card["weak_top3"]
            )
            seg.append(f"薄弱点：{weak}")
        if card.get("error_total"):
            seg.append(f"错题 {card['error_total']} 道（近7天 +{card.get('error_recent', 0)}）")
        if card.get("streak_current"):
            seg.append(f"连续打卡 {card['streak_current']} 天{'，今日已打卡' if card.get('checked_today') else ''}")
        if card.get("preferences"):
            seg.append(f"偏好：{card['preferences']}")
        if seg:
            lines.append("；".join(seg))
        else:
            return ""
        # 近期错题明细：学生问"错题规律/错在哪"时，模型只能基于这些真实记录作答
        items = card.get("error_items") or []
        if items:
            lines.append("【近期错题明细】（讨论错题时只准引用以下真实记录，禁止虚构题目）：")
            for it in items[:_ERROR_DETAIL_TOP_N]:
                kp_tail = (it.get("kp") or "未分类").split("-")[-1]
                etype = _ETYPE_ZH.get(it.get("etype", ""), it.get("etype") or "未标注")
                lines.append(f"· [{kp_tail}|{etype}] {it.get('brief', '')}（错{it.get('wrong', 1)}次，{it.get('at', '')}）")
        return "\n".join(lines)

    # ---- 缓存（Redis 尽力而为） ----

    def _cache_key(self, uid: str, role: str, domain: str) -> str:
        return f"lp:card:{uid}:{role}:{domain}"

    async def _get_cached(self, uid: str, role: str, domain: str) -> dict | None:
        try:
            from app.gateway.redis import get_redis
            r = get_redis()
            if r is None:
                return None
            raw = await r.get(self._cache_key(uid, role, domain))
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def _set_cache(self, uid: str, role: str, domain: str, card: dict) -> None:
        try:
            from app.gateway.redis import get_redis
            r = get_redis()
            if r is None:
                return
            await r.set(self._cache_key(uid, role, domain), json.dumps(card), ex=_CACHE_TTL_SECONDS)
        except Exception:
            pass

    async def invalidate(self, uid: str, role: str = "student", domain: str = "math") -> None:
        """学情变化后主动失效缓存（错题/打卡变更时由调用方触发）"""
        try:
            from app.gateway.redis import get_redis
            r = get_redis()
            if r is None:
                return
            await r.delete(self._cache_key(str(uid), role, domain))
        except Exception:
            pass

    @staticmethod
    def _safe():
        import contextlib
        return contextlib.suppress(Exception)


# ---- 全局单例 ----
_service: LearningProfileService | None = None


def get_learning_profile_service() -> LearningProfileService:
    global _service
    if _service is None:
        _service = LearningProfileService()
    return _service
