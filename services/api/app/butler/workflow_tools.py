"""Butler Kernel v2 阶段 4B：7 个星辰远程工具边界（Task 9）

仅注册 7 个 xingchen.* 工具（ToolRisk.EXTERNAL），包装现有
resolve_effective_xingchen_config / run_workflow，不重写 HTTP 客户端。

明确不注册（F14 / 编排器 / 多轮主状态）：
- wf_verify_derivation / research.verify_derivation / lean.*（M2 范围外）
- wf_intent_router（意图路由由本地 Planner 管理）
- wf_socratic_chat（不拥有多轮主状态；引导式解题由本地 Tutor/Butler 管理）

输出统一包装 available/source/degraded/error_code/data，不改底层 YAML I/O。
稳定错误码：xingchen_disabled / xingchen_missing_credentials /
xingchen_missing_flow_id / xingchen_timeout / xingchen_rate_limited /
xingchen_concurrency / xingchen_invalid_json / xingchen_schema_mismatch /
xingchen_unavailable / xingchen_unknown。

本地降级优先（复用既有能力，不新写数学/OCR 引擎）：
- smart_quiz → supply_variants（question_supply 去重与质量链）
- error_analysis → classify_subtype（确定性错因分类）
- web_search → RAG 知识库先检索，仅显式开启或本地拒答时远程
- speech_to_latex → _local_spark_to_latex（本地模型降级）
- solution_pregrade → _ai_pregrade_solution（本地预评分，输出非确定性判分）
- document_understand / course_preprocess：无本地等价能力 → 明确 unavailable

安全纪律：
- handler 只用 ToolExecutionContext 的 user_id/db，不信任模型传入 user_id；
- 不 commit/rollback/开第二事务；不使用 eval/getattr 动态调用；
- 错误/日志/ToolResult/账本不含 API key、secret、Authorization、原始异常路径或完整响应；
- 搜索内容不得进入确定性判分、掌握度写入或成绩事实。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.butler.contracts import ActorRole, ToolRisk
from app.butler.executor import ToolExecutionContext
from app.butler.registry import ToolDefinition, ToolRegistry
from app.butler.tools import supply_variants
from app.providers.xingchen import (
    XingchenConcurrencyError,
    XingchenError,
    XingchenRateLimitError,
    XingchenTimeoutError,
    _flow_id_of,
    resolve_effective_xingchen_config,
    run_workflow,
)

# 稳定错误码（对外契约，不随内部实现变化）
ERROR_DISABLED = "xingchen_disabled"
ERROR_MISSING_CREDENTIALS = "xingchen_missing_credentials"
ERROR_MISSING_FLOW_ID = "xingchen_missing_flow_id"
ERROR_TIMEOUT = "xingchen_timeout"
ERROR_RATE_LIMITED = "xingchen_rate_limited"
ERROR_CONCURRENCY = "xingchen_concurrency"
ERROR_INVALID_JSON = "xingchen_invalid_json"
ERROR_SCHEMA_MISMATCH = "xingchen_schema_mismatch"
ERROR_UNAVAILABLE = "xingchen_unavailable"
ERROR_UNKNOWN = "xingchen_unknown"

# 工作流 → 工具超时（Butler 侧统一 ≤20s，受 ButlerBudget.timeout_s 总预算约束；
# provider 原有独立接口超时配置保留，不修改 YAML）
_TOOL_TIMEOUTS: dict[str, float] = {
    "wf_doc_understand": 20.0,
    "wf_speech_to_latex": 10.0,
    "wf_web_search": 20.0,
    "wf_smart_quiz": 20.0,
    "wf_solution_pregrade": 10.0,
    "wf_error_analysis": 5.0,
    "wf_course_preprocess": 20.0,
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ==================== 类型化 I/O ====================


class WorkflowToolOutput(BaseModel):
    """星辰工具统一输出包装（available/source/degraded/error_code/data）。"""

    model_config = ConfigDict(extra="forbid")

    available: bool
    source: str  # xingchen | local | none
    degraded: bool
    error_code: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class DocumentUnderstandOutput(WorkflowToolOutput):
    pass


class SpeechToLatexOutput(WorkflowToolOutput):
    pass


class WebSearchOutput(WorkflowToolOutput):
    pass


class SmartQuizOutput(WorkflowToolOutput):
    pass


class SolutionPregradeOutput(WorkflowToolOutput):
    pass


class ErrorAnalysisOutput(WorkflowToolOutput):
    pass


class CoursePreprocessOutput(WorkflowToolOutput):
    pass


class DocumentUnderstandInput(BaseModel):
    image_url: str = Field(min_length=1, max_length=4096)
    task: str = Field(pattern="^(extract_question|describe_figure)$")
    grade_hint: str | None = Field(default=None, max_length=64)


class SpeechToLatexInput(BaseModel):
    asr_text: str = Field(min_length=1, max_length=500)
    context_kp: str | None = Field(default=None, max_length=64)


class WebSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    max_results: int = Field(default=5, ge=1, le=10)


class SmartQuizInput(BaseModel):
    kp_name: str = Field(min_length=1, max_length=64)
    kp_code: str = Field(min_length=1, max_length=64)
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")
    q_type: str | None = Field(default=None, pattern="^(choice|blank|solution)$")


class SolutionPregradeInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    reference: str = Field(min_length=1, max_length=2000)
    student_answer: str = Field(min_length=1, max_length=2000)
    max_score: float = Field(default=10.0, gt=0, le=100)


class ErrorAnalysisInput(BaseModel):
    question_text: str = Field(min_length=1, max_length=2000)
    answer_text: str = Field(default="", max_length=2000)
    student_answer: str = Field(min_length=1, max_length=2000)
    context_kp: str | None = Field(default=None, max_length=64)


class CoursePreprocessInput(BaseModel):
    transcript: str = Field(min_length=1, max_length=30000)
    course_title: str | None = Field(default=None, max_length=200)
    kp_hint: list[str] = Field(default_factory=list, max_length=50)


# ==================== 统一远程执行 ====================


async def _run_remote_tool(
    context: ToolExecutionContext,
    *,
    flow: str,
    parameters: dict[str, Any],
    validated_input: dict[str, Any],
    local_fallback: Callable[[ToolExecutionContext, dict[str, Any]], Awaitable[dict[str, Any]]]
    | None = None,
) -> dict[str, Any]:
    """统一远程工具执行：解析配置 → 可用性检查 → run_workflow → 错误映射 → 本地降级。

    返回 dict 恒为 Schema 合法包装（available/source/degraded/error_code/data），
    不抛异常；错误码稳定，不含密钥/原始异常路径/完整响应。
    所有失败路径（disabled/缺凭证/缺 flow_id/超时/限流/并发/无效 JSON/不可用/未知）
    均传入 validated_input，有本地等价能力即降级。
    """
    user_id = str(context.request.actor.user_id)
    cfg = await resolve_effective_xingchen_config(context.db, user_id)

    if not cfg.enabled:
        return await _fallback_or_unavailable(local_fallback, context, validated_input, ERROR_DISABLED)
    if not cfg.api_key or not cfg.api_secret:
        return await _fallback_or_unavailable(
            local_fallback, context, validated_input, ERROR_MISSING_CREDENTIALS
        )
    if not _flow_id_of(cfg, flow):
        return await _fallback_or_unavailable(
            local_fallback, context, validated_input, ERROR_MISSING_FLOW_ID
        )

    try:
        result = await run_workflow(flow, uid=user_id, parameters=parameters, config=cfg)
    except XingchenTimeoutError:
        return await _fallback_or_unavailable(local_fallback, context, validated_input, ERROR_TIMEOUT)
    except XingchenRateLimitError:
        return await _fallback_or_unavailable(
            local_fallback, context, validated_input, ERROR_RATE_LIMITED
        )
    except XingchenConcurrencyError:
        return await _fallback_or_unavailable(
            local_fallback, context, validated_input, ERROR_CONCURRENCY
        )
    except XingchenError as e:
        if e.code == -2:
            code = ERROR_SCHEMA_MISMATCH if "schema" in str(e) else ERROR_INVALID_JSON
            return await _fallback_or_unavailable(local_fallback, context, validated_input, code)
        return await _fallback_or_unavailable(
            local_fallback, context, validated_input, ERROR_UNAVAILABLE
        )
    except RuntimeError as e:
        if "flow_id" in str(e):
            return await _fallback_or_unavailable(
                local_fallback, context, validated_input, ERROR_MISSING_FLOW_ID
            )
        return await _fallback_or_unavailable(
            local_fallback, context, validated_input, ERROR_UNAVAILABLE
        )
    except Exception:  # noqa: BLE001 —— provider 未知异常 → 稳定 unknown
        return await _fallback_or_unavailable(local_fallback, context, validated_input, ERROR_UNKNOWN)

    return {
        "available": True,
        "source": "xingchen",
        "degraded": False,
        "error_code": None,
        "data": result,
    }


async def _fallback_or_unavailable(
    local_fallback: Callable[[ToolExecutionContext, dict[str, Any]], Awaitable[dict[str, Any]]]
    | None,
    context: ToolExecutionContext,
    validated_input: dict[str, Any],
    error_code: str,
) -> dict[str, Any]:
    """本地降级成功 → source=local；无等价能力或降级也失败 → 明确 unavailable。"""
    if local_fallback is not None:
        try:
            data = await local_fallback(context, validated_input)
            return {
                "available": True,
                "source": "local",
                "degraded": True,
                "error_code": error_code,
                "data": data,
            }
        except Exception:  # noqa: BLE001 —— 本地降级失败 → unavailable，不泄漏细节
            return {
                "available": False,
                "source": "none",
                "degraded": True,
                "error_code": error_code,
                "data": {},
            }
    return {
        "available": False,
        "source": "none",
        "degraded": True,
        "error_code": error_code,
        "data": {},
    }


# ==================== 本地降级（复用既有能力） ====================


async def _local_smart_quiz(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    """本地降级：question_supply 题库变式（不自行生成数学事实）。"""
    if context.db is None:
        raise RuntimeError("db unavailable for local variants")
    items = await supply_variants(
        context.db,
        context.request.actor.user_id,
        validated_input["kp_code"],
        (validated_input["difficulty"],),
    )
    if not items:
        raise RuntimeError("no local variants")
    return {"items": items, "degraded_from": "xingchen"}


async def _local_error_analysis(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    """本地降级：确定性错因分类（classify_subtype 规则，无 LLM）。"""
    from app.services.growth import classify_subtype

    subtype, subtype_zh, parent = classify_subtype(None, validated_input["student_answer"])
    return {
        "error_type": subtype,
        "kp_code": validated_input.get("context_kp"),
        "confidence": 0.0,
        "subtype_zh": subtype_zh,
    }


async def _local_speech_to_latex(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    """本地降级：本地模型 LaTeX 转换（_local_spark_to_latex）。"""
    from app.gateway.speech_router import _local_spark_to_latex

    latex = await _local_spark_to_latex(
        validated_input["asr_text"], validated_input.get("context_kp")
    )
    if not latex:
        raise RuntimeError("local latex unavailable")
    return {"latex": latex, "normalized_text": validated_input["asr_text"], "ambiguous": False}


async def _local_solution_pregrade(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    """本地降级：复用 _ai_pregrade_solution（本地预评分，输出非确定性判分）。

    allow_xingchen=False：降级链不得再次进入星辰（远程总调用次数严格 = 1）。
    """
    from app.gateway.student_router import _ai_pregrade_solution

    verdict, score, extra = await _ai_pregrade_solution(
        None,
        validated_input["student_answer"],
        user_id=str(context.request.actor.user_id),
        db=context.db,
        question_text=validated_input["question"],
        expected_answer=validated_input["reference"],
        max_score=validated_input["max_score"],
        allow_xingchen=False,
    )
    return {
        "verdict": verdict,
        "score": score,
        "max_score": validated_input["max_score"],
        "ai_pregraded": bool(extra.get("ai_pregraded")),
        "comment": str(extra.get("comment") or "")[:500],
        "error_type": extra.get("error_type"),
        "degraded": extra.get("degraded"),
    }


async def _local_kb_search(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any] | None:
    """本地知识库检索（RAG）。db 缺失/异常/无结果 → None 或 {answerable:False}。"""
    db = context.db
    if db is None:
        return None
    try:
        from app.kernel.rag import RAGPipeline

        result = await RAGPipeline().retrieve(
            validated_input["query"], db=db, mode="hybrid", request_id="butler-web-search"
        )
        if result.answerable and result.chunks:
            chunks = result.chunks[: validated_input["max_results"]]
            sources = [
                {
                    "title": (c.doc_title or "本地知识库")[:100],
                    "url": "",
                    "snippet": c.content[:300],
                    "retrieved_at": _now_iso(),
                }
                for c in chunks
            ]
            answer = "\n".join(c.content[:300] for c in chunks)
            return {
                "answerable": True,
                "data": {"answer": answer, "sources": sources, "badge": "local_kb"},
            }
        return {
            "answerable": False,
            "refuse_reason": result.refuse_reason or "本地知识库未检索到相关内容",
        }
    except Exception:  # noqa: BLE001 —— 本地检索失败视为拒答，允许远程
        return None


def _normalize_sources(raw: Any, limit: int = 5) -> list[dict[str, str]]:
    """来源规范化：每项严格含 title/url/snippet/retrieved_at，长度限制 + URL 校验。"""
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "")[:100]
        url = str(item.get("url") or item.get("link") or "")[:500]
        snippet = str(item.get("snippet") or item.get("content") or "")[:300]
        if not url.startswith(("http://", "https://")):
            url = ""
        out.append({"title": title, "url": url, "snippet": snippet, "retrieved_at": _now_iso()})
    return out


# ==================== handlers（显式函数，不使用 eval/getattr） ====================


async def _h_document_understand(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    # 无本地等价能力（本地 OCR 需 File 对象，非 image_url）：远程失败 → 明确 unavailable
    return await _run_remote_tool(
        context,
        flow="wf_doc_understand",
        parameters={
            "AGENT_USER_INPUT": context.request.message[:200],
            "image_url": validated_input["image_url"],
            "task": validated_input["task"],
            "grade_hint": validated_input.get("grade_hint") or "",
        },
        validated_input=validated_input,
        local_fallback=None,
    )


async def _h_speech_to_latex(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    return await _run_remote_tool(
        context,
        flow="wf_speech_to_latex",
        parameters={
            "AGENT_USER_INPUT": validated_input["asr_text"],
            "asr_text": validated_input["asr_text"],
            "context_kp": validated_input.get("context_kp") or "",
        },
        validated_input=validated_input,
        local_fallback=_local_speech_to_latex,
    )


async def _h_web_search(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    # 1. 本地知识库先检索（受信任 local-first 路径：任何请求都先走本地）
    local = await _local_kb_search(context, validated_input)
    local_refused = local is None or not local.get("answerable")
    if not local_refused:
        return {
            "available": True,
            "source": "local",
            "degraded": False,
            "error_code": None,
            "data": local["data"],
        }
    # 2. 本地真实拒答/不可用（运行事实）→ fail-closed 检查类型化授权上下文：
    #    - auth 缺失（None，旧调用路径）→ 拒绝远程（无授权上下文一律不放行）；
    #    - 全局能力关闭 → 拒绝远程（服务端能力是硬前置）；
    #    - 用户本次未 opt-in → 拒绝远程（单条请求授权；即使全局开启也不放行）；
    #    - 只有 auth 存在、global_enabled=true、user_opt_in=true、且本次真实本地拒答
    #      后才允许远程。local_refused 是本次运行事实，任何全局配置都不能伪造它。
    auth = context.web_search_auth
    if auth is None or not auth.global_enabled or not auth.user_opt_in:
        refuse = (local or {}).get("refuse_reason") or "本地知识库未检索到相关内容"
        return {
            "available": False,
            "source": "none",
            "degraded": True,
            "error_code": "confirmation_required",
            "data": {"refuse_reason": refuse},
        }
    # 3. 远程搜索（本地真实拒答后）
    remote = await _run_remote_tool(
        context,
        flow="wf_web_search",
        parameters={
            "query": validated_input["query"],
            "max_results": validated_input["max_results"],
        },
        validated_input=validated_input,
        local_fallback=None,
    )
    if remote["available"]:
        data = dict(remote["data"])
        data["sources"] = _normalize_sources(
            data.get("sources"), validated_input["max_results"]
        )
        return {**remote, "data": data}
    # 4. 远程失败 → 可解释降级（保留本地拒答原因，不影响本地回答）
    refuse = (local or {}).get("refuse_reason") or "本地知识库未检索到相关内容"
    return {
        "available": False,
        "source": "none",
        "degraded": True,
        "error_code": remote["error_code"],
        "data": {"refuse_reason": refuse},
    }


async def _h_smart_quiz(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    return await _run_remote_tool(
        context,
        flow="wf_smart_quiz",
        parameters={
            "AGENT_USER_INPUT": context.request.message[:200],
            "kp_name": validated_input["kp_name"],
            "kp_code": validated_input["kp_code"],
            "difficulty": validated_input["difficulty"],
            "q_type": validated_input.get("q_type") or "",
        },
        validated_input=validated_input,
        local_fallback=_local_smart_quiz,
    )


async def _h_solution_pregrade(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    return await _run_remote_tool(
        context,
        flow="wf_solution_pregrade",
        parameters={
            "AGENT_USER_INPUT": "请批改这道解答题",
            "question": validated_input["question"],
            "reference": validated_input["reference"],
            "student_answer": validated_input["student_answer"],
            "max_score": str(int(validated_input["max_score"])),
        },
        validated_input=validated_input,
        local_fallback=_local_solution_pregrade,
    )


async def _h_error_analysis(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    return await _run_remote_tool(
        context,
        flow="wf_error_analysis",
        parameters={
            "AGENT_USER_INPUT": context.request.message[:200],
            "question_text": validated_input["question_text"],
            "answer_text": validated_input["answer_text"],
            "student_answer": validated_input["student_answer"],
            "context_kp": validated_input.get("context_kp") or "",
        },
        validated_input=validated_input,
        local_fallback=_local_error_analysis,
    )


async def _h_course_preprocess(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    # 无现有本地预处理能力：远程失败 → 明确 unavailable，不伪造章节/知识点
    return await _run_remote_tool(
        context,
        flow="wf_course_preprocess",
        parameters={
            "AGENT_USER_INPUT": context.request.message[:200],
            "transcript": validated_input["transcript"],
            "course_title": validated_input.get("course_title") or "",
            "kp_hint": validated_input.get("kp_hint") or [],
        },
        validated_input=validated_input,
        local_fallback=None,
    )


# ==================== 注册 ====================

_STUDENT_ONLY = frozenset({ActorRole.STUDENT})

_WORKFLOW_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="xingchen.document_understand",
        version="1.0.0",
        description="拍照题图片理解（印刷体/手写/几何图形）：提取题干文字与 LaTeX 公式",
        input_model=DocumentUnderstandInput,
        output_model=DocumentUnderstandOutput,
        risk=ToolRisk.EXTERNAL,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.practice", "student.errors"}),
        timeout_s=_TOOL_TIMEOUTS["wf_doc_understand"],
        handler=_h_document_understand,
    ),
    ToolDefinition(
        name="xingchen.speech_to_latex",
        version="1.0.0",
        description="口语转 LaTeX 公式（本地模型降级可用）",
        input_model=SpeechToLatexInput,
        output_model=SpeechToLatexOutput,
        risk=ToolRisk.EXTERNAL,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.practice", "student.errors"}),
        timeout_s=_TOOL_TIMEOUTS["wf_speech_to_latex"],
        handler=_h_speech_to_latex,
    ),
    ToolDefinition(
        name="xingchen.web_search",
        version="1.0.0",
        description="联网搜索（本地知识库先检索；仅显式开启或本地拒答时远程）",
        input_model=WebSearchInput,
        output_model=WebSearchOutput,
        risk=ToolRisk.EXTERNAL,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset(
            {"student.dashboard", "student.practice", "student.review", "student.errors", "student.chat"}
        ),
        timeout_s=_TOOL_TIMEOUTS["wf_web_search"],
        handler=_h_web_search,
    ),
    ToolDefinition(
        name="xingchen.smart_quiz",
        version="1.0.0",
        description="按知识点/难度生成智能题（远程优先，question_supply 题库变式本地降级）",
        input_model=SmartQuizInput,
        output_model=SmartQuizOutput,
        risk=ToolRisk.EXTERNAL,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.practice"}),
        timeout_s=_TOOL_TIMEOUTS["wf_smart_quiz"],
        handler=_h_smart_quiz,
    ),
    ToolDefinition(
        name="xingchen.solution_pregrade",
        version="1.0.0",
        description="解答题 AI 初批（非确定性判分，留痕待教师确认）",
        input_model=SolutionPregradeInput,
        output_model=SolutionPregradeOutput,
        risk=ToolRisk.EXTERNAL,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.practice", "student.errors"}),
        timeout_s=_TOOL_TIMEOUTS["wf_solution_pregrade"],
        handler=_h_solution_pregrade,
    ),
    ToolDefinition(
        name="xingchen.error_analysis",
        version="1.0.0",
        description="错因分析（远程优先，确定性错因分类本地降级）",
        input_model=ErrorAnalysisInput,
        output_model=ErrorAnalysisOutput,
        risk=ToolRisk.EXTERNAL,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.errors", "student.practice"}),
        timeout_s=_TOOL_TIMEOUTS["wf_error_analysis"],
        handler=_h_error_analysis,
    ),
    ToolDefinition(
        name="xingchen.course_preprocess",
        version="1.0.0",
        description="课程预处理（章节/知识点锚定；无本地等价能力，远程失败返回 unavailable）",
        input_model=CoursePreprocessInput,
        output_model=CoursePreprocessOutput,
        risk=ToolRisk.EXTERNAL,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.tasks", "student.practice"}),
        timeout_s=_TOOL_TIMEOUTS["wf_course_preprocess"],
        handler=_h_course_preprocess,
    ),
)


def register_workflow_tools(registry: ToolRegistry) -> None:
    """显式注册 7 个星辰远程工具到指定 registry（阶段 4.1 统一工厂组合用）。"""
    for definition in _WORKFLOW_TOOL_DEFINITIONS:
        registry.register(definition)


def build_workflow_registry() -> ToolRegistry:
    """注册 7 个星辰远程工具（阶段 4B）：全部 EXTERNAL，学生可见，F14/lean.* 由注册层拒绝。"""
    reg = ToolRegistry()
    register_workflow_tools(reg)
    return reg
