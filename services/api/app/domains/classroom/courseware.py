"""教师课堂课件（classroom courseware）领域服务。

一条真实课堂链的数据服务层：

    教师发布课件（TeachingArtifact, scene=classroom_courseware）
        → 后台准备（讲稿 / blocks / 资产 / 分层练习）
        → confirm + publish（复用 domains/teacher/artifacts 状态机与审计）
        → 学生打开（ClassroomSession, source_type=teacher_deck）
        → 进度 / 练习回流（沿用既有 classroom_sessions.progress / practice_stats）
        → 教师监控聚合（真实 SQL + JSONB，不伪造）

设计约束（与 docs/superpowers/specs/2026-10-20-teacher-student-classroom-design.md 一致）：
- 不新建表 / 不新增迁移：发布载体 = TeachingArtifact，学生会话 = ClassroomSession；
- 不修改 stage_router 既有逻辑：lazy import 复用 `_gen_practice` / `_fallback_practice`；
- 讲稿优先教师 `speaker_note`；否则一次批量 LLM（失败 → 确定性讲稿，不空转、不裸露 LaTeX）；
- 监控所有数字都来自真实写入；无数据时诚实返回 0 / 空数组。
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.teacher.artifacts import (
    archive_artifact,
    confirm_artifact,
    create_artifact,
    get_owned_artifact,
    publish_artifact,
)
from app.domains.teacher.scope import raise_http
from app.models.class_ import Class
from app.models.class_member import ClassMember
from app.models.classroom import ClassroomSession
from app.models.database import background_session_factory
from app.models.teacher import TeachingArtifact
from app.models.user import User

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# 常量与契约
# ---------------------------------------------------------------------------

COURSEWARE_ARTIFACT_TYPE = "slide_deck"
COURSEWARE_SCENE = "classroom_courseware"
COURSEWARE_SOURCE_TYPE = "teacher_deck"

MIN_SLIDES = 3
MAX_SLIDES = 30
MAX_ELEMENTS_PER_SLIDE = 12
MAX_ASSET_TOTAL_BYTES = 4 * 1024 * 1024  # 4MB（设计 §4.1）
MAX_NARRATION_CHARS = 320
SLIDE_MINUTES = 3

ALLOWED_IMAGE_MIME_PREFIX = "image/"
_DATA_URL_MIME_RE = re.compile(r"^image/[a-zA-Z0-9.+-]+$")

# 准备中的课件（进程内去重；生产单进程 uvicorn + 断点由详情接口兜底续跑）
_active_preparations: set[str] = set()
_background_tasks: set[asyncio.Task] = set()

_ERR_NOT_FOUND = 40400
_ERR_VALIDATION = 40001


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _clip(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


# ---------------------------------------------------------------------------
# 请求归一化（创建发布）
# ---------------------------------------------------------------------------


def _normalize_element(raw: Any) -> dict | None:
    """把前端 deck 元素收敛到 §A.2 语义元素 + 尺寸上限（不改变教学内容）。"""
    if not isinstance(raw, dict):
        return None
    role = _clip(raw.get("role"), 40)
    if not role:
        return None
    out: dict[str, Any] = {"role": role}
    if raw.get("text") is not None:
        out["text"] = _clip(raw.get("text"), 400)
    if raw.get("latex") is not None:
        out["latex"] = _clip(raw.get("latex"), 2000)
    if raw.get("title") is not None:
        out["title"] = _clip(raw.get("title"), 60)
    items = raw.get("items")
    if isinstance(items, (list, tuple)):
        clipped = [_clip(item, 200) for item in items[:10] if str(item).strip()]
        if clipped:
            out["items"] = clipped
    if raw.get("figure_ref") is not None:
        out["figure_ref"] = _clip(raw.get("figure_ref"), 40)
    return out


def _normalize_asset(raw: Any, remaining: int) -> tuple[dict | None, int, str | None]:
    """归一化单个资产；返回 (资产, 消耗字节, 警告)。超限/非法 → 跳过并写 warning。"""
    if not isinstance(raw, dict):
        return None, 0, "存在无法识别的资产，已跳过"
    asset_id = _clip(raw.get("id"), 120)
    mime = _clip(raw.get("mime_type"), 80).lower()
    data = raw.get("data_base64")
    if not asset_id or not isinstance(data, str) or not data.strip():
        return None, 0, "存在缺少 id/data_base64 的资产，已跳过"
    if not mime.startswith(ALLOWED_IMAGE_MIME_PREFIX) or not _DATA_URL_MIME_RE.match(mime):
        return None, 0, f"资产 {asset_id} 的 MIME 类型不受支持，已跳过"
    size = len(data) * 3 // 4
    if size > remaining:
        return None, 0, f"资产 {asset_id} 超出 4MB 预算，已跳过"
    asset = {"id": asset_id, "mime_type": mime, "data_base64": data}
    sha = _clip(raw.get("sha256"), 64)
    if sha:
        asset["sha256"] = sha
    return asset, size, None


def normalize_courseware_payload(body: dict) -> tuple[dict, list[str]]:
    """校验并归一化 8100 发布载荷；返回 (payload, warnings)。

    契约约束：3–30 页；单页 elements ≤12（超出裁剪 + warning）；
    资产总大小 ≤4MB（超限跳过 + warning，不阻断发布）。
    """
    title = _clip(body.get("title"), 200)
    if not title:
        raise_http(_ERR_VALIDATION, 422, "title_required", recoverable=True)

    raw_slides = body.get("slides")
    if not isinstance(raw_slides, list) or not (MIN_SLIDES <= len(raw_slides) <= MAX_SLIDES):
        raise_http(
            _ERR_VALIDATION,
            422,
            "invalid_slide_count",
            min=MIN_SLIDES,
            max=MAX_SLIDES,
            recoverable=True,
        )

    warnings: list[str] = [_clip(w, 200) for w in (body.get("warnings") or []) if str(w).strip()]
    budget = MAX_ASSET_TOTAL_BYTES
    slides: list[dict] = []

    for index, raw in enumerate(raw_slides):
        if not isinstance(raw, dict):
            raise_http(_ERR_VALIDATION, 422, "invalid_slide", index=index, recoverable=True)
        slide_id = _clip(raw.get("id") or f"slide-{index:02d}", 80)
        slide_type = _clip(raw.get("type") or "content", 40)
        slide_title = _clip(raw.get("title") or f"第 {index + 1} 页", 120)
        slide: dict[str, Any] = {
            "id": slide_id,
            "type": slide_type,
            "title": slide_title,
            "content": _clip(raw.get("content"), 1200),
            "layout_hint": _clip(raw.get("layout_hint"), 40) or None,
            "elements": [],
            "math_object": (
                raw.get("math_object") if isinstance(raw.get("math_object"), dict) else None
            ),
            "assets": [],
            "speaker_note": _clip(raw.get("speaker_note"), 2000) or None,
        }

        raw_elements = raw.get("elements") or []
        if isinstance(raw_elements, list) and len(raw_elements) > MAX_ELEMENTS_PER_SLIDE:
            warnings.append(f"第 {index + 1} 页元素超过 {MAX_ELEMENTS_PER_SLIDE} 个，已裁剪")
        for element in (raw_elements if isinstance(raw_elements, list) else [])[
            :MAX_ELEMENTS_PER_SLIDE
        ]:
            normalized = _normalize_element(element)
            if normalized is not None:
                slide["elements"].append(normalized)

        raw_assets = raw.get("assets") or []
        for asset in raw_assets if isinstance(raw_assets, list) else []:
            normalized, spent, warning = _normalize_asset(asset, budget)
            if warning:
                warnings.append(warning)
            if normalized is not None:
                budget -= spent
                slide["assets"].append(normalized)

        slides.append(slide)

    payload: dict[str, Any] = {
        "deck_id": _clip(body.get("deck_id"), 80),
        "deck_revision": int(body.get("deck_revision") or 1),
        "title": title,
        "textbook_line": _clip(body.get("textbook_line"), 200) or None,
        "subject": _clip(body.get("subject"), 40) or None,
        "slides": slides,
        "source_refs": [ref for ref in (body.get("source_refs") or []) if isinstance(ref, dict)][
            :50
        ],
        "warnings": warnings,
        "page_count": len(slides),
        # 后台准备状态（学生端只看到 published，教师端据此显示“准备中”）
        "narration_status": "pending",
        "practice_status": "pending",
        "prepared_at": None,
        "practice": None,
        "outlines": [],
    }
    return payload, warnings


async def create_courseware(
    db: AsyncSession,
    *,
    teacher_id: uuid.UUID,
    body: dict,
) -> TeachingArtifact:
    """创建 draft artifact（payload 已归一化），供后台 prepare 使用。"""
    payload, warnings = normalize_courseware_payload(body)
    artifact = await create_artifact(
        db,
        owner_id=teacher_id,
        artifact_type=COURSEWARE_ARTIFACT_TYPE,
        scene=COURSEWARE_SCENE,
        class_id=body.get("class_id"),
        payload=payload,
        source_refs=payload["source_refs"],
        engine="local",
        degraded=False,
        warnings=warnings,
    )
    return artifact


def schedule_prepare(artifact_id: str) -> None:
    """请求返回后立即在后台准备（不用 BackgroundTasks——本环境已证实不触发）。"""
    task = asyncio.get_running_loop().create_task(prepare_courseware(artifact_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def is_preparing(artifact_id: str) -> bool:
    """该课件是否已有准备任务在跑（详情接口 auto-resume 用）。"""
    return artifact_id in _active_preparations


# ---------------------------------------------------------------------------
# 讲稿生成 / 清洗
# ---------------------------------------------------------------------------

_INLINE_MATH_RE = re.compile(r"\$[^$]*\$")
_DISPLAY_MATH_RE = re.compile(r"\$\$.*?\$\$", re.S)
_FENCE_RE = re.compile(r"```.*?```", re.S)
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def _strip_latex_commands(value: str) -> str:
    """删除 LaTeX 命令及其（可嵌套的）花括号参数，保留普通文字。

    `\\boxed{\\frac{1}{2}}` 必须整体删除，而不是只删命令名留下 `{ {1}{2}}`。
    """
    out: list[str] = []
    i = 0
    length = len(value)
    while i < length:
        if value[i] == "\\" and i + 1 < length and value[i + 1].isalpha():
            j = i + 1
            while j < length and value[j].isalpha():
                j += 1
            while j < length:
                if value[j] == "[":
                    end = value.find("]", j)
                    if end < 0:
                        break
                    j = end + 1
                elif value[j] == "{":
                    depth = 0
                    k = j
                    while k < length:
                        if value[k] == "{":
                            depth += 1
                        elif value[k] == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        k += 1
                    if k >= length:
                        break
                    j = k + 1
                else:
                    break
            out.append(" ")
            i = j
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


_NARRATION_PROMPT_HEAD = (
    "你是高中数学课堂的主讲老师，正在面向学生讲课。下面是教师课件的逐页信息，"
    "请为每一页写一段学生能听懂、能跟着看的课堂讲稿。\n"
    "要求：\n"
    "1. 每页 3–6 句，口语化，面向高中生，单页 60–260 字；按“看什么 → 是什么 → 为什么 → 怎么用”的顺序讲；\n"
    "2. 必须覆盖该页的主要文字、公式含义和图形/例题；公式要用中文口述其含义，绝对不要念 LaTeX 源码；\n"
    "3. 只能讲页面已经给出的内容，不得编造公式、定理、数值或结论；\n"
    "4. 教师备注只用于理解教学意图，**绝对不要原样朗读**，也不要出现“让学生/请学生/教师巡视/同桌互查”等备课口吻；\n"
    "5. 不要出现 LaTeX 源码、美元符号、反斜杠命令、Markdown 标记或序号前缀；\n"
    '6. 只输出 JSON 对象：{"narrations":[{"order":1,"narration":"……"}]}，不要输出其他文字。\n'
)

_TEACHER_FACING_RE = re.compile(
    r"让学生|请学生|引导学生回答|提问学生|同桌互查|同桌互评|教师巡视|板书设计|布置作业|课堂管理"
)
# 文本元素里混入的 LaTeX 源码（含被剥掉反斜杠后的 coslangle 之类）不能进入口播稿
_MATH_SOURCE_RE = re.compile(
    r"\\|\$|\^|_\{|langle|rangle|frac|sqrt|begin\{|end\{|\b(?:cos|sin|tan|log|ln)\b"
)


def _sanitize_narration(text: object) -> str:
    """讲稿清洗：去掉公式源码 / 代码围栏 / Markdown 标记，≤320 字。

    讲稿用于 TTS 与字幕，绝不能出现 `\\boxed`、`$`、`\\frac` 等源码。
    """
    value = str(text or "")
    value = _FENCE_RE.sub(" ", value)
    value = _DISPLAY_MATH_RE.sub(" ", value)
    value = _INLINE_MATH_RE.sub(" ", value)
    value = _strip_latex_commands(value)
    value = value.replace("$", " ").replace("\\", " ")
    value = value.replace("{", " ").replace("}", " ")
    value = value.replace("**", " ").replace("##", " ")
    value = re.sub(r"[ \t\u00a0]+", " ", value)
    value = re.sub(r"\s*\n\s*", " ", value)
    value = re.sub(r"[（(]\s*[）)]", "", value)
    value = re.sub(r"[，、；]{2,}", "，", value)
    value = re.sub(r"[。；，]{2,}", "。", value)
    value = value.strip().lstrip(" ，；、")
    if len(value) > MAX_NARRATION_CHARS:
        cut = value[:MAX_NARRATION_CHARS]
        boundary = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"), cut.rfind("；"))
        value = cut[: boundary + 1] if boundary >= 160 else cut
    return value.strip()


def _looks_like_math_source(text: str) -> bool:
    return bool(_MATH_SOURCE_RE.search(text or ""))


def _plain_units(elements: list[dict], limit: int = 3) -> list[str]:
    """从元素里抽取可朗读的纯文本单元（跳过公式源码 / 数学源码残片）。"""
    units: list[str] = []
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        role = str(element.get("role") or "")
        if role == "formula":
            continue
        if role == "table":
            for row in element.get("items") or []:
                cells = [_sanitize_narration(c) for c in str(row).split("|")]
                text = "、".join(c for c in cells if c and not _looks_like_math_source(c))
                if text:
                    units.append(_clip(text, 80))
            continue
        if element.get("text"):
            text = _sanitize_narration(element.get("text"))
            if text and not _looks_like_math_source(text):
                units.append(_clip(text, 80))
        for item in element.get("items") or []:
            text = _sanitize_narration(item)
            if text and not _looks_like_math_source(text):
                units.append(_clip(text, 80))
        if len(units) >= limit:
            break
    # 去重（保序）
    seen: set[str] = set()
    out: list[str] = []
    for unit in units:
        if unit and unit not in seen:
            seen.add(unit)
            out.append(unit)
    return out[:limit]


def _formula_units(elements: list[dict], limit: int = 4) -> list[str]:
    """本页公式（原文，仅供讲稿生成时理解并口述，不会直接下发）。"""
    formulas: list[str] = []
    for element in elements or []:
        if not isinstance(element, dict) or str(element.get("role") or "") != "formula":
            continue
        latex = str(element.get("latex") or "").strip()
        if latex:
            formulas.append(_clip(latex, 160))
        if len(formulas) >= limit:
            break
    return formulas


def _figure_hint(slide: dict) -> str:
    """本页图形/资产的可朗读提示（只描述页面确有的内容，不编造形状）。"""
    elements = slide.get("elements") or []
    hints: list[str] = []
    for element in elements:
        if not isinstance(element, dict) or str(element.get("role") or "") != "figure":
            continue
        caption = _sanitize_narration(element.get("text"))
        if caption:
            hints.append(caption)
        break
    math_object = slide.get("math_object")
    if isinstance(math_object, dict) and math_object.get("kind"):
        hints.append(f"页面配有数学图形（{math_object.get('kind')}）")
    if slide.get("assets"):
        hints.append("页面配有图像")
    return "；".join(dict.fromkeys(hints))


def _valid_classroom_narration(text: object) -> bool:
    """课堂讲稿有效性：面向学生、足够完整、不是备课口吻。"""
    value = _sanitize_narration(text)
    if len(value) < 40:
        return False
    return not _TEACHER_FACING_RE.search(value)


def _is_cover_slide(raw: dict) -> bool:
    return str(raw.get("type") or "") == "title" or str(raw.get("layout_hint") or "") == "title"


def _narration_page(raw: dict, order: int) -> dict:
    """单页讲稿上下文。封面版式不渲染 elements，讲稿也不能讲隐藏元素。"""
    elements = [e for e in (raw.get("elements") or []) if isinstance(e, dict)]
    title = _clip(raw.get("title") or f"第 {order} 页", 120)
    raw_content = str(raw.get("content") or "")
    content = _sanitize_narration(raw_content)
    if _is_cover_slide(raw):
        # 封面版式只渲染标题 + 短副标题；若 content 是整节课正文（历史 deck 常见），
        # 不能拿它生成封面讲稿，否则学生会听到“课堂小结”之类与画面不符的内容。
        cover_content = (
            content if len(raw_content.strip()) <= 60 and "\n" not in raw_content else ""
        )
        return {
            "order": order,
            "title": title,
            "content": cover_content,
            "units": [],
            "formulas": [],
            "figure": "",
            "teacher_note": "",
            "is_cover": True,
        }
    return {
        "order": order,
        "title": title,
        "content": content,
        "units": _plain_units(elements, limit=6),
        "formulas": _formula_units(elements),
        "figure": _figure_hint(raw),
        "teacher_note": _sanitize_narration(raw.get("speaker_note")),
        "is_cover": False,
    }


def _derive_cover_narration(title: str, subtitle: str = "") -> str:
    """封面讲稿：只介绍课题/教材位置，不触碰封面不渲染的隐藏正文。"""
    plain_title = _sanitize_narration(title) or "本课"
    text = f"同学们好，今天我们一起学习「{plain_title}」。"
    sub = _sanitize_narration(subtitle)
    if sub:
        text += f"这节课对应{sub.rstrip('。')}。"
    text += "先看课题，想一想这节课要解决什么问题，再跟着页面一步步展开。"
    return _sanitize_narration(text)


def _derive_narration(
    title: str,
    elements: list[dict],
    *,
    content: str = "",
    formulas: list[str] | None = None,
    figure_hint: str = "",
) -> str:
    """确定性讲稿（LLM 不可用 / 单页生成失败时的保底，不编造数学事实）。"""
    plain_title = _sanitize_narration(title) or "本页内容"
    units = _plain_units(elements)
    if not units and content:
        units = [_sanitize_narration(content)]
    if units:
        body = "；".join(units)
        text = f"这一页我们学习「{plain_title}」。{body}。"
    else:
        text = f"这一页我们学习「{plain_title}」。"
    if formulas:
        text += "本页还给出了公式，请对照页面上的表达式理解它的含义和适用条件。"
    if figure_hint:
        text += f"{figure_hint.rstrip('。')}，请结合图像观察变化趋势。"
    text += "请先看清页面给出的条件和结论，再跟着例子完成推理。"
    return _sanitize_narration(text)


def _extract_json_object(text: str) -> Any:
    """从模型输出中抠出第一个 JSON 对象/数组（容忍 ```json 围栏与前后说明）。"""
    value = str(text or "").strip()
    value = _FENCE_RE.sub(" ", value).strip()
    start = min(
        (pos for pos in (value.find("{"), value.find("[")) if pos >= 0),
        default=-1,
    )
    if start < 0:
        return None
    closing = "}" if value[start] == "{" else "]"
    end = value.rfind(closing)
    if end <= start:
        return None
    try:
        return json.loads(value[start : end + 1])
    except (TypeError, ValueError):
        return None


def _parse_narrations(content: str) -> dict[int, str]:
    """解析批量讲稿 JSON，兼容 list / 字典两种形状。"""
    data = _extract_json_object(content)
    out: dict[int, str] = {}
    if isinstance(data, dict):
        raw = data.get("narrations", data)
        if isinstance(raw, dict):
            for key, text in raw.items():
                try:
                    order = int(key)
                except (TypeError, ValueError):
                    continue
                if isinstance(text, str):
                    out[order] = text
        elif isinstance(raw, list):
            data = raw
        else:
            return out
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                order = int(item.get("order"))
            except (TypeError, ValueError):
                continue
            text = item.get("narration") or item.get("text")
            if isinstance(text, str):
                out[order] = text
    return out


async def _generate_narrations(
    deck_title: str, pages: list[dict]
) -> tuple[dict[int, str], str | None]:
    """分批 LLM 生成学生课堂讲稿；失败返回部分结果 + 警告，由调用方逐页降级。

    批次上限 8 页：30 页 deck 一次生成会超过 max_tokens，导致后半份讲稿被截断。
    首批后对缺失/不合格页再补一次单批重试；仍失败才走确定性讲稿。
    """
    if not pages:
        return {}, None
    # 封面讲稿由确定性开场文案负责（封面不渲染正文，AI 容易把后文内容串进来）
    pages = [p for p in pages if not p.get("is_cover")]
    if not pages:
        return {}, None
    from app.providers.router import get_model_router

    router = get_model_router()
    cleaned: dict[int, str] = {}
    failures: list[str] = []
    batch_size = 8

    async def _request(batch: list[dict]) -> dict[int, str]:
        lines = [f"课件标题：{deck_title}"]
        for page in batch:
            lines.append(f"{page['order']}. 【{page['title']}】")
            if page.get("content"):
                lines.append(f"   页面说明：{page['content']}")
            if page.get("units"):
                lines.append(f"   页面文字：{'；'.join(page['units'])}")
            if page.get("formulas"):
                lines.append(
                    "   页面公式（请用中文口述含义，不要念源码）：" + "；".join(page["formulas"])
                )
            if page.get("figure"):
                lines.append(f"   图形：{page['figure']}")
            if page.get("teacher_note"):
                lines.append(
                    f"   教师备注（仅供理解教学意图，禁止原样朗读）：{page['teacher_note']}"
                )
        result = await router.chat(
            messages=[{"role": "user", "content": _NARRATION_PROMPT_HEAD + "\n".join(lines)}],
            temperature=0.4,
            max_tokens=4000,
            request_id=f"courseware-narration-{uuid.uuid4().hex[:12]}",
            scene="classroom_courseware",
        )
        content = result.get("content", "") if isinstance(result, dict) else ""
        parsed = _parse_narrations(content)
        out: dict[int, str] = {}
        for order, text in parsed.items():
            narration = _sanitize_narration(text)
            if _valid_classroom_narration(narration):
                out[order] = narration
        return out

    # 按批生成正文页；封面已在上方排除，由确定性开场文案负责。
    for start in range(0, len(pages), batch_size):
        try:
            cleaned.update(await _request(pages[start : start + batch_size]))
        except Exception as exc:  # noqa: BLE001 - 部分批次失败仍保留其它批次
            logger.warning("courseware_narration_llm_failed", error=str(exc)[:200])
            failures.append(type(exc).__name__)

    missing = [p for p in pages if p["order"] not in cleaned]
    if missing:
        try:
            cleaned.update(await _request(missing[:batch_size]))
        except Exception as exc:  # noqa: BLE001 - 重试失败仍走确定性讲稿
            logger.warning("courseware_narration_retry_failed", error=str(exc)[:200])
            failures.append(type(exc).__name__)

    missing_orders = [p["order"] for p in pages if p["order"] not in cleaned]
    if not cleaned:
        return {}, "讲稿生成返回空结果，已使用确定性讲稿"
    if missing_orders or failures:
        return cleaned, (
            f"第 {'、'.join(str(o) for o in missing_orders[:8])} 页讲稿生成缺失或不合格，"
            "已使用确定性讲稿"
        )
    return cleaned, None


# ---------------------------------------------------------------------------
# blocks / 资产 / 内部 slide 构建
# ---------------------------------------------------------------------------


def _blocks_from_elements(slide: dict, asset_ids: list[str]) -> list[dict]:
    """元素 → 学生课堂 blocks（给 AI 上下文 / 练习生成 / 白板公式用）。"""
    blocks: list[dict] = []
    for element in slide.get("elements") or []:
        if not isinstance(element, dict):
            continue
        role = str(element.get("role") or "")
        text = _clip(element.get("text"), 400)
        latex = _clip(element.get("latex"), 2000)
        if role == "definition":
            if text:
                blocks.append(
                    {
                        "kind": "theorem",
                        "title": _clip(element.get("title") or "定义", 60),
                        "body": text,
                    }
                )
        elif role == "formula":
            if latex:
                block: dict[str, Any] = {"kind": "latex", "latex": latex}
                if text:
                    block["text"] = _clip(text, 200)
                blocks.append(block)
        elif role == "knowledge_card":
            if latex:
                blocks.append({"kind": "latex", "latex": latex})
            items = [_clip(item, 200) for item in element.get("items") or [] if str(item).strip()]
            if items:
                blocks.append(
                    {
                        "kind": "note",
                        "title": _clip(element.get("title") or "要点", 60),
                        "text": "；".join(items),
                    }
                )
        elif role in ("bullets", "steps"):
            items = [_clip(item, 200) for item in element.get("items") or [] if str(item).strip()]
            if items:
                if role == "steps":
                    rendered = "；".join(
                        f"{_CIRCLED[pos] if pos < len(_CIRCLED) else f'{pos + 1}.'} {item}"
                        for pos, item in enumerate(items[:10])
                    )
                else:
                    rendered = "；".join(items[:10])
                blocks.append({"kind": "text", "text": rendered})
        elif role == "table":
            rows = [str(row) for row in element.get("items") or [] if str(row).strip()]
            if len(rows) >= 2:
                headers = [cell.strip() for cell in rows[0].split("|")][:8]
                body = [
                    [cell.strip() for cell in row.split("|")][: len(headers)] for row in rows[1:9]
                ]
                if headers and body:
                    blocks.append({"kind": "table", "headers": headers, "rows": body})
        elif role == "note":
            if text:
                blocks.append({"kind": "note", "text": text})
        elif role == "figure":
            block = {
                "kind": "figure",
                "caption": _clip(element.get("text") or slide.get("title") or "图形", 120),
            }
            if asset_ids:
                block["asset_id"] = asset_ids[0]
            if slide.get("math_object"):
                block["math_object"] = slide["math_object"]
            blocks.append(block)
        else:
            if latex:
                blocks.append({"kind": "latex", "latex": latex})
            elif text:
                blocks.append({"kind": "text", "text": text})

    if not blocks:
        content = _clip(slide.get("content"), 1200)
        if content:
            blocks.append({"kind": "text", "text": content})
    return blocks


def _asset_urls(slide: dict) -> dict[str, str]:
    urls: dict[str, str] = {}
    for asset in slide.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        asset_id = asset.get("id")
        mime = asset.get("mime_type")
        data = asset.get("data_base64")
        if asset_id and mime and data:
            urls[str(asset_id)] = f"data:{mime};base64,{data}"
    return urls


def _build_slides_and_outlines(
    payload: dict, ai_narrations: dict[int, str]
) -> tuple[list[dict], list[dict], list[str], bool]:
    """把发布载荷构造成学生课堂内部 slides / outlines。

    返回 (slides, outlines, warnings, degraded)。课堂讲稿**永远从页面内容生成**：
    教师 speaker_note 是备课备注，不能直接读给学生；它只作为讲稿模型的意图上下文。
    任何缺讲稿的页面都走确定性讲稿，因此 degraded=True 必须如实反映出来。
    """
    raw_slides = [s for s in (payload.get("slides") or []) if isinstance(s, dict)]
    deck_meta = {
        "title": _clip(payload.get("title"), 200),
        "textbook_line": _clip(payload.get("textbook_line"), 200),
        "theme": "golden-b",
    }
    slides: list[dict] = []
    outlines: list[dict] = []
    warnings: list[str] = []
    degraded = False

    for index, raw in enumerate(raw_slides):
        order = index + 1
        title = _clip(raw.get("title") or f"第 {order} 页", 120)
        assets = _asset_urls(raw)
        asset_ids = list(assets.keys())
        elements = [e for e in (raw.get("elements") or []) if isinstance(e, dict)]
        is_cover = _is_cover_slide(raw)
        ai_narration = ai_narrations.get(order)
        if is_cover:
            raw_content = str(raw.get("content") or "")
            cover_sub = (
                _sanitize_narration(raw_content)
                if len(raw_content.strip()) <= 60 and "\n" not in raw_content
                else ""
            )
            narration = _derive_cover_narration(title, cover_sub)
            narration_source = "cover"
        elif ai_narration and _valid_classroom_narration(ai_narration):
            narration = _sanitize_narration(ai_narration)
            narration_source = "ai"
        else:
            narration = _derive_narration(
                title,
                elements,
                content=str(raw.get("content") or ""),
                formulas=_formula_units(elements),
                figure_hint=_figure_hint(raw),
            )
            narration_source = "derived"
            degraded = True
            reason = "AI 讲稿缺失或不合格" if ai_narration else "未生成 AI 讲稿"
            warnings.append(f"第 {order} 页{reason}，使用确定性讲稿")
        blocks = _blocks_from_elements(raw, asset_ids)
        slide = {
            "order": order,
            "title": title,
            "kp_code": "",
            "narration": narration,
            "minutes": SLIDE_MINUTES,
            "blocks": blocks,
            "key_points": [],
            "deck_slide": {
                "id": raw.get("id"),
                "type": raw.get("type"),
                "title": title,
                "elements": raw.get("elements") or [],
                "math_object": raw.get("math_object"),
                "layout_hint": raw.get("layout_hint"),
            },
            "deck_meta": deck_meta,
            "asset_urls": assets,
            "narration_source": narration_source,
            "verification_result": {"status": "verified"},
        }
        slides.append(slide)
        outlines.append(
            {
                "order": order,
                "type": raw.get("type") or "content",
                "title": title,
                "subtitle": "",
                "kp_code": "",
                "key_points": [],
                "narration": narration,
                "minutes": SLIDE_MINUTES,
            }
        )
    return slides, outlines, warnings, degraded


def _narration_status(slides: list[dict]) -> str:
    """整份课件的学生讲稿来源状态（AI / 确定性降级 / 混合）。封面不计入降级。"""
    sources = {slide.get("narration_source") for slide in slides}
    if sources <= {"ai", "cover"}:
        return "ai"
    if sources <= {"derived", "cover"}:
        return "fallback"
    return "mixed"


# ---------------------------------------------------------------------------
# 后台准备
# ---------------------------------------------------------------------------


async def _fallback_practice(payload: dict, slides: list[dict], outlines: list[dict]) -> dict:
    """复用 stage_router 的确定性保底练习（lazy import，保证补丁可测）。"""
    from app.domains.classroom import stage_router

    return stage_router._fallback_practice(_clip(payload.get("title"), 200), outlines, slides)


async def _gen_practice_safe(
    payload: dict, slides: list[dict], outlines: list[dict]
) -> tuple[dict, str, str | None]:
    """生成分层练习；失败 → fallback。返回 (practice, status, warning)。"""
    try:
        from app.domains.classroom import stage_router

        practice = await stage_router._gen_practice(
            _clip(payload.get("title"), 200), outlines, slides
        )
        if practice:
            return practice, "ready", None
        return (
            await _fallback_practice(payload, slides, outlines),
            "fallback",
            "AI 练习生成暂不可用，已切换为本课保底复习题",
        )
    except Exception as exc:  # noqa: BLE001 - 练习失败不得阻断课堂发布
        logger.warning("courseware_practice_generation_failed", error=str(exc)[:200])
        return (
            await _fallback_practice(payload, slides, outlines),
            "fallback",
            "AI 练习生成暂不可用，已切换为本课保底复习题",
        )


async def _confirm_and_publish(db: AsyncSession, artifact: TeachingArtifact) -> None:
    """draft → confirmed → published（复用 M3 状态机；稳定幂等键 + 审计）。"""
    # 刚 commit 过的行，server onupdate 列（updated_at）处于 expired 状态；
    # artifacts._serialize_artifact 会同步读这些列，必须先异步 refresh，
    # 否则会在 greenlet 之外触发 IO（MissingGreenlet）。
    await db.refresh(artifact)
    if artifact.status not in ("draft", "confirmed"):
        # 期间被教师下架/已发布 → 不复活、不重复发布
        return
    artifact_id = str(artifact.id)
    if artifact.status == "draft":
        await confirm_artifact(
            db,
            artifact.owner_id,
            artifact.id,
            client_request_id=f"courseware:{artifact_id}:confirm",
            idempotency_key=f"courseware:{artifact_id}:confirm",
        )
        # confirm 的写操作会在 publish 的幂等查询触发 autoflush 时落地，
        # 并让 updated_at（onupdate）过期；先 flush+refresh 保证下一次序列化
        # 全部命中已加载列，避免在 greenlet 之外触发 IO。
        await db.flush()
        await db.refresh(artifact)
    if artifact.status == "confirmed":
        await publish_artifact(
            db,
            artifact.owner_id,
            artifact.id,
            client_request_id=f"courseware:{artifact_id}:publish",
            idempotency_key=f"courseware:{artifact_id}:publish",
        )


async def prepare_courseware(artifact_id: str) -> None:
    """后台准备：讲稿 → blocks/资产 → 练习 → 发布。

    学生只有在 published 后才可见；准备失败也必须发布（degraded=true + warnings），
    但数据库/状态机真实失败时保持原状态并记录 warning，绝不伪成功。
    """
    if artifact_id in _active_preparations:
        return
    _active_preparations.add(artifact_id)
    try:
        async with background_session_factory() as db:
            artifact = await db.get(TeachingArtifact, uuid.UUID(artifact_id))
            if (
                artifact is None
                or artifact.deleted_at is not None
                or artifact.status in ("archived", "published")
            ):
                return

            payload = dict(artifact.payload or {})
            if artifact.status == "confirmed":
                # 崩溃在 confirm 与 publish 之间：内容已就绪时补齐发布
                if payload.get("prepared_at"):
                    await _confirm_and_publish(db, artifact)
                    await db.commit()
                return
            if payload.get("prepared_at"):
                # 已构建过内容（上次发布阶段失败）→ 只补发布，不重复调模型
                await _confirm_and_publish(db, artifact)
                await db.commit()
                return

            raw_slides = [s for s in (payload.get("slides") or []) if isinstance(s, dict)]
            # 每一页都要生成学生讲稿；教师备注只作为教学意图上下文，绝不直接朗读。
            pending_pages = [
                _narration_page(raw, index + 1) for index, raw in enumerate(raw_slides)
            ]

            ai_narrations: dict[int, str] = {}
            narration_warning: str | None = None
            if pending_pages:
                ai_narrations, narration_warning = await _generate_narrations(
                    _clip(payload.get("title"), 200), pending_pages
                )

            slides, outlines, build_warnings, degraded = _build_slides_and_outlines(
                payload, ai_narrations
            )
            warnings = [_clip(w, 200) for w in (payload.get("warnings") or []) if str(w).strip()]
            warnings.extend(build_warnings)
            if narration_warning:
                warnings.append(narration_warning)
                degraded = True

            practice, practice_status, practice_warning = await _gen_practice_safe(
                payload, slides, outlines
            )
            if practice_warning:
                warnings.append(practice_warning)
                degraded = True

            payload.update(
                {
                    "slides": slides,
                    "outlines": outlines,
                    "practice": practice,
                    "practice_status": practice_status,
                    "narration_status": _narration_status(slides),
                    "warnings": warnings,
                    "prepared_at": _now().isoformat(),
                    "page_count": len(slides),
                    "degraded": degraded,
                }
            )
            artifact.payload = payload
            artifact.warnings = warnings
            artifact.degraded = degraded
            artifact.engine = "model_router" if ai_narrations else "local"
            await db.commit()

            await _confirm_and_publish(db, artifact)
            await db.commit()
            logger.info(
                "courseware_prepared",
                courseware_id=artifact_id,
                status=artifact.status,
                pages=len(slides),
                degraded=degraded,
            )
    except Exception as exc:  # noqa: BLE001 - 后台任务必须有兜底
        logger.exception("courseware_prepare_failed", courseware_id=artifact_id)
        try:
            await _prepare_degraded(artifact_id, str(exc)[:200])
        except Exception:  # noqa: BLE001 - 兜底失败只记录，不吞掉主日志
            logger.exception("courseware_prepare_degraded_failed", courseware_id=artifact_id)
    finally:
        _active_preparations.discard(artifact_id)


async def _prepare_degraded(artifact_id: str, error_message: str) -> None:
    """准备异常时的诚实降级：确定性讲稿 + 保底练习 + 发布，degraded=true。"""
    async with background_session_factory() as db:
        artifact = await db.get(TeachingArtifact, uuid.UUID(artifact_id))
        if (
            artifact is None
            or artifact.deleted_at is not None
            or artifact.status in ("archived", "published")
        ):
            return

        payload = dict(artifact.payload or {})
        if artifact.status == "confirmed":
            if payload.get("prepared_at"):
                await _confirm_and_publish(db, artifact)
                await db.commit()
            return

        warnings = [_clip(w, 200) for w in (payload.get("warnings") or []) if str(w).strip()]
        warnings.append(f"课件准备过程异常，已降级发布：{_clip(error_message, 120)}")

        if not payload.get("prepared_at"):
            slides, outlines, build_warnings, _ = _build_slides_and_outlines(payload, {})
            practice = await _fallback_practice(payload, slides, outlines)
            payload.update(
                {
                    "slides": slides,
                    "outlines": outlines,
                    "practice": practice,
                    "practice_status": "fallback",
                    "narration_status": _narration_status(slides),
                    "warnings": warnings + build_warnings,
                    "prepared_at": _now().isoformat(),
                    "page_count": len(slides),
                    "degraded": True,
                }
            )
        else:
            payload["warnings"] = warnings
            payload["degraded"] = True

        artifact.payload = payload
        artifact.warnings = payload["warnings"]
        artifact.degraded = True
        await db.commit()
        await _confirm_and_publish(db, artifact)
        await db.commit()


# ---------------------------------------------------------------------------
# 教师端：列表 / 详情 / 监控
# ---------------------------------------------------------------------------


def _authored_artifacts_stmt(teacher_id: uuid.UUID):
    return select(TeachingArtifact).where(
        TeachingArtifact.owner_id == teacher_id,
        TeachingArtifact.artifact_type == COURSEWARE_ARTIFACT_TYPE,
        TeachingArtifact.scene == COURSEWARE_SCENE,
        TeachingArtifact.deleted_at.is_(None),
        TeachingArtifact.status.in_(("draft", "confirmed", "published")),
    )


async def _sessions_for_artifacts(
    db: AsyncSession, artifact_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ClassroomSession]]:
    if not artifact_ids:
        return {}
    stmt = select(ClassroomSession).where(
        ClassroomSession.deleted_at.is_(None),
        ClassroomSession.source_type == COURSEWARE_SOURCE_TYPE,
        ClassroomSession.source_ref["courseware_artifact_id"].astext.in_(
            [str(aid) for aid in artifact_ids]
        ),
    )
    rows = (await db.execute(stmt)).scalars().all()
    grouped: dict[uuid.UUID, list[ClassroomSession]] = {}
    for session in rows:
        ref = session.source_ref or {}
        try:
            artifact_id = uuid.UUID(str(ref.get("courseware_artifact_id")))
        except (TypeError, ValueError, AttributeError):
            continue
        grouped.setdefault(artifact_id, []).append(session)
    return grouped


def _accuracy_from_stats(stats: dict | None) -> tuple[int, int]:
    answered = 0
    correct = 0
    for tier in ("basic", "advanced", "challenge"):
        tier_stats = (stats or {}).get(tier) if isinstance(stats, dict) else None
        if isinstance(tier_stats, dict):
            answered += int(tier_stats.get("total") or 0)
            correct += int(tier_stats.get("correct") or 0)
    return answered, correct


def _session_progress(session: ClassroomSession | None, page_count: int) -> dict:
    """从真实 progress / practice_stats 提取每生指标（无会话全部为 0）。

    `current_page` 与 `progress.slide_index` 同为 0 基（前端统一 +1 展示）；
    未打开会话返回 None，避免把“没开始”显示成第 1 页。
    """
    if session is None:
        return {
            "opened": False,
            "current_page": None,
            "pages_seen": 0,
            "self_check_ok": 0,
            "self_check_again": 0,
            "practice_answered": 0,
            "practice_correct": 0,
            "last_active": None,
            "completed": False,
            "progress_pct": 0.0,
        }
    progress = session.progress if isinstance(session.progress, dict) else {}
    page_check = progress.get("page_check") if isinstance(progress.get("page_check"), dict) else {}
    ok = sum(1 for value in page_check.values() if value == "ok")
    again = sum(1 for value in page_check.values() if value == "again")

    seen: set[int] = set()
    raw_index = progress.get("slide_index")
    index: int | None = None
    try:
        if raw_index is not None:
            index = int(raw_index)
    except (TypeError, ValueError):
        index = None
    # 会话已创建 = 学生已进入课堂第 1 页（slide_index 0）；进度端点尚未回写时按 0 计。
    if index is None or index < 0:
        index = 0
    if page_count > 0:
        seen.update(range(0, min(index, page_count - 1) + 1))
        for key in page_check:
            try:
                page = int(key)
            except (TypeError, ValueError):
                continue
            if 0 <= page < page_count:
                seen.add(page)
    pages_seen = len(seen)
    current_page = min(index, page_count - 1) if page_count > 0 else 0
    completed = bool(progress.get("completed_at")) or (page_count > 0 and pages_seen >= page_count)
    pct = 100.0 if completed else (round(pages_seen / page_count * 100, 1) if page_count else 0.0)
    answered, correct = _accuracy_from_stats(session.practice_stats)
    return {
        "opened": True,
        "current_page": current_page,
        "pages_seen": pages_seen,
        "self_check_ok": ok,
        "self_check_again": again,
        "practice_answered": answered,
        "practice_correct": correct,
        "last_active": _iso(getattr(session, "updated_at", None)),
        "completed": completed,
        "progress_pct": pct,
    }


def _latest_session_per_user(sessions: list[ClassroomSession]) -> dict[uuid.UUID, ClassroomSession]:
    latest: dict[uuid.UUID, ClassroomSession] = {}
    for session in sessions:
        current = latest.get(session.user_id)
        if current is None:
            latest[session.user_id] = session
            continue
        current_ts = getattr(current, "updated_at", None) or current.created_at
        new_ts = getattr(session, "updated_at", None) or session.created_at
        if new_ts and (current_ts is None or new_ts > current_ts):
            latest[session.user_id] = session
    return latest


async def _class_names(db: AsyncSession, class_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not class_ids:
        return {}
    rows = (await db.execute(select(Class.id, Class.name).where(Class.id.in_(class_ids)))).all()
    return {row[0]: row[1] for row in rows}


async def _class_student_counts(
    db: AsyncSession, class_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not class_ids:
        return {}
    rows = (
        await db.execute(
            select(ClassMember.class_id, func.count(ClassMember.id))
            .where(
                ClassMember.class_id.in_(class_ids),
                ClassMember.member_role == "student",
                ClassMember.confirmed.is_(True),
                ClassMember.deleted_at.is_(None),
            )
            .group_by(ClassMember.class_id)
        )
    ).all()
    return {row[0]: int(row[1]) for row in rows}


async def _user_names(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    rows = (await db.execute(select(User.id, User.nickname).where(User.id.in_(user_ids)))).all()
    return {row[0]: (row[1] or "") for row in rows}


async def list_teacher_coursewares(
    db: AsyncSession,
    *,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID | None,
    deck_id: str | None,
) -> list[dict]:
    stmt = _authored_artifacts_stmt(teacher_id)
    if class_id is not None:
        stmt = stmt.where(TeachingArtifact.class_id == class_id)
    if deck_id:
        stmt = stmt.where(TeachingArtifact.payload["deck_id"].astext == deck_id)
    artifacts = (
        (await db.execute(stmt.order_by(TeachingArtifact.created_at.desc()))).scalars().all()
    )

    artifact_ids = [a.id for a in artifacts]
    sessions_by_artifact = await _sessions_for_artifacts(db, artifact_ids)
    class_ids = list({a.class_id for a in artifacts if a.class_id})
    names = await _class_names(db, class_ids)
    totals = await _class_student_counts(db, class_ids)
    teacher_name = (await _user_names(db, [teacher_id])).get(teacher_id, "")

    items: list[dict] = []
    for artifact in artifacts:
        payload = artifact.payload or {}
        page_count = int(payload.get("page_count") or len(payload.get("slides") or []))
        sessions = sessions_by_artifact.get(artifact.id, [])
        per_user = _latest_session_per_user(sessions)
        metrics = [_session_progress(session, page_count) for session in per_user.values()]
        opened = len(metrics)
        avg_progress = round(sum(m["progress_pct"] for m in metrics) / opened, 1) if opened else 0.0
        answered = sum(m["practice_answered"] for m in metrics)
        correct = sum(m["practice_correct"] for m in metrics)
        total = totals.get(artifact.class_id, 0) if artifact.class_id else 0
        items.append(
            {
                "courseware_id": str(artifact.id),
                "title": payload.get("title") or "",
                "class_id": str(artifact.class_id) if artifact.class_id else None,
                "class_name": names.get(artifact.class_id, "") if artifact.class_id else "",
                "teacher_name": teacher_name,
                "deck_id": payload.get("deck_id"),
                "deck_revision": payload.get("deck_revision"),
                "status": artifact.status,
                "page_count": page_count,
                "degraded": artifact.degraded,
                "published_at": _iso(artifact.published_at),
                "created_at": _iso(artifact.created_at),
                "updated_at": _iso(artifact.updated_at),
                "opened": opened,
                "total": total,
                "students_total": total,
                "avg_progress": avg_progress,
                "avg_progress_pct": avg_progress,
                "practice_accuracy": round(correct / answered * 100, 1) if answered else None,
            }
        )
    return items


async def get_courseware_artifact(
    db: AsyncSession, *, teacher_id: uuid.UUID, courseware_id: uuid.UUID
) -> TeachingArtifact:
    artifact = await get_owned_artifact(db, teacher_id, courseware_id)
    if artifact.artifact_type != COURSEWARE_ARTIFACT_TYPE or artifact.scene != COURSEWARE_SCENE:
        raise_http(_ERR_NOT_FOUND, 404, "not_found", recoverable=False)
    return artifact


async def courseware_detail(db: AsyncSession, artifact: TeachingArtifact) -> dict:
    payload = artifact.payload or {}
    slides = [s for s in (payload.get("slides") or []) if isinstance(s, dict)]
    slides_ready = sum(1 for slide in slides if slide.get("narration") or slide.get("speaker_note"))
    class_name = ""
    if artifact.class_id:
        clazz = await db.get(Class, artifact.class_id)
        class_name = clazz.name if clazz is not None else ""
    if artifact.status == "published":
        stage = "已发布"
    elif artifact.status == "archived":
        stage = "已下架"
    elif payload.get("prepared_at"):
        stage = "发布中"
    else:
        stage = "逐页讲稿与随堂练习准备中"
    narration_status = payload.get("narration_status") or "pending"
    practice_status = payload.get("practice_status") or "pending"
    return {
        "courseware_id": str(artifact.id),
        "title": payload.get("title") or "",
        "class_id": str(artifact.class_id) if artifact.class_id else None,
        "class_name": class_name,
        "deck_id": payload.get("deck_id"),
        "deck_revision": payload.get("deck_revision"),
        "textbook_line": payload.get("textbook_line"),
        "subject": payload.get("subject"),
        "status": artifact.status,
        "page_count": int(payload.get("page_count") or len(slides)),
        "slides_ready": slides_ready,
        "slides_total": len(slides),
        "narration_status": narration_status,
        "practice_status": practice_status,
        # Phase D 详情页消费的准备进度（done/total/stage 与扁平字段同源）
        "preparation": {
            "done": slides_ready,
            "total": len(slides),
            "stage": stage,
            "narration_status": narration_status,
            "practice_status": practice_status,
        },
        "warnings": list(artifact.warnings or payload.get("warnings") or []),
        "degraded": bool(artifact.degraded),
        "source_refs": list(artifact.source_refs or []),
        "prepared_at": payload.get("prepared_at"),
        "published_at": _iso(artifact.published_at),
        "created_at": _iso(artifact.created_at),
        "updated_at": _iso(artifact.updated_at),
    }


async def courseware_monitor(db: AsyncSession, artifact: TeachingArtifact) -> dict:
    """真实学情聚合：班级已确认学生 + 本课件 ClassroomSession 的 progress/practice_stats。"""
    payload = artifact.payload or {}
    page_count = int(payload.get("page_count") or len(payload.get("slides") or []))
    members: list[tuple[ClassMember, User | None]] = []
    if artifact.class_id:
        rows = (
            await db.execute(
                select(ClassMember, User)
                .outerjoin(User, User.id == ClassMember.user_id)
                .where(
                    ClassMember.class_id == artifact.class_id,
                    ClassMember.member_role == "student",
                    ClassMember.confirmed.is_(True),
                    ClassMember.deleted_at.is_(None),
                )
                .order_by(ClassMember.joined_at.asc())
            )
        ).all()
        members = [(row[0], row[1]) for row in rows]

    sessions = (await _sessions_for_artifacts(db, [artifact.id])).get(artifact.id, [])
    per_user = _latest_session_per_user(sessions)

    students: list[dict] = []
    metric_rows: list[dict] = []
    for member, user in members:
        session = per_user.get(member.user_id)
        metrics = _session_progress(session, page_count)
        name = (member.nickname_in_class or "").strip() or (
            (user.nickname if user is not None else "") or ""
        ).strip()
        students.append(
            {
                "user_id": str(member.user_id),
                "name": name,
                "opened": metrics["opened"],
                "current_page": metrics["current_page"],
                "current_page_no": (
                    metrics["current_page"] + 1 if metrics["current_page"] is not None else None
                ),
                "pages_seen": metrics["pages_seen"],
                "self_check_ok": metrics["self_check_ok"],
                "self_check_again": metrics["self_check_again"],
                "practice_answered": metrics["practice_answered"],
                "practice_correct": metrics["practice_correct"],
                "last_active": metrics["last_active"],
            }
        )
        if metrics["opened"]:
            metric_rows.append(metrics)

    title_map = {
        slide.get("order"): _clip(slide.get("title"), 120)
        for slide in (payload.get("slides") or [])
        if isinstance(slide, dict)
    }
    page_dropoff = [
        {
            "page": page_no - 1,  # 0 基，与 progress.slide_index 同口径（前端 +1 展示）
            "page_no": page_no,
            "title": title_map.get(page_no, f"第 {page_no} 页"),
            "reached": sum(1 for m in metric_rows if m["pages_seen"] >= page_no),
            "count": sum(1 for m in metric_rows if m["pages_seen"] >= page_no),
        }
        for page_no in range(1, page_count + 1)
    ]

    opened = len(metric_rows)
    answered = sum(m["practice_answered"] for m in metric_rows)
    correct = sum(m["practice_correct"] for m in metric_rows)
    return {
        "courseware_id": str(artifact.id),
        "title": payload.get("title") or "",
        "class_id": str(artifact.class_id) if artifact.class_id else None,
        "status": artifact.status,
        "page_count": page_count,
        "students_total": len(members),
        "opened": opened,
        "completed": sum(1 for m in metric_rows if m["completed"]),
        "avg_progress_pct": (
            round(sum(m["progress_pct"] for m in metric_rows) / opened, 1) if opened else 0.0
        ),
        "practice_accuracy": round(correct / answered * 100, 1) if answered else None,
        "page_dropoff": page_dropoff,
        "students": students,
        "updated_at": _now().isoformat(),
    }


async def archive_courseware(
    db: AsyncSession, *, teacher_id: uuid.UUID, courseware_id: uuid.UUID
) -> None:
    artifact = await get_courseware_artifact(db, teacher_id=teacher_id, courseware_id=courseware_id)
    await archive_artifact(
        db,
        teacher_id,
        artifact.id,
        client_request_id=f"courseware:{artifact.id}:archive",
        idempotency_key=f"courseware:{artifact.id}:archive",
    )


# ---------------------------------------------------------------------------
# 学生端：课件列表 / 打开课堂
# ---------------------------------------------------------------------------


async def _confirmed_student_class_ids(db: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        await db.execute(
            select(ClassMember.class_id).where(
                ClassMember.user_id == user_id,
                ClassMember.member_role == "student",
                ClassMember.confirmed.is_(True),
                ClassMember.deleted_at.is_(None),
            )
        )
    ).all()
    return [row[0] for row in rows]


async def list_student_coursewares(db: AsyncSession, *, user_id: uuid.UUID) -> list[dict]:
    class_ids = await _confirmed_student_class_ids(db, user_id)
    if not class_ids:
        return []

    artifacts = (
        (
            await db.execute(
                select(TeachingArtifact)
                .join(Class, Class.id == TeachingArtifact.class_id)
                .where(
                    TeachingArtifact.class_id.in_(class_ids),
                    TeachingArtifact.artifact_type == COURSEWARE_ARTIFACT_TYPE,
                    TeachingArtifact.scene == COURSEWARE_SCENE,
                    TeachingArtifact.status == "published",
                    TeachingArtifact.deleted_at.is_(None),
                    Class.deleted_at.is_(None),
                )
                .order_by(
                    TeachingArtifact.published_at.desc().nullslast(),
                    TeachingArtifact.created_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not artifacts:
        return []

    artifact_ids = [a.id for a in artifacts]
    sessions = (
        (
            await db.execute(
                select(ClassroomSession).where(
                    ClassroomSession.user_id == user_id,
                    ClassroomSession.deleted_at.is_(None),
                    ClassroomSession.source_type == COURSEWARE_SOURCE_TYPE,
                    ClassroomSession.source_ref["courseware_artifact_id"].astext.in_(
                        [str(aid) for aid in artifact_ids]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    sessions_by_artifact: dict[str, ClassroomSession] = {}
    for session in sessions:
        ref = session.source_ref or {}
        key = str(ref.get("courseware_artifact_id"))
        current = sessions_by_artifact.get(key)
        if current is None:
            sessions_by_artifact[key] = session
            continue
        current_ts = getattr(current, "updated_at", None) or current.created_at
        new_ts = getattr(session, "updated_at", None) or session.created_at
        if new_ts and (current_ts is None or new_ts > current_ts):
            sessions_by_artifact[key] = session

    names = await _class_names(db, list({a.class_id for a in artifacts if a.class_id}))
    teacher_ids = list({a.owner_id for a in artifacts})
    teacher_rows = (
        await db.execute(select(User.id, User.nickname).where(User.id.in_(teacher_ids)))
    ).all()
    teacher_names = {row[0]: row[1] for row in teacher_rows}

    items: list[dict] = []
    for artifact in artifacts:
        payload = artifact.payload or {}
        page_count = int(payload.get("page_count") or len(payload.get("slides") or []))
        session = sessions_by_artifact.get(str(artifact.id))
        metrics = _session_progress(session, page_count)
        items.append(
            {
                "courseware_id": str(artifact.id),
                "title": payload.get("title") or "",
                "deck_id": payload.get("deck_id"),
                "deck_revision": payload.get("deck_revision"),
                "class_id": str(artifact.class_id) if artifact.class_id else None,
                "class_name": names.get(artifact.class_id, "") if artifact.class_id else "",
                "teacher_name": teacher_names.get(artifact.owner_id, "") or "",
                "page_count": page_count,
                "published_at": _iso(artifact.published_at),
                "opened": metrics["opened"],
                "progress_pct": metrics["progress_pct"],
                "practice_accuracy": (
                    round(metrics["practice_correct"] / metrics["practice_answered"] * 100, 1)
                    if metrics["practice_answered"]
                    else None
                ),
                "last_session_id": str(session.id) if session is not None else None,
            }
        )
    return items


async def open_student_session(
    db: AsyncSession, *, user_id: uuid.UUID, courseware_id: uuid.UUID
) -> ClassroomSession:
    """学生打开老师课件：复用或创建自己的 ClassroomSession（不新增字段/表）。"""
    artifact = await db.get(TeachingArtifact, courseware_id)
    if (
        artifact is None
        or artifact.deleted_at is not None
        or artifact.artifact_type != COURSEWARE_ARTIFACT_TYPE
        or artifact.scene != COURSEWARE_SCENE
        or artifact.status != "published"
    ):
        raise_http(_ERR_NOT_FOUND, 404, "not_found", recoverable=False)

    if artifact.class_id is None:
        raise_http(_ERR_NOT_FOUND, 404, "not_found", recoverable=False)

    membership = (
        await db.execute(
            select(ClassMember).where(
                ClassMember.class_id == artifact.class_id,
                ClassMember.user_id == user_id,
                ClassMember.member_role == "student",
                ClassMember.confirmed.is_(True),
                ClassMember.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise_http(40302, 403, "class_scope_denied", recoverable=False)

    existing = (
        await db.execute(
            select(ClassroomSession)
            .where(
                ClassroomSession.user_id == user_id,
                ClassroomSession.deleted_at.is_(None),
                ClassroomSession.source_type == COURSEWARE_SOURCE_TYPE,
                ClassroomSession.source_ref["courseware_artifact_id"].astext == str(artifact.id),
            )
            .order_by(ClassroomSession.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    payload = dict(artifact.payload or {})
    slides = json.loads(json.dumps(payload.get("slides") or [], ensure_ascii=False))
    outlines = json.loads(json.dumps(payload.get("outlines") or [], ensure_ascii=False))
    practice = payload.get("practice")
    practice_copy = json.loads(json.dumps(practice, ensure_ascii=False)) if practice else None
    title = _clip(payload.get("title") or "老师课堂", 200)

    session = ClassroomSession(
        user_id=user_id,
        title=title,
        mode="sync",
        slide_count=len(slides),
        status="ready",
        outlines=outlines,
        slides=slides,
        engine=COURSEWARE_SOURCE_TYPE,
        generated_at=_now(),
        source_type=COURSEWARE_SOURCE_TYPE,
        source_ref={
            "courseware_artifact_id": str(artifact.id),
            "class_id": str(artifact.class_id),
            "teacher_id": str(artifact.owner_id),
            "courseware_version": artifact.version,
        },
        progress={},
        practice=practice_copy,
        practice_stats={},
        verification={
            "overall": "verified",
            "per_slide": [
                {"idx": slide.get("order", index + 1), "status": "verified"}
                for index, slide in enumerate(slides)
                if isinstance(slide, dict)
            ],
        },
        knowledge_points=[],
        qa_summary={},
        content_version=f"courseware-v{artifact.version}",
    )
    db.add(session)
    await db.flush()
    return session


__all__ = [
    "COURSEWARE_ARTIFACT_TYPE",
    "COURSEWARE_SCENE",
    "COURSEWARE_SOURCE_TYPE",
    "archive_courseware",
    "courseware_detail",
    "courseware_monitor",
    "create_courseware",
    "get_courseware_artifact",
    "is_preparing",
    "list_student_coursewares",
    "list_teacher_coursewares",
    "normalize_courseware_payload",
    "open_student_session",
    "prepare_courseware",
    "schedule_prepare",
]
