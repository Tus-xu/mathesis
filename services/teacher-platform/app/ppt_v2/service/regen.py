"""局部重生成（T24）：只重生成目标页/目标块，并显式引用上游事实。

为什么必须显式带 refs：重生成的合法性来自"基于哪个已确认的 Blueprint 版本、哪份
Context、哪条证据"。不记这些，产物就无法回溯，也无法证明没有偷用别的上下文。

不变量（INV-2/INV-3）：
- 生成的 ops 只落在目标 slide 上，dry-run 的 AffectedSlideSet 必须等于 {target}；
- 任何试图改写 spec.projection 的引用一律拒绝（只允许引用，不允许改写上游事实）。
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorCode
from app.ppt_v2.adapters.ai import AIPlatformAdapter
from app.ppt_v2.service import spec_edit

_REQUIRED_REFS = (
    "approved_blueprint_id",
    "blueprint_revision",
    "spec_revision",
    "prompt_ref",
    "context_manifest_id",
)

_SYSTEM_PROMPT = (
    "你是一名中学数学教研助手。只重写教师指定的那一页内容，保持其余结构不变；"
    "必须围绕本课件标题、本页标题与本页正文所属的数学主题扩写，"
    "严禁引入与本页无关的知识点（例如数列页不得改写成椭圆/函数/概率内容）。"
    "输出单个 JSON 对象，键为 content（页面正文纯文本，不超过 300 字）。不要输出解释。"
)

# AI 快捷操作「生成推导演示」：产出可直接逐步播放的编号推导链（前端 steps + reveal）。
_SYSTEM_PROMPT_DERIVATION = (
    "你是一名中学数学教研助手。请基于教师指定页的标题、正文与所属教学阶段，"
    "补全一条**可直接投影的分步推导链**。"
    "硬约束：① 只能使用本课件标题/本页标题所属的数学主题，严禁切换到其他章节"
    "（例如等差数列页不得出现椭圆、抛物线、函数定义域等内容）；"
    "② 每一步只写一个动作，按 1. 2. 3. 编号，每步一行，含关键公式（LaTeX 用 $...$）；"
    "③ 总步数 4–8 步，总字数不超过 600 字；④ 不写教师动作、不写“引导学生”等批注。"
    "输出单个 JSON 对象，键为 content（上述编号步骤纯文本）。不要输出解释。"
)

# R4 AI 快捷操作「生成相似练习题」：基于本页生成随堂练习（追加到该页备注，不改动正文）
_SYSTEM_PROMPT_PRACTICE = (
    "你是一名中学数学教研助手。基于教师指定的那一页教学内容，生成 2 道与该页知识点"
    "同类的随堂练习题（含小问与答案提示；数学公式用 LaTeX 记号 $...$）；"
    "题目必须与课件标题、本页标题所属主题一致，严禁切换到无关章节。"
    "输出单个 JSON 对象，键为 items（字符串数组，每题一条，每条不超过 120 字）。不要输出解释。"
)

RegenMode = Literal["content", "note_practice", "derivation"]

# ── 主题一致性护栏（确定性，不依赖模型自证）───────────────────────────────
# 每个主题用“强特征词”而不是泛词：避免“函数”这类词把数列页误判成函数页，
# 也避免“圆”命中“椭圆”。生成内容只要出现一个与本页/本课主题不相交的强主题，
# 且没有本页主题特征 → 判定为串题，拒绝落库（页面保持原样）。
_TOPIC_LEXICON: dict[str, tuple[str, ...]] = {
    "sequence": ("数列", "等差", "等比", "通项", "前n项和", "前 n 项和", "递推公式"),
    "ellipse": ("椭圆", "长轴", "短轴", "离心率", "焦点在"),
    "hyperbola": ("双曲线", "渐近线", "实轴", "虚轴"),
    "parabola": ("抛物线", "准线", "焦点到"),
    "set": ("集合", "交集", "并集", "补集", "子集", "韦恩图", "venn"),
    "vector": ("向量", "数量积", "点积", "共线向量", "夹角余弦"),
    "probability": ("概率", "随机变量", "分布列", "数学期望", "方差", "二项分布"),
    "derivative": ("导数", "极值", "切线方程", "导函数", "单调递增", "单调递减"),
    "trig": ("三角函数", "正弦", "余弦", "正切", "弧度制", "诱导公式"),
    "solid": ("立体几何", "棱柱", "棱锥", "圆锥", "圆柱", "异面直线", "线面角", "二面角"),
    "complex": ("复数", "共轭复数", "虚部", "实部", "复平面"),
}


def _topics_of(text: str) -> set[str]:
    blob = str(text or "").lower()
    return {
        topic
        for topic, words in _TOPIC_LEXICON.items()
        if any(word.lower() in blob for word in words)
    }


def _deck_topic_text(spec: Any, slide: Any) -> str:
    """本课/本页的“允许主题”来源。

    关键：**不包含 slide.content / speaker_note**。这两个字段可能已被上一次串题生成污染，
    一旦纳入，护栏会把污染页的椭圆内容当成“本页主题”，从此永远放行（自我豁免）。
    只使用不可被单页 regen 改写的上游事实：deck 标题、outline 目标、教学策略、
    页标题、教学目标。
    """
    outline_goal = ""
    for entry in getattr(spec, "outline", []) or []:
        if getattr(slide, "id", None) in (getattr(entry, "slide_ids", None) or []):
            outline_goal = str(getattr(entry, "goal", "") or "")
            break
    strategy = getattr(spec, "teaching_strategy", None)
    strategy_text = " ".join(
        [
            str(getattr(strategy, "intro", "") or ""),
            " ".join(getattr(strategy, "concept_progression", []) or []),
            " ".join(getattr(strategy, "derivation_chain", []) or []),
        ]
    )
    return "\n".join(
        [
            str(getattr(getattr(spec, "metadata", None), "title", "") or ""),
            outline_goal,
            strategy_text,
            str(getattr(slide, "title", "") or ""),
            str(getattr(slide, "teaching_goal", "") or ""),
        ]
    )


def _reject_topic_drift(spec: Any, slide: Any, generated: str) -> None:
    """生成内容出现本课主题之外的强主题 → 拒绝落库（不静默接受串题内容）。

    - 用 deck 标题/outline/策略/页标题/教学目标 作为允许集合，**不看已被污染的正文**；
    - 只要生成内容带有允许集合之外的强主题（如数列课出现“椭圆/抛物线/双曲线”），
      即使同时提到了“数列”也拒绝（旧实现是“交集非空即放行”，挡不住混写）。
    """
    deck_topics = _topics_of(_deck_topic_text(spec, slide))
    if not deck_topics:
        return
    generated_topics = _topics_of(generated)
    if not generated_topics:
        return
    drift = generated_topics - deck_topics
    if not drift:
        return
    raise ApiError(
        ErrorCode.VALIDATION,
        "AI 输出偏离本页主题（本页 " f"{sorted(deck_topics)}，生成 {sorted(generated_topics)}），已拒绝落库；请重试或补充更明确的指令",
    )



def get_ai_adapter() -> AIPlatformAdapter:
    return AIPlatformAdapter()


def validate_refs(spec: Any, refs: dict[str, Any] | None, expected_revision: int) -> dict[str, Any]:
    if not isinstance(refs, dict):
        raise ApiError(ErrorCode.VALIDATION, "局部重生成必须提供 refs（上游事实引用）")
    missing = [k for k in _REQUIRED_REFS if not refs.get(k)]
    if missing:
        raise ApiError(ErrorCode.VALIDATION, f"refs 缺少必填项: {missing}")
    projection = spec.projection
    if projection is None:
        raise ApiError(ErrorCode.VALIDATION, "deck 缺少 approved projection，禁止重生成")
    if refs["approved_blueprint_id"] != projection.approved_blueprint_id:
        raise ApiError(
            ErrorCode.CONFLICT,
            "refs.approved_blueprint_id 与当前 spec 的已确认 Blueprint 不一致（上游事实不可偷换）",
        )
    if projection.approved_blueprint_revision is not None and int(refs["blueprint_revision"]) != int(
        projection.approved_blueprint_revision
    ):
        raise ApiError(ErrorCode.CONFLICT, "refs.blueprint_revision 与 spec.projection 不一致")
    if int(refs["spec_revision"]) != expected_revision:
        raise ApiError(ErrorCode.CONFLICT, "refs.spec_revision 不是本次提交所基于的版本")
    prompt_ref = refs.get("prompt_ref") or {}
    if not isinstance(prompt_ref, dict) or not prompt_ref.get("prompt_id"):
        raise ApiError(ErrorCode.VALIDATION, "refs.prompt_ref.prompt_id 必填")
    return refs


def _slide_element_text(slide: Any) -> str:
    """本页结构化元素的可见文本（derive 必须看到 elements，而不是只有可能过时的 content）。"""
    out: list[str] = []
    for el in getattr(slide, "elements", []) or []:
        role = str(getattr(el, "role", "") or "")
        text = str(getattr(el, "text", "") or "").strip()
        latex = str(getattr(el, "latex", "") or "").strip()
        items = [str(x).strip() for x in (getattr(el, "items", []) or []) if str(x).strip()]
        title = str(getattr(el, "title", "") or "").strip()
        if role == "figure":
            continue
        if title:
            out.append(f"[{role}] {title}")
        if text:
            out.append(f"[{role}] {text}")
        if latex:
            out.append(f"[{role}] ${latex}$")
        out.extend(f"[{role}] {it}" for it in items)
    return "\n".join(out[:40])


def _prompt(
    spec: Any,
    slide: Any,
    instruction: str,
    refs: dict[str, Any],
    system_prompt: str = _SYSTEM_PROMPT,
) -> list[dict[str, str]]:
    evidence = refs.get("evidence_refs") or []
    source_refs = refs.get("source_refs") or list(slide.source_refs or [])
    note = getattr(slide, "speaker_note", None)
    strategy = getattr(spec, "teaching_strategy", None)
    strategy_bits = [
        str(getattr(strategy, "intro", "") or ""),
        " → ".join(getattr(strategy, "concept_progression", []) or []),
        " → ".join(getattr(strategy, "derivation_chain", []) or []),
    ]
    strategy_text = "；".join(bit for bit in strategy_bits if bit) or "（未指定）"
    outline_goal = ""
    for entry in getattr(spec, "outline", []) or []:
        if getattr(slide, "id", None) in (getattr(entry, "slide_ids", None) or []):
            outline_goal = str(getattr(entry, "goal", "") or "")
            break
    lines = [
        f"课件标题：{getattr(getattr(spec, 'metadata', None), 'title', '') or '（未命名）'}",
        f"本页环节：{getattr(slide, 'type', '') or '（未标注）'}"
        + (f" · 教学目标：{getattr(slide, 'teaching_goal', '')}" if getattr(slide, "teaching_goal", None) else ""),
        f"页面标题：{slide.title}",
        f"当前正文：{slide.content}",
        f"当前版式元素：\n{_slide_element_text(slide) or '（无）'}",
        f"教师要求：{instruction}",
        f"教材依据：{', '.join(source_refs) if source_refs else '（无）'}",
        f"证据引用：{', '.join(evidence) if evidence else '（无）'}",
        f"教学策略：{strategy_text}",
    ]
    if outline_goal:
        lines.append(f"本页所属教学阶段目标：{outline_goal}")
    if note:
        lines.append(f"本页备注：{note}")
    lines.append("硬约束：只改写本页，内容必须与上述课件标题/页面标题属于同一数学主题；不得切换到无关章节。")
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _extract_content(text: str) -> str:
    import json

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
    try:
        payload = json.loads(cleaned)
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    except (ValueError, AttributeError):
        pass
    # 模型没按 JSON 回复时，退化为纯文本（仍然是模型真实输出，不是伪造内容）
    return cleaned[:2000].strip()


def _extract_practice_items(text: str) -> list[str]:
    """解析练习生成回复 → 条目列表（JSON {items:[…]} 优先，退化按行切分）。"""
    import json

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
    try:
        payload = json.loads(cleaned)
        items = payload.get("items")
        if isinstance(items, list):
            out = [str(it).strip() for it in items if str(it).strip()]
            if out:
                return out[:5]
    except (ValueError, AttributeError):
        pass
    lines = [ln.strip(" -*0123456789.、") for ln in cleaned.splitlines()]
    return [ln for ln in (s.strip() for s in lines) if ln][:5]


async def regenerate_slide(
    session: AsyncSession,
    deck_id: str,
    *,
    expected_revision: int,
    slide_id: str,
    instruction: str,
    refs: dict[str, Any] | None,
    actor_id: str | None = None,
    adapter: AIPlatformAdapter | None = None,
    mode: RegenMode = "content",
) -> dict[str, Any]:
    row = await spec_edit.load_deck_row(session, deck_id)
    spec = spec_edit.load_spec(row)
    if expected_revision != row.revision:
        payload = await spec_edit._build_conflict(  # noqa: SLF001 - 同一服务内部复用冲突载荷
            session, row, expected_revision=expected_revision, operations=[]
        )
        raise spec_edit._conflict_error(payload)  # noqa: SLF001

    checked_refs = validate_refs(spec, refs, expected_revision)
    slide = next((s for s in spec.slides if s.id == slide_id), None)
    if slide is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"slide {slide_id} 不存在")

    use_case = "ppt_regen"
    if mode == "note_practice":
        system_prompt = _SYSTEM_PROMPT_PRACTICE
    elif mode == "derivation":
        system_prompt = _SYSTEM_PROMPT_DERIVATION
    else:
        system_prompt = _SYSTEM_PROMPT
    ai = adapter or get_ai_adapter()
    try:
        reply = await ai.chat(_prompt(spec, slide, instruction, checked_refs, system_prompt), use_case=use_case)
    except Exception as exc:  # noqa: BLE001 - provider 失败必须诚实报出，不产出占位内容
        raise ApiError(
            ErrorCode.DEPENDENCY_UNAVAILABLE, f"重生成 provider 调用失败: {type(exc).__name__}: {exc}"
        ) from exc

    if mode == "note_practice":
        items = _extract_practice_items(reply.text)
        if not items:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "练习生成 provider 返回空内容")
        generated = "\n".join(f"{i}. {t}" for i, t in enumerate(items, start=1))
        _reject_topic_drift(spec, slide, generated)
        existing_note = slide.speaker_note or ""
        new_note = f"{existing_note}\n【随堂练习】\n{generated}" if existing_note.strip() else f"【随堂练习】\n{generated}"
        field = "speaker_note"
        value: Any = new_note
        before: Any = slide.speaker_note
        summary = f"AI 生成随堂练习追加到 page {slide_id} 备注"
    else:
        new_content = _extract_content(reply.text)
        if not new_content:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "重生成 provider 返回空内容")
        _reject_topic_drift(spec, slide, new_content)
        field = "content"
        value = new_content
        before = slide.content
        summary = (
            f"AI 生成推导演示 page {slide_id}"
            if mode == "derivation"
            else f"局部重生成 page {slide_id}"
        )

    operations = [
        {
            "op": "set_slide",
            "target": slide_id,
            "field": field,
            "value": value,
            "before": before,
        }
    ]
    proposal = await spec_edit.create_proposal(
        session,
        deck_id,
        expected_revision=expected_revision,
        summary=summary,
        source="regen",
        operations=operations,
        instruction=instruction,
        refs=checked_refs,
        actor_id=actor_id,
    )
    if proposal.get("changed_slide_ids") not in ([slide_id], []):
        raise ApiError(
            ErrorCode.INTERNAL,
            f"局部重生成影响了 {proposal.get('changed_slide_ids')}，超出目标页 —— 已中止",
        )
    proposal["target"] = {"kind": "slide", "slide_id": slide_id}
    proposal["changed_slide_ids"] = [slide_id]
    proposal["refs_echo"] = checked_refs
    proposal["provider"] = {"model": reply.model, "run_id": f"regen-{uuid.uuid4().hex[:12]}"}
    return proposal
