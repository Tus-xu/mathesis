"""RV-WB · 论文工作台「自研内核」服务层。

三个自研能力，**各接一个真实内核**（不新造第二套调用）：

- `latex_editing`      → LLM 网关（`core/llm/gateway.py`）+ TeX 前置安全校验
                          （复用 `core/xf/latex.py::_validate_tex_source`）；
- `method_rag`         → 文献内核（`core/literature/crossref.py::CrossrefProvider`）
                          真实检索 + **可选** LLM 生成方法说明（如实标注 ai 字段）；
- `academic_translate` → LLM 网关（**同一次调用**产出段级对照，不追加第二次请求）。

外加 `capabilities()`（`GET /wb/capabilities` 数据源）。`writing_polish` 不进本层
—— 它仍由讯飞星辰线上工作流承载（见 `models.CAPABILITIES`）。

诚实降级铁律（体现在**值**上，不是缺键上）
------------------------------------------
- LLM 未配置 / 超时 / 输出不可用 → 明确错误码（503/504/502），**不伪造回答**；
- 文献内核失败 → `WB_LITERATURE_UNAVAILABLE`（503），**不用模型编造文献顶替**；
- `method:search` 的 LLM 生成字段不可用时 → 字段留空 + `warning`，**整体仍 200**
  并返回真实 `sources`（检索不受 LLM 影响）；
- LaTeX 源码（用户入参**与**模型输出）任一未过安全校验 → 拒绝：
  **入参命中守卫 → 绝不调用模型**，`<1s` 返回 `WB_TEX_SOURCE_INVALID`(400)；
  模型输出命中 → `WB_LLM_BAD_OUTPUT`(502)。
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import uuid
from collections import Counter
from typing import Any, Protocol

from ..literature.provider import LiteratureProviderError
from ..llm import gateway
from ..xf.latex import XF_TEX_MAX_SOURCE_BYTES, _validate_tex_source
from ..xf.models import XfError
from .models import (
    CAPABILITIES,
    XF_FLOW_ENV_FOR_WRITING_POLISH,
    WbBadRequest,
    WbError,
    WbLlmBadOutput,
    WbLlmError,
    WbLlmNotConfigured,
    WbLlmTimeout,
    WbLiteratureUnavailable,
    WbTexSourceInvalid,
    wb_llm_timeout_s,
)

logger = logging.getLogger(__name__)

#: 单次检索返回的条目上限（与文献 provider 的 rows 归一，防滥用）。
WB_METHOD_MAX_RESULTS = 20

#: 段级对照缺失时的诚实提示（宁可空 + 提示，也不给错位对照）。
BILINGUAL_UNAVAILABLE_WARNING = "未能生成段级对照，仅提供整段译文"


# ==========================================================================
# capabilities
# ==========================================================================


def _xingchen_writing_polish_available() -> bool:
    """`writing_polish` 是否可用（讯飞 flow 已配置 + 凭据齐备 + 总开关开启）。

    读 `RESEARCH_XF_*`（复用 `core/xf/models.load_settings`），
    **不触网**、**不回显任何凭据原文**。
    """
    try:
        from ..xf.models import load_settings

        cfg = load_settings()
        return bool(
            cfg.enabled
            and cfg.has_credentials
            and cfg.flow_id(XF_FLOW_ENV_FOR_WRITING_POLISH)
        )
    except Exception as e:  # noqa: BLE001 - 元信息端点绝不因读取失败而 5xx
        logger.warning("wb_xf_capability_probe_failed err=%s", type(e).__name__)
        return False


def capabilities() -> dict[str, Any]:
    """论文工作台能力清单（引擎归属 / 可用性 / 端点 / 入参 schema / 响应键）。

    **不含任何凭证**：只暴露布尔可用性与（非敏感）模型名。
    新增 `requestSchema` / `responseKeys` 让前端**数据驱动**，不必手抄字段名。
    """
    llm_ok = gateway.llm_configured()
    xf_ok = _xingchen_writing_polish_available()

    items: list[dict[str, Any]] = []
    for cap in CAPABILITIES:
        if cap.engine == "xingchen":
            available = xf_ok
        elif cap.kernel in ("llm", "llm+tex-guard"):
            available = llm_ok
        else:  # literature：可用性取决于 provider 导入与网络，此处乐观声明为 True
            available = True
        items.append(
            {
                "key": cap.key,
                "title": cap.title,
                "engine": cap.engine,
                "kernel": cap.kernel,
                "method": cap.method,
                "path": cap.path,
                "description": cap.description,
                "available": available,
                "requestSchema": dict(cap.request_schema),
                "responseKeys": list(cap.response_keys),
            }
        )

    return {
        "items": items,
        "llm": {
            "configured": llm_ok,
            "model": gateway.llm_model(),
            "modelLong": gateway.llm_model_long(),
        },
        "literature": {
            "provider": "crossref",
            "available": True,
            "note": "metadata-only；abstract ≠ full text；检索结果非模型生成",
        },
        "xb": {
            "enabled": xf_ok,
            "keptWorkflow": "writing_polish",
            "note": "仅 writing_polish（wf_writing_polish_V2）保留讯飞线上工作流",
        },
    }


# ==========================================================================
# LLM 调用（统一入口，错误码映射）
# ==========================================================================


def _map_llm_error(e: gateway.LlmGateError) -> WbError:
    """把网关的 `LlmGateError` 映射成稳定 WB 错误码。"""
    code = (getattr(e, "code", "") or "").upper()
    message = getattr(e, "message", None) or str(e)
    if code == "LLM_NOT_CONFIGURED":
        return WbLlmNotConfigured(message)
    if "TIMEOUT" in code or "timeout" in message.lower():
        return WbLlmTimeout(message)
    if code in ("LLM_EMPTY_CHOICES",):
        return WbLlmBadOutput(message)
    return WbLlmError(message)


async def _complete(
    messages: list[dict[str, Any]], *, model: str | None = None
) -> gateway.Completion:
    """经 LLM 网关完成一次补全（**硬性**墙钟上限，超时→`WB_LLM_TIMEOUT`）。

    Raises:
        WbLlmNotConfigured: 凭据缺失；
        WbLlmTimeout: 超过 `wb_llm_timeout_s()`；
        WbLlmBadOutput: 返回空正文；
        WbLlmError: 其它网关失败。
    """
    if not gateway.llm_configured():
        raise WbLlmNotConfigured(
            "未配置 LLM 凭据（RESEARCH_LLM_API_KEY / SPARK_API_PASSWORD）：模型不可用"
        )

    backend = gateway.get_backend()
    try:
        completion = await asyncio.wait_for(
            backend.acompletion(model=model, messages=messages, stream=False),
            timeout=wb_llm_timeout_s(),
        )
    except asyncio.TimeoutError as e:
        raise WbLlmTimeout(f"LLM 调用超时（>{wb_llm_timeout_s():.0f}s）") from e
    except gateway.LlmGateError as e:
        raise _map_llm_error(e) from e
    except WbError:
        raise
    except Exception as e:  # noqa: BLE001 - 未预期异常也要有稳定错误码
        raise WbLlmError(f"LLM 调用失败（{type(e).__name__}）") from e

    if not (completion.text or "").strip():
        raise WbLlmBadOutput("模型返回空输出")
    return completion


#: 合法 JSON 转义之外的单反斜杠（如 LaTeX `\ge`）。模型在 JSON 字符串里写未转义
#: 的 LaTeX 时，`json.loads` 会因**非法转义**直接抛错 —— 这正是"模型给了严格 JSON、
#: 我们却按非 JSON 处理"的真机根因。
_INVALID_JSON_ESCAPE_RE = re.compile(r'\\(?!["\\/bfnrtu])')


def _repair_json_escapes(s: str) -> str:
    """把非法 JSON 转义的单反斜杠补成双反斜杠（容忍模型输出的 LaTeX）。

    例：`"$x \\ge 0$"` 里的 `\\g` 不是合法 JSON 转义 → 补成 `\\\\g` 后可正常解析。
    合法转义（`\\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX`）原样保留。
    """
    return _INVALID_JSON_ESCAPE_RE.sub(lambda _m: "\\\\", s)


def _json_candidates(body: str) -> list[str]:
    """给出若干「可能是 JSON 对象」的候选切片（整串 / 首个 `{` 到末个 `}`）。"""
    out = [body]
    i, j = body.find("{"), body.rfind("}")
    if i != -1 and j > i:
        sliced = body[i : j + 1]
        if sliced != body:
            out.append(sliced)
    return out


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出中**宽容**地抽取一个 JSON 对象；失败返回 None。

    依次尝试：① 直接 `json.loads`；② 剥掉 ```json 围栏；③ 取首个 `{` 到末个 `}`；
    并在每种候选上额外尝试**修复非法转义**（LaTeX 反斜杠）后的版本。
    """
    body = (text or "").strip()
    fences = re.search(r"```[^\n]*\r?\n(.*?)```", body, re.DOTALL)
    if fences is not None:
        body = fences.group(1).strip()
    for cand in _json_candidates(body):
        for variant in (cand, _repair_json_escapes(cand)):
            try:
                obj = json.loads(variant)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(obj, dict):
                return obj
    return None


