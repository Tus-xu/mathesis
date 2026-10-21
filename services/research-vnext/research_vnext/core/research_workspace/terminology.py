"""RV17 · 术语记忆（宪法 §42 Terminology Memory）。

## 为什么需要它

§42 明确要求：

> 项目应拥有：Terminology Memory
> 至少在同一 Project 内保持：同一个专业术语，翻译一致。

在此之前 `translate` 的每一次调用都是**独立**向模型发问 —— 同一个术语在两次翻译里
可能给出不同译法，而科研论文里术语漂移是**硬伤**（同一篇稿子里 "a posteriori error estimate"
一会儿叫「后验误差估计」一会儿叫「事后误差估计」，审稿人一眼看出）。

## 两版演进（记录决策，避免后来者重走）

| 版本 | 存储 | 问题 |
|---|---|---|
| v1 | 仅**进程内** LRU | 8300 重启 → 已积累术语全丢 →「一致性」只在单次进程生命周期内成立 |
| **v2（当前）** | **`research_terminology` 表（持久）** + 进程内读缓存 | — |

v2 的关键点：**「同项目同术语只有一个译名」由数据库唯一约束保证**
（`uq_rt19_tenant_proj_term`），而不是靠应用层自觉。
进程内只做**读缓存**（`prompt_block()` 每次翻译都要用，不该每次都查库）+ 写穿透。

## 用法

    mem = TerminologyMemory.for_project(tenant_id, project_id)
    hint = mem.prompt_block(db)                  # 注入 prompt 的「已确立术语」清单（读缓存）
    mem.remember_many(db, pairs)                 # 从模型返回的术语对照里学习（写穿透 + upsert）
    mem.lookup(db, "a posteriori error estimate") # 单条查询
    mem.conflicts(db, text, translated)          # §42 一致性反向校验素材
"""

from __future__ import annotations

import re
import threading
import time
import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import ResearchTerminologyRow, utcnow

#: 单个项目最多记住多少条术语（防无界增长；超出时淘汰最少复用的）。
MAX_TERMS = 500
#: 进程内读缓存存活时间（秒）。**库是权威**，缓存只是省一次查询。
CACHE_TTL_S = 10 * 60

#: 术语候选：拉丁字母短语（2~6 个词）或 CJK 短术语。
_LATIN_TERM_RE = re.compile(r"\b[A-Za-z][A-Za-z\-]*(?:\s+[A-Za-z][A-Za-z\-]*){0,5}\b")
_CJK_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,10}")


def normalize_term(term: str) -> str:
    """术语规范化：小写 + 折叠空白。**查询与存储必须共用本函数**（否则"看起来有记忆却查不到"）。"""
    return re.sub(r"\s+", " ", (term or "").strip().lower())


def _as_uuid(v: uuid.UUID | str) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def _valid(source: str, target: str) -> bool:
    s = normalize_term(source)
    t = (target or "").strip()
    if len(s) < 2 or len(t) < 2:
        return False
    # 过长的"目标"不是术语而是整句 → 拒收（宁缺勿错）
    return len(t) <= 60


