"""AI 添加页（R4）：基于锚点页/教师指令生成新页内容 → add_slide + move_slide 提案。

与 regen 同一信任模型：显式 refs（approved blueprint / spec revision / prompt），
生成的 ops 只落在新增页上（dry-run AffectedSlideSet = {new_slide_id} ∪ 页序影响）；
禁止空白假页——AI 返回空内容即 503 诚实报错，不落占位页。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorCode
from app.ppt_v2.adapters.ai import AIPlatformAdapter
from app.ppt_v2.service import spec_edit
from app.ppt_v2.service.regen import get_ai_adapter, validate_refs

_ADD_PAGE_SYSTEM = (
    "你是一名中学数学教研助手。基于锚点页所属的教学环节与教师要求，为一节高中数学课件"
    "新增一页教学内容：标题不超过 16 字；正文用 \\n 分条，2-4 条，每条不超过 80 字，"
    "数学公式用 LaTeX 记号 $...$。"
    "输出单个 JSON 对象，键为 title、content、teaching_goal（一句话教学意图）。不要输出解释。"
)

_SLIDE_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("练习", "practice"),
    ("例题", "worked-example"),
    ("小结", "summary"),
    ("引入", "introduction"),
)


def _new_slide_id(spec: Any) -> str:
    existing = {s.id for s in spec.slides}
    n = len(spec.slides)
    while f"slide-{n:02d}" in existing:
        n += 1
    if n < 100:
        return f"slide-{n:02d}"
    return f"slide-{uuid.uuid4().hex[:8]}"


def _derive_slide_type(instruction: str) -> str:
    for keyword, slide_type in _SLIDE_TYPE_KEYWORDS:
        if keyword in instruction:
            return slide_type
    return "concept"


async def add_generated_page(
    session: AsyncSession,
    deck_id: str,
    *,
    expected_revision: int,
    after_slide_id: str,
    instruction: str,
    refs: dict[str, Any] | None,
    actor_id: str | None = None,
    adapter: AIPlatformAdapter | None = None,
) -> dict[str, Any]:
    row = await spec_edit.load_deck_row(session, deck_id)
    spec = spec_edit.load_spec(row)
    if expected_revision != row.revision:
        payload = await spec_edit._build_conflict(  # noqa: SLF001 - 与 regen 同口径
            session, row, expected_revision=expected_revision, operations=[]
        )
        raise spec_edit._conflict_error(payload)  # noqa: SLF001

    checked_refs = validate_refs(spec, refs, expected_revision)
    anchor = next((s for s in spec.slides if s.id == after_slide_id), None)
    if anchor is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"锚点页 {after_slide_id} 不存在")

    ai = adapter or get_ai_adapter()
    user_lines = [
        f"课件标题：{spec.metadata.title}",
        f"锚点页标题：{anchor.title}",
        f"锚点页正文：{anchor.content}",
        f"锚点页类型：{anchor.type}",
        f"教师要求：{instruction or '围绕锚点页的教学环节补充一页内容'}",
        f"教材依据：{', '.join(anchor.source_refs or []) or '（无）'}",
    ]
    try:
        reply = await ai.chat(
            [
                {"role": "system", "content": _ADD_PAGE_SYSTEM},
                {"role": "user", "content": "\n".join(user_lines)},
            ],
            use_case="ppt_regen",
        )
    except Exception as exc:  # noqa: BLE001 - provider 失败诚实报出
        raise ApiError(
            ErrorCode.DEPENDENCY_UNAVAILABLE, f"新增页生成 provider 调用失败: {type(exc).__name__}: {exc}"
        ) from exc

    title, content, goal = _extract_page(reply.text)
    if not content:
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "新增页生成 provider 返回空内容")

    new_id = _new_slide_id(spec)
    slide_type = _derive_slide_type(instruction)
    new_slide_value: dict[str, Any] = {
        "type": slide_type,
        "teaching_goal": goal or title or "教学环节",
        "title": title or "新页面",
        "content": content,
        "source_refs": list(anchor.source_refs or []),
        "visual_intent": "",
        "layout_hint": slide_type,
    }
    anchor_index = next(i for i, s in enumerate(spec.slides) if s.id == after_slide_id)
    operations = [
        {"op": "add_slide", "target": new_id, "value": new_slide_value},
        {"op": "move_slide", "target": new_id, "value": {"index": anchor_index + 1}},
    ]
    proposal = await spec_edit.create_proposal(
        session,
        deck_id,
        expected_revision=expected_revision,
        summary=f"AI 新增页（锚点 {after_slide_id}）",
        source="regen",
        operations=operations,
        instruction=instruction,
        refs=checked_refs,
        actor_id=actor_id,
    )
    proposal["target"] = {"kind": "slide", "slide_id": new_id, "after_slide_id": after_slide_id}
    proposal["added_slide_id"] = new_id
    proposal["refs_echo"] = checked_refs
    proposal["provider"] = {"model": reply.model, "run_id": f"page-add-{uuid.uuid4().hex[:12]}"}
    return proposal


def _extract_page(text: str) -> tuple[str, str, str]:
    import json

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            title = str(payload.get("title") or "").strip()[:40]
            content = str(payload.get("content") or "").strip()
            goal = str(payload.get("teaching_goal") or "").strip()
            return title, content[:2000], goal
    except (ValueError, AttributeError):
        pass
    # 模型未按 JSON 回复：整段按正文处理（真实输出，不伪造结构）
    return "", cleaned[:2000], ""