# ==========================================================================
# LaTeX 编辑
# ==========================================================================

_LATEX_SYSTEM_PROMPT = (
    "你是严谨的学术 LaTeX 编辑助手。只输出编辑后的完整 LaTeX 源码，"
    "不要解释、不要 Markdown 代码围栏、不要前后缀文字。"
    "保持原有数学公式、宏包与结构不变，仅按用户指令做必要修改。"
    "严禁插入任何文件读写或命令执行类控制序列（如 \\input、\\include、"
    "\\openin、\\openout、\\write、\\catcode、\\csname、\\directlua 等）。"
)

_LATEX_USER_TEMPLATE = (
    "【编辑指令】\n__INSTRUCTION__\n\n"
    "【待编辑的 LaTeX 源码】\n__SOURCE__\n\n"
    "请直接输出编辑后的完整 LaTeX 源码。"
)


def _extract_latex(text: str) -> str:
    """从模型输出里抽取 LaTeX 正文（剥掉解释与代码围栏）。"""
    body = text or ""
    m = re.search(r"```(?:latex|tex)?[ \t]*\r?\n(.*?)```", body, re.DOTALL | re.IGNORECASE)
    if m is not None:
        return m.group(1).strip()
    stripped = body.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        inner = stripped[3:-3]
        first_nl = inner.find("\n")
        if first_nl != -1 and inner[:first_nl].strip().lower() in ("", "latex", "tex"):
            inner = inner[first_nl + 1 :]
        return inner.strip()
    return stripped