class TerminologyMemory:
    """某项目的术语记忆：**DB 持久 + 进程内读缓存**。线程安全。

    不自己持有 Session —— 调用方按需传 `db`（Agent 任务在 BackgroundTasks 里跑，
    Session 生命周期由调用方掌握，本类不延长它）。
    """

    def __init__(self, tenant_id: uuid.UUID | str, project_id: uuid.UUID | str) -> None:
        self.tenant_id = _as_uuid(tenant_id)
        self.project_id = _as_uuid(project_id)
        self._cache: dict[str, str] = {}
        self._cache_at = 0.0
        self._lock = threading.Lock()

    @classmethod
    def for_project(
        cls, tenant_id: uuid.UUID | str, project_id: uuid.UUID | str
    ) -> "TerminologyMemory":
        """取某项目的记忆对象。**不查库** —— 库访问在 `load()` 及以下各方法里。"""
        return cls(tenant_id, project_id)

    # ── 读 ──────────────────────────────────────────────────────────
    def load(self, db: Session, *, force: bool = False) -> dict[str, str]:
        """把该项目的术语全量读入缓存（TTL 内直接返回缓存）。"""
        with self._lock:
            if not force and self._cache and time.time() - self._cache_at < CACHE_TTL_S:
                return dict(self._cache)
        rows = db.execute(
            select(ResearchTerminologyRow.source_term, ResearchTerminologyRow.target_term)
            .where(
                ResearchTerminologyRow.tenant_id == self.tenant_id,
                ResearchTerminologyRow.project_id == self.project_id,
            )
            .order_by(
                ResearchTerminologyRow.hits.desc(),
                ResearchTerminologyRow.source_term.asc(),
            )
        ).all()
        with self._lock:
            self._cache = {str(s): str(t) for s, t in rows}
            self._cache_at = time.time()
            return dict(self._cache)

    def lookup(self, db: Session, source: str) -> str | None:
        return self.load(db).get(normalize_term(source))

    def prompt_block(self, db: Session, limit: int = 40) -> str:
        """生成注入 prompt 的「已确立术语」清单；无记忆时返回空串（**不污染 prompt**）。"""
        snap = self.load(db)
        if not snap:
            return ""
        return (
            "【本项目已确立的术语译法（必须沿用，不得另起译名）】\n"
            + "\n".join(f"- {k} → {snap[k]}" for k in sorted(snap)[:limit])
        )

    def snapshot(self, db: Session) -> dict[str, str]:
        return self.load(db)

    # ── 写 ──────────────────────────────────────────────────────────
    def remember_many(self, db: Session, pairs: list[tuple[str, str]]) -> int:
        """批量记住术语对，返回**新学到的条数**（已存在的只刷新 hits）。

        `ON CONFLICT DO UPDATE` 让数据库唯一约束成为**一致性保证**：
        同一项目同一术语再次学习时覆盖为最新译名并累加 `hits`，**永远不会出现两行**。
        """
        accepted: list[dict] = []
        seen: set[str] = set()
        for p in pairs or []:
            try:
                src, tgt = p[0], p[1]
            except (IndexError, TypeError):
                continue
            if not _valid(str(src), str(tgt)):
                continue
            k = normalize_term(str(src))
            if k in seen:
                continue
            seen.add(k)
            accepted.append({"src": k, "tgt": str(tgt).strip()})
        if not accepted:
            return 0

        known = set(self.load(db).keys())
        fresh = [a for a in accepted if a["src"] not in known]

        now = utcnow()
        for a in accepted:
            stmt = pg_insert(ResearchTerminologyRow).values(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                source_term=a["src"],
                target_term=a["tgt"],
                hits=1,
                created_at=now,
                updated_at=now,
            )
            db.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_rt19_tenant_proj_term",
                    set_={
                        "target_term": stmt.excluded.target_term,
                        "hits": ResearchTerminologyRow.__table__.c.hits + 1,
                        "updated_at": now,
                    },
                )
            )
        db.flush()
        self.load(db, force=True)  # 写穿透
        return len(fresh)

    def remember(self, db: Session, source: str, target: str) -> bool:
        """记一条；返回是否**新学**。"""
        return self.remember_many(db, [(source, target)]) > 0

    # ── §42 一致性校验 ──────────────────────────────────────────────
    def conflicts(self, db: Session, source_text: str, translated: str) -> list[str]:
        """反向校验：译文有没有沿用已确立的译法。

        判据（保守、可核对）：源文里**出现了**某术语，而它的已确立译名**没出现在**译文中
        → 记一条提醒。这是**提示**而非判决（译名可能合理地变形），故只作为 finding 呈现。
        """
        low = (source_text or "").lower()
        out: list[str] = []
        for src, tgt in self.load(db).items():
            if src in low and len(tgt) >= 2 and tgt not in (translated or ""):
                out.append(f"「{src}」此前译为「{tgt}」，本次译文中未出现")
        return out

    # ── 维护 ────────────────────────────────────────────────────────
    def prune(self, db: Session) -> int:
        """超出 `MAX_TERMS` 时淘汰 `hits` 最少的条目；返回删除条数。"""
        if len(self.load(db)) <= MAX_TERMS:
            return 0
        keep = (
            db.execute(
                select(ResearchTerminologyRow.id)
                .where(
                    ResearchTerminologyRow.tenant_id == self.tenant_id,
                    ResearchTerminologyRow.project_id == self.project_id,
                )
                .order_by(
                    ResearchTerminologyRow.hits.desc(),
                    ResearchTerminologyRow.updated_at.desc(),
                )
                .limit(MAX_TERMS)
            )
            .scalars()
            .all()
        )
        res = db.execute(
            delete(ResearchTerminologyRow).where(
                ResearchTerminologyRow.tenant_id == self.tenant_id,
                ResearchTerminologyRow.project_id == self.project_id,
                ResearchTerminologyRow.id.notin_(keep),
            )
        )
        db.flush()
        self.load(db, force=True)
        return int(res.rowcount or 0)

    def clear(self, db: Session) -> int:
        """清空本项目术语记忆（数据重置 / 测试用）。"""
        res = db.execute(
            delete(ResearchTerminologyRow).where(
                ResearchTerminologyRow.tenant_id == self.tenant_id,
                ResearchTerminologyRow.project_id == self.project_id,
            )
        )
        db.flush()
        self.load(db, force=True)
        return int(res.rowcount or 0)

    def forget_cache(self) -> None:
        """只丢进程内缓存（下次访问重新读库）—— 用于测试跨进程重启的语义。"""
        with self._lock:
            self._cache = {}
            self._cache_at = 0.0


#: 实例缓存：同一进程内同一项目复用同一对象（读缓存才有意义）。
_INSTANCES: dict[tuple[uuid.UUID, uuid.UUID], TerminologyMemory] = {}
_INSTANCES_LOCK = threading.Lock()


def for_project(tenant_id: uuid.UUID | str, project_id: uuid.UUID | str) -> TerminologyMemory:
    """取（或创建）某项目的记忆对象；同进程同项目复用（读缓存生效）。"""
    k = (_as_uuid(tenant_id), _as_uuid(project_id))
    with _INSTANCES_LOCK:
        m = _INSTANCES.get(k)
        if m is None:
            m = TerminologyMemory(k[0], k[1])
            _INSTANCES[k] = m
        return m


__all__ = [
    "CACHE_TTL_S",
    "MAX_TERMS",
    "TerminologyMemory",
    "for_project",
    "normalize_term",
]
