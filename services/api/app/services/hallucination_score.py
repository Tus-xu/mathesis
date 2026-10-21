"""防幻觉评分服务（P1-3，M2 迭代18）

把队友的离线前测评分脚本（D:\\工作流搭建情况\\wf_*前测防幻觉得分\\）
升级为产线运行时质检。评分公式（100 分制，对齐离线脚本语义版 v4.0）：

    total = max(0, 100 − A×30 − B×10 − C_deduction)

- A 类（严重幻觉）：质量闸未通过。产线内由 smart_quiz 三闸强制执行
  （未过闸的题不会产出），本模块仅对已过闸题目兜底计分。
- B 类（轻微幻觉）：输出 difficulty ≠ 期望 difficulty，扣 10。
- C 类（知识锚定偏差）：知识点名称 vs 题目文本的 BGE-M3 余弦相似度，分段扣分：
      sim < 0.15 → 10；[0.15, 0.3) → 6；[0.3, 0.5) → 3；≥ 0.5 → 0

embedding 服务不可用时 C 类 fail-open（跳过扣分、记 note），绝不阻塞出题主链路。
"""

from __future__ import annotations

import math
import uuid

import structlog

from app.providers.embedding import EmbeddingProvider

logger = structlog.get_logger(__name__)

# C 类分段扣分（与离线脚本"高度匹配版"阈值一致）
_C_BANDS = ((0.5, 0.0), (0.3, 3.0), (0.15, 6.0))
_C_BELOW = 10.0


def c_deduction(similarity: float) -> float:
    """相似度 → C 类扣分（分段，见模块 docstring）。"""
    for threshold, penalty in _C_BANDS:
        if similarity >= threshold:
            return penalty
    return _C_BELOW