def _safe_filename(filename: str) -> str:
    """仅用于回显的展示名：去路径语义，防路径注入。"""
    base = str(filename or "main.tex").replace("\\", "/").split("/")[-1]
    base = "".join(c for c in base if c.isalnum() or c in "._-") or "main.tex"
    return base[:128]


def _guard_free_text(value: str, *, field: str) -> None:
    """对所有**自由文本入参**执行同一 TeX 安全校验（fail-closed，400）。

    **为什么不能只校验源码**：危险控制序列可出现在**任何**自由文本里，
    最常见的是 `instruction`（编辑指令）。历史缺陷：只校验 `file_content`，
    于是"源码干净、但指令里带 `\\write18`"的请求**放行到模型**（真机实测被
    调用 33.86s / 打到 60s 超时）。命中即抛，**绝不调用模型**。

    调用方必须在本函数之后才触碰 LLM。

    Args:
        value: 待校验的自由文本（空串视为通过）。
        field: 出错时回显的字段名（便于定位是哪一项命中）。
    """
    if not (value or "").strip():
        return
    try:
        _validate_tex_source(value)
    except XfError as e:
        raise WbTexSourceInvalid(f"字段 {field}：{e.message}") from e


def _guard_tex_model_output(source: str) -> None:
    """模型输出 LaTeX 安全校验（fail-closed → `WB_LLM_BAD_OUTPUT` 502）。"""
    try:
        _validate_tex_source(source)
    except XfError as e:
        raise WbLlmBadOutput(
            f"模型输出的 LaTeX 未通过安全校验：{e.message}"
        ) from e


def _describe_change(tag: str, i1: int, i2: int, j1: int, j2: int) -> str:
    span = f"第 {i1 + 1}-{i2} 行" if i2 > i1 else f"第 {i1 + 1} 行后"
    if tag == "replace":
        return f"{span}：内容修改（{i2 - i1} → {j2 - j1} 行）"
    if tag == "delete":
        return f"{span}：删除 {i2 - i1} 行"
    return f"{span}：新增 {j2 - j1} 行"


