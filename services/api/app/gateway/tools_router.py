"""工具门面路由（SSOT §5.9 / X-Tool-Key 鉴权）

端点：
- POST /tools/verify/run — SymPy 沙箱执行（星辰回调 + 本地 Skill 共用）
- POST /tools/retrieve — 知识库检索扩展（mode 参数）
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.config import settings
from app.providers.sandbox import run_sandbox

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])


class VerifyRunRequest(BaseModel):
    code: str = Field(..., max_length=4000)
    timeout_ms: int = Field(default=10000, le=10000)


class RetrieveRequest(BaseModel):
    query: str
    mode: str = "hybrid"  # hybrid/vector/fulltext/kp
    content_type: str | None = None
    kp_codes: list[str] | None = None
    top_k: int = Field(default=4, le=10)


def _check_tool_key(x_tool_key: str) -> dict | None:
    """X-Tool-Key 校验，失败返回错误响应（常数时间比较，防时序侧信道）"""
    import hmac

    if not settings.tool_api_key:
        return {"code": 50301, "message": "工具密钥未配置（TOOL_API_KEY）"}
    if not hmac.compare_digest(x_tool_key or "", settings.tool_api_key):
        return {"code": 40301, "message": "无效的工具密钥"}
    return None


@router.post("/verify/run")
async def verify_run(
    req: VerifyRunRequest,
    x_tool_key: str = Header(default=""),
):
    """SymPy 沙箱执行（SSOT §5.9）

    星辰自定义托管插件回调入口 + 本地 Skill 共用。
    限时/限内存/禁网。
    """
    # 鉴权
    err = _check_tool_key(x_tool_key)
    if err:
        return err

    result = await run_sandbox(req.code, timeout_ms=req.timeout_ms)

    return {
        "code": 0,
        "data": {
            "exec_status": result["exec_status"],
            "stdout": result["stdout"],
            "result_repr": result["result_repr"],
            "error": result["error"],
        },
    }


@router.post("/retrieve")
async def tools_retrieve(
    req: RetrieveRequest,
    x_tool_key: str = Header(default=""),
):
    """知识库检索扩展（SSOT §5.9 统一门面）

    本地 Skill 与星辰工作流共用。
    mode: hybrid（三路全开）/ vector / fulltext / kp（单路调试）
    content_type/kp_codes 元数据过滤（迭代05 接线：先过滤再向量匹配）。
    """
    # 鉴权
    err = _check_tool_key(x_tool_key)
    if err:
        return err

    # 调用 kernel/rag.py（复用 M1 既有管线；RAGPipeline 无参，db 走 retrieve 关键字参数）
    try:
        from app.kernel.rag import get_rag_pipeline
        from app.models.database import async_session_factory

        async with async_session_factory() as db:
            result = await get_rag_pipeline().retrieve(
                req.query,
                db=db,
                mode=req.mode,
                content_type=req.content_type,
                kp_codes=req.kp_codes,
            )

        chunks = [
            {
                "chunk_id": c.chunk_id,
                "content": c.content[:500],
                "score": c.score,
                "raw_score": c.raw_score,
                "kp_codes": c.kp_ids,
                "doc_title": c.doc_title,
            }
            for c in result.chunks[: req.top_k]
        ]

        return {
            "code": 0,
            "data": {
                "chunks": chunks,
                "answerable": result.answerable,
                "gate": {
                    "top1_score": result.chunks[0].score if result.chunks else 0.0,
                    "threshold": settings.rag_refuse_threshold,
                },
            },
        }

    except Exception as e:
        logger.error("tools_retrieve_failed", error=str(e))
        return {"code": 50001, "message": f"检索失败: {str(e)[:100]}"}


# ==================== GET /tools/bank/supply（题库只读供给，出题兜底链 D7 同源） ====================


@router.get("/bank/supply")
async def bank_supply(
    kp_codes: str = "",
    q_type: str | None = None,
    difficulty: str | None = None,
    count: int = 3,
    x_tool_key: str = Header(default=""),
):
    """按知识点从 question_bank 供题（只读，零副作用）。

    复用 supply_questions 的全部治理过滤（scope=student/排除竞赛超纲/kp GIN 重叠/
    难度放宽/hash 环回轮换）。供 B1 出题兜底链（ADR-11）：AI 五闸拦截后题库补位，
    真题天然带 verified 语义，响应绝不伪装成 AI 生成。
    """
    err = _check_tool_key(x_tool_key)
    if err:
        return err
    from app.models.database import get_db  # 延迟导入避免循环
    from app.skills.question_supply import supply_questions

    kps = [k.strip() for k in (kp_codes or "").split(",") if k.strip()]
    agen = get_db()
    db = await agen.__anext__()
    try:
        rows = await supply_questions(
            db, kp_codes=kps, q_type=q_type or None, difficulty=difficulty or None,
            count=max(1, min(int(count), 10)),
        )
    finally:
        await agen.aclose()
    items = [
        {
            "id": str(r.id),
            "stem_text": r.stem,
            "q_type": r.q_type,
            "options": r.options,
            "answer": r.answer,
            "explanation": r.analysis,
            "difficulty": r.difficulty,
            "kp_codes": list(r.kp_codes or []),
            "is_real_exam": bool(r.is_real_exam),
            "year": r.year,
            "image": r.image,
            "hash": r.hash,
        }
        for r in rows
    ]
    return {"code": 0, "message": "ok", "data": {"items": items, "total": len(items)}}