def _cosine(a: list[float], b: list[float]) -> float:
    """两向量余弦相似度（向量已 L2 归一化时退化为点积）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def score_many(
    items: list[dict],
    *,
    kp_code: str,
    kp_name: str,
    expected_difficulty: str,
) -> list[dict]:
    """批量评分：一次 embedding 调用完成全部 C 类相似度计算。

    返回与 items 等长的行字典列表（可直接落 ai_quality_scores）。
    任何异常都 fail-open：对应维度跳过扣分并记 note。
    """
    rows: list[dict] = []
    n = len(items)
    if n == 0:
        return rows

    # ---- C 类：批量取向量（kp_name 一条 + 各题题干一条）----
    similarities: list[float | None] = [None] * n
    c_notes: list[str] = [""] * n
    try:
        embedder = EmbeddingProvider()
        texts = [kp_name] + [
            (item.get("question_text") or "").strip()[:512] for item in items
        ]
        vectors = await embedder.embed(texts)
        if len(vectors) == len(texts):
            kp_vec = vectors[0]
            for i in range(n):
                similarities[i] = round(_cosine(kp_vec, vectors[i + 1]), 4)
        else:
            for i in range(n):
                c_notes[i] = "embedding 返回条数异常，C 类跳过"
    except Exception as e:  # fail-open
        logger.warning("hallucination_score.embed_failed", error=str(e)[:150])
        for i in range(n):
            c_notes[i] = f"embedding 不可用（{type(e).__name__}），C 类跳过"

    # ---- 逐题计分 ----
    try:
        from app.models.question_bank import stem_hash
    except Exception:
        def stem_hash(s: str) -> str:  # 兜底：仅格式化
            return uuid.uuid5(uuid.NAMESPACE_OID, s or "").hex

    for i, item in enumerate(items):
        q_text = (item.get("question_text") or "").strip()
        out_diff = str(item.get("difficulty") or "").strip()
        q_type = str(item.get("q_type") or "").strip()
        gates_passed = bool(item.get("gates_passed", True))
        b_hit = bool(out_diff) and out_diff != expected_difficulty
        sim = similarities[i]
        c_pen = c_deduction(sim) if sim is not None else 0.0
        total = max(0.0, 100.0 - (0.0 if gates_passed else 30.0) - (10.0 if b_hit else 0.0) - c_pen)
        notes = "; ".join(x for x in [c_notes[i]] if x)[:200]
        rows.append({
            "kp_code": kp_code,
            "expected_difficulty": expected_difficulty,
            "output_difficulty": out_diff,
            "q_type": q_type,
            "question_hash": stem_hash(q_text),
            "question_text": q_text[:500],
            "gates_passed": gates_passed,
            "b_hit": b_hit,
            "c_similarity": sim,
            "c_deduction": round(c_pen, 2),
            "total_score": round(total, 2),
            "note": notes,
        })
    return rows


async def score_items(
    items: list[dict],
    *,
    kp_names: list[str],
    expected_difficulties: list[str],
    kp_codes: list[str],
) -> list[dict]:
    """逐题个性化评分（练题补缺/组卷路径：每题知识点不同，迭代18）。

    一次 embedding 调用：文本 = [kp_name_0, q0, kp_name_1, q1, ...]。
    返回与 items 等长的行字典列表（可直接落 ai_quality_scores）。
    """
    n = len(items)
    if n == 0:
        return []
    texts: list[str] = []
    for i in range(n):
        texts.append((kp_names[i] or "").strip())
        texts.append((items[i].get("question_text") or "").strip()[:512])

    similarities: list[float | None] = [None] * n
    c_notes: list[str] = [""] * n
    try:
        embedder = EmbeddingProvider()
        vectors = await embedder.embed(texts)
        if len(vectors) == len(texts):
            for i in range(n):
                similarities[i] = round(_cosine(vectors[2 * i], vectors[2 * i + 1]), 4)
        else:
            for i in range(n):
                c_notes[i] = "embedding 返回条数异常，C 类跳过"
    except Exception as e:  # fail-open
        logger.warning("hallucination_score.embed_failed", error=str(e)[:150])
        for i in range(n):
            c_notes[i] = f"embedding 不可用（{type(e).__name__}），C 类跳过"

    try:
        from app.models.question_bank import stem_hash
    except Exception:
        def stem_hash(s: str) -> str:
            return uuid.uuid5(uuid.NAMESPACE_OID, s or "").hex

    rows: list[dict] = []
    for i, item in enumerate(items):
        q_text = (item.get("question_text") or "").strip()
        out_diff = str(item.get("difficulty") or "").strip()
        q_type = str(item.get("q_type") or "").strip()
        gates_passed = bool(item.get("gates_passed", True))
        b_hit = bool(out_diff) and out_diff != expected_difficulties[i]
        sim = similarities[i]
        c_pen = c_deduction(sim) if sim is not None else 0.0
        total = max(0.0, 100.0 - (0.0 if gates_passed else 30.0) - (10.0 if b_hit else 0.0) - c_pen)
        notes = "; ".join(x for x in [c_notes[i]] if x)[:200]
        rows.append({
            "kp_code": kp_codes[i] or "",
            "expected_difficulty": expected_difficulties[i] or "",
            "output_difficulty": out_diff,
            "q_type": q_type,
            "question_hash": stem_hash(q_text),
            "question_text": q_text[:500],
            "gates_passed": gates_passed,
            "b_hit": b_hit,
            "c_similarity": sim,
            "c_deduction": round(c_pen, 2),
            "total_score": round(total, 2),
            "note": notes,
        })
    return rows


async def persist_scores(rows: list[dict], *, scene: str, request_id: str | None) -> int:
    """评分行落库（独立会话，失败只记日志，绝不影响调用方事务）。

    返回成功写入行数。
    """
    if not rows:
        return 0
    try:
        from sqlalchemy import insert

        from app.models.ai_quality_score import AIQualityScore
        from app.models.database import async_session_factory

        rid = request_id or str(uuid.uuid4())
        payload = [
            {"request_id": rid, "scene": scene, **row}
            for row in rows
        ]
        async with async_session_factory() as session:
            await session.execute(insert(AIQualityScore).values(payload))
            await session.commit()
        logger.info(
            "hallucination_score.persisted",
            scene=scene,
            request_id=rid,
            count=len(payload),
            avg_total=round(sum(r["total_score"] for r in rows) / len(rows), 2),
        )
        return len(payload)
    except Exception as e:
        logger.warning("hallucination_score.persist_failed", error=str(e)[:200])
        return 0