def _diff_changes(old: str, new: str, filename: str) -> list[dict[str, Any]]:
    """对 in/out 做**真实**行级 diff，返回改动块列表（无改动则 `[]`）。

    每条：`{file, old_lines, new_lines, description}` —— `old_lines`/`new_lines` 是
    **字符串**（多行用 `\\n` 连接），与前端渲染契约（逐键对齐 `/xf`）一致；
    若前端需要行数组，用**额外的** `old_lines_list`/`new_lines_list`（不改主键类型，
    否则前端两套渲染分支会打架）。
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    if old_lines == new_lines:
        return []
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changes: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old_slice = list(old_lines[i1:i2])
        new_slice = list(new_lines[j1:j2])
        changes.append(
            {
                "file": filename,
                "old_lines": "\n".join(old_slice),
                "new_lines": "\n".join(new_slice),
                "old_lines_list": old_slice,
                "new_lines_list": new_slice,
                "description": _describe_change(tag, i1, i2, j1, j2),
            }
        )
    return changes


async def edit_latex(
    source: str,
    instruction: str,
    *,
    filename: str = "main.tex",
    model: str | None = None,
) -> dict[str, Any]:
    """用 LLM 网关编辑 LaTeX 片段（入参**与**模型输出均过 TeX 安全校验）。

    **顺序保证**：入参守卫在**任何** LLM 调用之前；命中即抛，模型**绝不会**被调用。

    Returns:
        `{success, changes, final_updated_code, security_msg, filename, instruction,
        source, model, latencyMs, engines}`。

    Raises:
        WbBadRequest / WbTexSourceInvalid / WbLlmBadOutput / 其它 `WbError`。
    """
    text = source or ""
    if not text.strip():
        raise WbTexSourceInvalid("LaTeX 源码为空")
    if len(text.encode("utf-8")) > XF_TEX_MAX_SOURCE_BYTES:
        raise WbTexSourceInvalid(f"LaTeX 源码超过上限 {XF_TEX_MAX_SOURCE_BYTES} 字节")
    if not (instruction or "").strip():
        raise WbBadRequest("缺少 instruction（编辑指令）")

    # 纵深防御第一层（对**所有自由文本入参**）：命中即抛，**绝不调用模型**。
    # 顺序铁律：这三行必须在任何 `_complete()` / LLM 调用之前。
    _guard_free_text(text, field="file_content")
    _guard_free_text(instruction, field="instruction")
    _guard_free_text(filename, field="target_file")

    user_prompt = _LATEX_USER_TEMPLATE.replace("__INSTRUCTION__", instruction).replace(
        "__SOURCE__", text
    )
    messages = [
        {"role": "system", "content": _LATEX_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    completion = await _complete(messages, model=model)
    edited = _extract_latex(completion.text)

    # 纵深防御第二层（对**模型输出**）：模型也可能被诱导产出危险控制序列。
    _guard_tex_model_output(edited)

    fname = _safe_filename(filename)
    logger.info(
        "wb_latex_edit_ok in_bytes=%s out_bytes=%s model=%s",
        len(text.encode("utf-8")),
        len(edited.encode("utf-8")),
        completion.model,
    )
    return {
        # —— 前端渲染契约（逐键对齐 /xf） ——
        "success": True,
        "changes": _diff_changes(text, edited, fname),
        "final_updated_code": edited,
        "security_msg": "",
        # —— 既有键全部保留（+ 规范别名字段） ——
        "target_file": fname,
        "filename": fname,
        "instruction": instruction,
        "source": edited,
        "model": completion.model,
        "latencyMs": completion.latency_ms,
        "engines": {"llm": completion.provider, "texGuard": "xf._validate_tex_source"},
    }


# ==========================================================================
# 方法检索（文献内核 + 可选 LLM 生成方法说明）
# ==========================================================================


class _LiteratureProviderLike(Protocol):
    """文献 provider 的最小契约（便于测试注入 fake）。"""

    name: str

    def search(
        self, query: str, *, limit: int = 5, request_id: str = ""
    ) -> list[Any]: ...


def _default_literature_provider() -> _LiteratureProviderLike:
    """构造默认文献 provider（Crossref）。**惰性 import**，避免无谓依赖加载。"""
    from ..literature.crossref import CrossrefProvider

    return CrossrefProvider()


def search_methods(
    query: str,
    *,
    limit: int = 5,
    provider: _LiteratureProviderLike | None = None,
) -> dict[str, Any]:
    """经文献内核检索方法素材（**真实**检索，返回可溯源元数据）。

    Returns:
        `{query, count, items, provider, note}`；`items` 为 `LiteratureMetadata.to_public()`。

    Raises:
        WbBadRequest: query 为空；
        WbLiteratureUnavailable: provider 超时 / 限流 / 网络 / 上游错误（诚实降级）。
    """
    q = (query or "").strip()
    if not q:
        raise WbBadRequest("缺少 query（检索式）")
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(n, WB_METHOD_MAX_RESULTS))

    prov = provider if provider is not None else _default_literature_provider()
    request_id = uuid.uuid4().hex[:16]

    try:
        rows = prov.search(q, limit=n, request_id=request_id)
    except LiteratureProviderError as e:
        logger.info("wb_method_search_provider_error code=%s", getattr(e, "code", "?"))
        raise WbLiteratureUnavailable(
            f"文献检索不可用：{getattr(e, 'message', str(e))}",
            detail={"providerCode": getattr(e, "code", "")},
        ) from e
    except WbError:
        raise
    except Exception as e:  # noqa: BLE001 - 未预期异常也要诚实降级，绝不编造
        logger.info("wb_method_search_failed err=%s", type(e).__name__)
        raise WbLiteratureUnavailable(
            f"文献检索失败（{type(e).__name__}）"
        ) from e

    items = [r.to_public() for r in rows]
    logger.info("wb_method_search_ok provider=%s count=%s", getattr(prov, "name", "?"), len(items))
    return {
        "query": q,
        "count": len(items),
        "items": items,
        "provider": getattr(prov, "name", "crossref"),
        "note": "真实检索结果（Crossref metadata-only）；非模型生成，metadata ≠ 全文",
    }


def _to_source(item: dict[str, Any]) -> dict[str, Any]:
    """把一条 `to_public()` 文献元数据映射为前端 `sources[]` 条目（**真实**字段）。"""
    published = str(item.get("published_date") or "")
    year = published[:4] if len(published) >= 4 and published[:4].isdigit() else ""
    authors: list[str] = []
    for a in item.get("authors") or []:
        if isinstance(a, dict):
            name = str(a.get("name") or a.get("family") or "").strip()
        else:
            name = str(a).strip()
        if name:
            authors.append(name)
    return {
        "title": str(item.get("title") or ""),
        "doi": str(item.get("doi") or ""),
        "url": str(item.get("source_url") or ""),
        "year": year,
        "venue": str(item.get("venue") or ""),
        "authors": authors,
    }


_METHOD_ENRICH_SYSTEM = (
    "你是数学建模方法库助手。基于用户给出的方法名与检索式，输出**严格 JSON**"
    "（单行、无解释、无代码围栏），且仅含以下字段："
    '{"method_name_en": 字符串, "keywords": [字符串], "core_formula": 字符串, '
    '"common_scenario": 字符串, "assumptions": [字符串], '
    '"precondition_checklist": [字符串], "limitation": 字符串}。'
    "内容必须是通用学术通识；**严禁**编造具体文献、作者、DOI、期刊或页码；"
    "拿不到的字段留空（字符串留空串，数组留空数组）。"
)

_METHOD_ENRICH_USER = "【方法名】__NAME__\n【检索式】__QUERY__\n"


def _method_empty_fields() -> dict[str, Any]:
    return {
        "method_name_en": "",
        "keywords": [],
        "core_formula": "",
        "common_scenario": "",
        "assumptions": [],
        "precondition_checklist": [],
        "limitation": "",
    }


async def _method_enrichment(query: str, method_name: str) -> tuple[dict[str, Any], list[str]]:
    """用 LLM 生成方法说明字段；返回 `(fields, ai_generated_fields)`。

    任一字段为空则不列入 `ai_generated_fields`（如实标注）。
    """
    name = (method_name or "").strip() or query.strip()
    user = _METHOD_ENRICH_USER.replace("__NAME__", name).replace("__QUERY__", query.strip())
    messages = [
        {"role": "system", "content": _METHOD_ENRICH_SYSTEM},
        {"role": "user", "content": user},
    ]
    completion = await _complete(messages)
    obj = _extract_json_object(completion.text)
    if obj is None:
        raise WbLlmBadOutput("方法说明生成未返回严格 JSON")

    def _str_field(key: str) -> str:
        value = obj.get(key)
        return str(value).strip() if isinstance(value, (str, int, float)) else ""

    def _list_field(key: str) -> list[str]:
        value = obj.get(key)
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    fields = {
        "method_name_en": _str_field("method_name_en"),
        "keywords": _list_field("keywords"),
        "core_formula": _str_field("core_formula"),
        "common_scenario": _str_field("common_scenario"),
        "assumptions": _list_field("assumptions"),
        "precondition_checklist": _list_field("precondition_checklist"),
        "limitation": _str_field("limitation"),
    }
    ai_fields = [
        k
        for k, v in fields.items()
        if (len(v) > 0 if isinstance(v, list) else bool(str(v).strip()))
    ]
    return fields, ai_fields


async def method_search(
    query: str,
    *,
    limit: int = 5,
    method_name: str = "",
    provider: _LiteratureProviderLike | None = None,
) -> dict[str, Any]:
    """`POST /wb/method:search` 的完整契约响应。

    流程：**真实检索**（失败即 503，绝不编造）→ **可选** LLM 生成方法说明
    （不可用时字段留空 + `warning`，**整体仍 200**）。

    Args:
        query: 检索式。
        limit: 返回条目上限。
        method_name: 用户给出的方法名（**原样回显**，不是生成物）。
        provider: 注入的 provider（测试用）。

    Raises:
        WbBadRequest: query 为空；
        WbLiteratureUnavailable: 文献检索失败（检索是硬依赖）。
    """
    base = await asyncio.to_thread(search_methods, query, limit=limit, provider=provider)
    items = base["items"]
    sources = [_to_source(it) for it in items]

    fields = _method_empty_fields()
    ai_fields: list[str] = []
    warning: str | None = None

    if gateway.llm_configured():
        try:
            gen, ai_fields = await _method_enrichment(base["query"], method_name)
            fields.update(gen)
        except WbError as e:
            warning = f"方法说明生成不可用（{e.code}）；已仅返回真实检索结果"
    else:
        warning = "LLM 未配置：方法说明字段为空，已仅返回真实检索结果"

    if not sources:
        warning = (warning + "；" if warning else "") + "未检索到可溯源文献（sources 为空）"

    logger.info(
        "wb_method_search_full count=%s ai_fields=%s warning=%s",
        len(sources),
        ai_fields,
        bool(warning),
    )
    return {
        "query": base["query"],
        # —— 前端渲染契约 ——
        "method_name": (method_name or "").strip(),
        "method_name_en": fields["method_name_en"],
        "keywords": fields["keywords"],
        "core_formula": fields["core_formula"],
        "common_scenario": fields["common_scenario"],
        "assumptions": fields["assumptions"],
        "precondition_checklist": fields["precondition_checklist"],
        "limitation": fields["limitation"],
        "reference_note": "以下方法说明由模型生成，请以专业教材与原始文献为准。",
        "sources": sources,
        "evidence_records": items,
        "ai_generated_fields": ai_fields,
        "warning": warning,
        # —— 既有键全部保留 ——
        "count": base["count"],
        "provider": base["provider"],
        "note": base["note"],
    }


# ==========================================================================
# 学术翻译（LLM 网关；单次调用产出段级对照；公式真实比对）
# ==========================================================================

_TRANSLATE_SYSTEM_PROMPT = (
    "你是严谨的学术翻译助手。在保持语义与术语一致的前提下，把用户文本翻译到目标语言。"
    "必须**逐字保留**所有数学公式（含 $...$、$$...$$、\\[...\\]、equation 环境）、"
    "LaTeX 控制序列、引用（\\cite{...}）与专有名词，不得翻译或改动其内容。"
    "**只输出严格 JSON（单行、无解释、无代码围栏）**："
    '{"translated_text": 整段译文, '
    '"blocks": [{"source": 原文段, "target": 译文段}, ...]}。'
    "`blocks` 为段级对照，段数应与原文段落一致。"
)

_TRANSLATE_USER_TEMPLATE = (
    "【源语言】__SRC__\n【目标语言】__TGT__\n\n"
    "【待翻译文本】\n__TEXT__\n"
)

#: 公式 token 提取（按块优先，避免 `$$` 被当成两个 `$`；逐步抹掉已匹配区间）。
_RE_BLOCK_MATH = re.compile(r"\$\$.+?\$\$", re.DOTALL)
_RE_BRACKET_MATH = re.compile(r"\\\[.+?\\\]", re.DOTALL)
_RE_PAREN_MATH = re.compile(r"\\\(.+?\\\)", re.DOTALL)
_RE_ENV_MATH = re.compile(
    r"\\begin\{(?:equation|align|gather|multline|eqnarray)\*?\}.+?"
    r"\\end\{(?:equation|align|gather|multline|eqnarray)\*?\}",
    re.DOTALL,
)
_RE_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)", re.DOTALL)


def _normalize_formula_token(token: str) -> str:
    """把公式 token 归一化以便**跨语言比对**：抹掉所有空白。

    模型在翻译时常会在公式里插入 / 去掉空格（如源 `$x^2\\ge 0$` vs 译
    `$x^2 \\ge 0$`）—— 语义完全一致，但字面不同。逐 token 抹掉空白后比对，
    才能得到**真实**的公式保全计数（既不漏报也不误报）。
    """
    return "".join((token or "").split())


def _formula_tokens(text: str) -> list[str]:
    """提取文本中的公式 token（块公式优先，逐步抹掉已匹配区间以防重复计数）。

    token 已做空白归一化（见 `_normalize_formula_token`），可直接用于跨语言计数。
    """
    s = text or ""
    tokens: list[str] = []

    def _take(pattern: re.Pattern[str]) -> None:
        nonlocal s
        for m in pattern.finditer(s):
            normalized = _normalize_formula_token(m.group(0))
            if normalized:
                tokens.append(normalized)
        s = pattern.sub(lambda m: " " * len(m.group(0)), s)

    _take(_RE_BLOCK_MATH)
    _take(_RE_BRACKET_MATH)
    _take(_RE_PAREN_MATH)
    _take(_RE_ENV_MATH)
    _take(_RE_INLINE_MATH)
    return tokens


def _count_formulas(source_text: str, target_text: str) -> tuple[int, int]:
    """返回 `(formulas_total, formulas_preserved)` —— 基于 token 多重集的真实计数。"""
    src = Counter(_formula_tokens(source_text))
    tgt = Counter(_formula_tokens(target_text))
    total = sum(src.values())
    preserved = sum(min(count, tgt.get(token, 0)) for token, count in src.items())
    return total, preserved


def _extract_plain(text: str) -> str:
    """剥掉可能的 Markdown 代码围栏（翻译 fallback 用）。"""
    body = (text or "").strip()
    if body.startswith("```"):
        m = re.search(r"```[^\n]*\r?\n(.*?)```", body, re.DOTALL)
        if m is not None:
            return m.group(1).strip()
    return body


#: 从形如 `"translated_text": "..."` 的文本里抢救纯译文（最后兜底）。
_RE_TRANSLATED_TEXT = re.compile(r'"translated_text"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)


def _looks_like_json_blob(s: str) -> bool:
    """粗略判断一段文本是否"看起来像一坨 JSON 对象"（据此**拒绝**把它当译文）。"""
    t = (s or "").strip()
    return t.startswith("{") and '"' in t and ":" in t


def _decode_json_string_fragment(raw: str) -> str:
    """把一个 JSON 字符串**片段**（不含外层引号）解码为明文；容错非法转义。"""
    try:
        return json.loads(f'"{raw}"')
    except Exception:  # noqa: BLE001
        return _repair_json_escapes(raw).replace('\\"', '"')


def _salvage_translation_text(text: str) -> str:
    """从模型输出中抢救**纯译文**；**绝不**把整串 JSON 当译文返回。

    顺序：① 若有 `"translated_text": "..."` → 取该值；② 否则剥围栏取纯文本，
    但若剥完仍是一坨 JSON → 返回空串（触发诚实的 502，而不是把 JSON 吐给用户）。
    """
    m = _RE_TRANSLATED_TEXT.search(text or "")
    if m is not None:
        decoded = _decode_json_string_fragment(m.group(1)).strip()
        if decoded:
            return decoded
    plain = _extract_plain(text)
    if _looks_like_json_blob(plain):
        return ""
    return plain


async def translate(
    text: str,
    *,
    source_lang: str,
    target_lang: str,
    model: str | None = None,
    glossary: str | None = None,
) -> dict[str, Any]:
    """学术翻译（LLM 网关；**同一次调用**产出段级对照；公式真实比对计数）。

    Args:
        glossary: 可选的「已确立术语」块（宪法 §42 Terminology Memory）。
            由调用方（`research_workspace/agent.py::_run_translate`）从**项目级**术语记忆生成；
            非空时追加到 user prompt，要求模型**必须沿用**这些译法，不得另起译名。

    Raises:
        WbBadRequest: 文本或目标语言缺失；
        `WbError`：LLM 层失败（未配置/超时/输出不可用/其它）。
    """
    body = text or ""
    if not body.strip():
        raise WbBadRequest("缺少 text（待翻译文本）")
    if not (target_lang or "").strip():
        raise WbBadRequest("缺少 targetLang（目标语言）")

    src = (source_lang or "auto").strip() or "auto"
    tgt = target_lang.strip()
    user_prompt = (
        _TRANSLATE_USER_TEMPLATE.replace("__SRC__", src)
        .replace("__TGT__", tgt)
        .replace("__TEXT__", body)
    )
    # §42：术语记忆注入（放在待翻译文本**之后**，更靠近生成位置，遵从率更高）
    if (glossary or "").strip():
        user_prompt = user_prompt + "\n" + glossary.strip() + "\n"
    messages = [
        {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    completion = await _complete(messages, model=model)

    parsed = _extract_json_object(completion.text)
    blocks: list[dict[str, str]] = []
    warning: str | None = None
    translated = ""
    if parsed is not None:
        translated = str(parsed.get("translated_text") or "").strip()
        raw_blocks = parsed.get("blocks")
        if isinstance(raw_blocks, list):
            for b in raw_blocks:
                if not isinstance(b, dict):
                    continue
                s = str(b.get("source") or "").strip()
                t = str(b.get("target") or "").strip()
                if s or t:
                    blocks.append({"source": s, "target": t})
        if not translated and blocks:
            translated = "\n".join(b["target"] for b in blocks if b["target"])

    if not translated:
        # 未能解析出结构化译文 → 尽力抢救纯译文；**绝不**把模型原始 JSON 串当译文
        # （那是最坏的"看起来成功"）。真正取不到才走上层诚实失败。
        translated = _salvage_translation_text(completion.text)
        blocks = []

    if not translated:
        raise WbLlmBadOutput("模型返回空译文（或仅返回无法解析的 JSON）")
    if not blocks:
        warning = BILINGUAL_UNAVAILABLE_WARNING

    formulas_total, formulas_preserved = _count_formulas(body, translated)
    logger.info(
        "wb_translate_ok src=%s tgt=%s blocks=%s formulas=%s/%s",
        src,
        tgt,
        len(blocks),
        formulas_preserved,
        formulas_total,
    )
    return {
        # —— 前端渲染契约（规范名 + camelCase 兼容别名） ——
        "source_lang": src,
        "target_lang": tgt,
        "sourceLang": src,
        "targetLang": tgt,
        "translated_text": translated,
        "bilingual_blocks": blocks,
        "formulas_preserved": formulas_preserved,
        "formulas_total": formulas_total,
        "warning": warning,
        # —— 既有键保留 ——
        "text": translated,
        "model": completion.model,
        "latencyMs": completion.latency_ms,
    }


__all__ = [
    "capabilities",
    "edit_latex",
    "search_methods",
    "method_search",
    "translate",
    "WB_METHOD_MAX_RESULTS",
    "BILINGUAL_UNAVAILABLE_WARNING",
]
