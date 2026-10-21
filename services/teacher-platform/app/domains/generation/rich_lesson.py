"""学生双师课堂 rich lesson → 教师投影课件的纯适配边界。

该模块只处理共享语义和教师端投影映射，不依赖学生端页面组件或数据库会话：

* 输入是学生生成器产出的 ``RichLessonSlide`` JSON；
* 输出仍是教师 V3 的 1280×720 元素和分页；
* 证据、验证结果和原始 blocks 保存在 slide metadata，供编辑器/质量门禁消费；
* 课堂任务在教师画布上可见，教师提示和讲评进入 notes；
* 同一归一化公式不会机械重复占页，明确标记为推导/应用的变形公式保留。
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any


RICH_IR_VERSION = "rich-lesson-v1"

_PAGE_LAYOUTS = {
    "concept_intro": "definition",
    "visual_definition": "definition",
    "derivation": "derivation",
    "formula_focus": "keypoints",
    "worked_example": "example",
    "compare": "definition",
    "common_error": "keypoints",
    "practice": "variation",
    "summary": "summary",
}
_VALID_PAGE_TYPES = set(_PAGE_LAYOUTS)
_KEEP_FORMULA_ROLES = {"derivation", "application", "conclusion", "result"}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _shorten(value: Any, limit: int = 40) -> str:
    body = _text(value)
    if len(body) <= limit:
        return body
    return body[: max(1, limit - 1)] + "…"


def normalize_latex(latex: str | None) -> str:
    """归一化只影响排版噪声，不改变公式语义。"""

    value = _text(latex)
    value = re.sub(r"^\$+|\$+$", "", value)
    value = re.sub(r"\\(?:displaystyle|textstyle|left|right)\b", "", value)
    value = re.sub(r"\\[,;!: ]", "", value)
    value = re.sub(r"\s+", "", value)
    # TeX 中单字符上下标的花括号是排版差异，不应让同一教学公式重复占页。
    value = re.sub(r"([_^])\{([^{}]+)\}", r"\1\2", value)
    # 去掉分式命令与花括号之间的空白后，视觉上相同的分式得到同一 key。
    value = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"\\frac{\1}{\2}", value)
    return value


def _block_kind(block: Mapping[str, Any]) -> str:
    return _text(block.get("kind") or block.get("type")).lower()


def _formula_value(block: Mapping[str, Any]) -> str:
    return _text(block.get("latex") or block.get("formula"))


def _formula_role(block: Mapping[str, Any]) -> str:
    return _text(block.get("role") or block.get("formula_role")).lower()


def _dedupe_formulas(slides: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """删除跨页机械重复公式，保留明确声明为推导/应用结果的变形。"""

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for source in slides:
        slide = copy.deepcopy(dict(source))
        blocks = slide.get("blocks")
        if not isinstance(blocks, list):
            result.append(slide)
            continue
        kept: list[Any] = []
        removed: list[str] = []
        for item in blocks:
            if not isinstance(item, Mapping):
                kept.append(item)
                continue
            value = _formula_value(item) if _block_kind(item) in {"latex", "formula"} else ""
            key = normalize_latex(value) if value else ""
            if key and key in seen and _formula_role(item) not in _KEEP_FORMULA_ROLES:
                removed.append(value)
                continue
            if key:
                seen.add(key)
            kept.append(dict(item))
        slide["blocks"] = kept
        if removed:
            slide["formula_reuse"] = [{"normalized": normalize_latex(value)} for value in removed]
        result.append(slide)
    return result


def dedupe_formula_fields(slides: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """兼容旧教师 SlideSemanticSpec 的顶层 ``formula`` 字段去重。"""

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for source in slides:
        slide = copy.deepcopy(dict(source))
        value = _text(slide.get("formula"))
        key = normalize_latex(value) if value else ""
        role = _text(slide.get("formula_role")).lower()
        if key and key in seen and role not in _KEEP_FORMULA_ROLES:
            slide["formula"] = None
            slide["formula_reuse"] = [{"normalized": key}]
        elif key:
            seen.add(key)
        result.append(slide)
    return result


def _block_content(block: Mapping[str, Any]) -> str:
    kind = _block_kind(block)
    if kind in {"solution", "solution_steps", "steps"}:
        steps = block.get("steps")
        if isinstance(steps, list):
            return "；".join(_text(step.get("content") if isinstance(step, Mapping) else step) for step in steps)
    for key in ("content", "text", "body", "question", "prompt", "title"):
        value = _text(block.get(key))
        if value:
            return value
    return ""


def _points_from_blocks(blocks: Sequence[Any]) -> list[str]:
    points: list[str] = []
    for item in blocks:
        if not isinstance(item, Mapping):
            continue
        kind = _block_kind(item)
        if kind in {"latex", "formula", "geometry", "plot2d", "ggb", "figure3d"}:
            continue
        if kind == "table":
            caption = _text(item.get("caption") or item.get("title"))
            headers = [
                _text(header)
                for header in (item.get("headers") or [])
                if _text(header)
            ]
            candidates = []
            if caption:
                candidates.append(f"表格：{caption}")
            if headers:
                candidates.append("表头：" + " | ".join(headers))
            for row in (item.get("rows") or []):
                if isinstance(row, (list, tuple)):
                    cells = [_text(cell) for cell in row if _text(cell)]
                    if cells:
                        candidates.append(" | ".join(cells))
        elif kind in {"solution", "solution_steps", "steps"} and isinstance(item.get("steps"), list):
            candidates = [
                _text(step.get("content") if isinstance(step, Mapping) else step)
                for step in item["steps"]
            ]
        else:
            candidates = [_block_content(item)]
        for candidate in candidates:
            candidate = re.sub(r"\$\$?(.+?)\$\$?", r"\1", candidate).strip()
            if candidate and candidate not in points:
                points.append(_shorten(candidate))
            if len(points) >= 5:
                return points
    return points


def _solution_steps(blocks: Sequence[Any]) -> list[dict[str, Any]]:
    for item in blocks:
        if not isinstance(item, Mapping):
            continue
        kind = _block_kind(item)
        if kind == "example":
            # 学生端真实契约通常把分步解析/答案内嵌在 example block，而不是另发
            # solution block；投影时拆成教师续页，不能只保留题干。
            analysis = _text(item.get("analysis") or item.get("solution"))
            answer = _text(item.get("answer") or item.get("conclusion"))
            if not analysis and not answer:
                continue
            chunks = [part.strip() for part in re.split(
                r"(?=(?:[①②③④⑤⑥⑦⑧⑨⑩]|\d+[.)、]))", analysis
            ) if part.strip()]
            steps = [{"content": chunk} for chunk in chunks]
            if answer:
                steps.append({"content": f"结论：{answer}", "role": "conclusion"})
            return steps
        if kind not in {"solution", "solution_steps", "steps"}:
            continue
        raw_steps = item.get("steps")
        if not isinstance(raw_steps, list):
            continue
        steps: list[dict[str, Any]] = []
        for step in raw_steps:
            if isinstance(step, Mapping):
                content = _text(step.get("content") or step.get("text"))
                if content:
                    steps.append(copy.deepcopy(dict(step)))
            elif _text(step):
                steps.append({"content": _text(step)})
        return steps
    return []


def _task_from_blocks(blocks: Sequence[Any]) -> dict[str, str] | None:
    for item in blocks:
        if not isinstance(item, Mapping):
            continue
        kind = _block_kind(item)
        if kind not in {"task", "activity", "example", "practice", "question"}:
            continue
        question = _text(item.get("question") or item.get("prompt") or item.get("context") or item.get("content"))
        if not question and kind not in {"task", "activity"}:
            continue
        task = {
            "goal": _text(item.get("task_goal") or item.get("goal") or item.get("objective")),
            "context": _text(item.get("problem_context") or item.get("scenario") or item.get("context") or question),
            "student_action": _text(item.get("student_action") or item.get("action")),
            "expected_output": _text(item.get("expected_output") or item.get("deliverable") or item.get("outcome")),
            "teacher_prompt": _text(item.get("teacher_prompt") or item.get("teacher_guidance") or item.get("hint")),
            "feedback": _text(item.get("feedback") or item.get("analysis")),
        }
        # 例题是课堂任务的最低语义来源；默认文字只描述教学动作，不编造答案。
        task["goal"] = task["goal"] or "完成本题并说明所用依据"
        task["student_action"] = task["student_action"] or "独立列式，写出关键步骤"
        task["expected_output"] = task["expected_output"] or "写出答案和可检查的关键计算式"
        return task
    return None


def _visual_from_blocks(blocks: Sequence[Any]) -> dict[str, Any] | None:
    for item in blocks:
        if not isinstance(item, Mapping):
            continue
        kind = _block_kind(item)
        if kind in {"figure3d", "geometry"}:
            scene = item.get("scene") or item.get("figure")
            if isinstance(scene, Mapping) and any(key in scene for key in ("solids", "curves", "planes", "segments")):
                return {"kind": "figure3d", "scene": copy.deepcopy(dict(scene))}
            preset = _text(item.get("preset") or item.get("preset_id"))
            if preset:
                return {"kind": "preset", "preset": preset, "params": dict(item.get("params") or {})}
            if isinstance(item.get("boardJson") or item.get("board_json"), Mapping):
                return {"kind": "boardJson", "boardJson": copy.deepcopy(dict(item.get("boardJson") or item.get("board_json")))}
        if kind == "plot2d":
            payload = copy.deepcopy(dict(item))
            payload.pop("kind", None)
            return {"kind": "boardJson", "boardJson": {"type": "plot2d", **payload}}
        if kind == "ggb":
            payload = copy.deepcopy(dict(item))
            payload.pop("kind", None)
            return {"kind": "boardJson", "boardJson": {"type": "ggb", **payload}}
    return None


def _normalize_source_evidence(value: Any) -> list[dict[str, Any]]:
    """把学生端 ``{status,citations:[{id,title,score}]}`` 转成教师可追溯列表。"""
    if isinstance(value, list):
        citations = value
    elif isinstance(value, Mapping):
        citations = value.get("citations") or value.get("sources") or value.get("items")
    else:
        return []
    if not isinstance(citations, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in citations:
        if not isinstance(item, Mapping):
            continue
        source_id = _text(item.get("source_id") or item.get("id") or item.get("source_chunk_id") or item.get("title"))
        if not source_id:
            continue
        source_type = _text(item.get("source_type")) or "textbook"
        locator = _text(item.get("locator") or item.get("title") or source_id)
        entry: dict[str, Any] = {
            "source_id": source_id,
            "source_type": source_type,
            "locator": locator,
            "used": bool(item.get("used", True)),
        }
        score = item.get("retrieval_score", item.get("score"))
        if isinstance(score, (int, float)):
            entry["retrieval_score"] = float(score)
        content_hash = _text(item.get("content_hash"))
        if content_hash:
            entry["content_hash"] = content_hash
        normalized.append(entry)
    return normalized


def _slot_for(slots: Mapping[str, Any] | None, page_type: str) -> dict[str, Any]:
    source = slots or {}
    candidate = source.get(page_type) if isinstance(source, Mapping) else None
    if not isinstance(candidate, Mapping):
        candidate = source.get("_default") if isinstance(source, Mapping) else None
    if not isinstance(candidate, Mapping):
        candidate = source
    return dict(candidate) if isinstance(candidate, Mapping) else {
        "max_text": 5,
        "formula_capacity": 1,
        "visual_capacity": 1,
        "required_slots": [],
    }


def _teacher_note(task: Mapping[str, str] | None, source: Mapping[str, Any]) -> str:
    parts: list[str] = []
    if task:
        if task.get("teacher_prompt"):
            parts.append(f"教师引导：{task['teacher_prompt']}")
        if task.get("feedback"):
            parts.append(f"讲评要点：{task['feedback']}")
    evidence = _normalize_source_evidence(source.get("source_evidence"))
    if evidence:
        parts.append(f"证据来源：{_text(evidence[0].get('source_id'))}")
    return "\n".join(parts)


def _task_points(task: Mapping[str, str]) -> list[str]:
    return [
        _shorten(f"任务目标：{task.get('goal', '')}"),
        _shorten(f"问题情境：{task.get('context', '')}"),
        _shorten(f"学生行动：{task.get('student_action', '')}"),
        _shorten(f"可检查产出：{task.get('expected_output', '')}"),
    ]


def _evidence_present(slide: Mapping[str, Any]) -> bool:
    return bool(_normalize_source_evidence(slide.get("source_evidence")))


def _verification_status(slide: Mapping[str, Any]) -> str:
    result = slide.get("verification_result")
    return _text(result.get("status") if isinstance(result, Mapping) else "") or "unknown"


def _scene_has_content(scene: Any) -> bool:
    if not isinstance(scene, Mapping):
        return False
    return any(isinstance(scene.get(key), list) and bool(scene.get(key)) for key in ("solids", "curves", "planes", "segments"))


def _latex_values(value: Any, *, key: str = "") -> list[str]:
    """从数学断言中抽取需要做语法验证的表达式字段。"""
    if isinstance(value, Mapping):
        out: list[str] = []
        for child_key, child in value.items():
            out.extend(_latex_values(child, key=_text(child_key).lower()))
        return out
    if isinstance(value, list):
        out = []
        for child in value:
            out.extend(_latex_values(child, key=key))
        return out
    if isinstance(value, str) and any(token in key for token in ("latex", "expr", "equation", "formula")):
        return [value]
    return []


def verify_rich_slide(slide: Mapping[str, Any]) -> dict[str, Any]:
    """对教师普通生成得到的 rich 页做本地可复核校验。

    模型返回的 ``verification_result.status`` 只作为输入线索，不作为通过依据：
    公式/断言至少经过 teacher-platform 的 LaTeX 解析，图形至少经过结构检查，
    并且页必须有真实 source evidence；任何不可判定项都保持 ``needs_review``。
    """
    blocks = slide.get("blocks") if isinstance(slide.get("blocks"), list) else []
    evidence = _normalize_source_evidence(slide.get("source_evidence"))
    checks: list[dict[str, Any]] = []
    reasons: list[str] = []
    hard_failed = False
    math_bearing = False

    if not evidence:
        reasons.append("缺少可追溯 source_evidence")
    if not blocks:
        reasons.append("缺少结构化 blocks")

    def check_latex(value: Any, label: str) -> None:
        nonlocal hard_failed, math_bearing
        math_bearing = True
        latex = _text(value)
        if not latex:
            reasons.append(f"{label}为空")
            return
        try:
            from app.verification.sympy_verifier import _latex_to_expr

            _latex_to_expr(normalize_latex(latex))
        except Exception as exc:  # noqa: BLE001 - 解析失败必须交教师复核
            hard_failed = True
            reasons.append(f"{label} LaTeX 不可判定：{_shorten(exc, 120)}")
            checks.append({"item": label, "ok": False, "detail": str(exc)[:160]})
            return
        checks.append({"item": label, "ok": True, "detail": "teacher-platform LaTeX parser"})

    for index, block in enumerate(blocks):
        if not isinstance(block, Mapping):
            reasons.append(f"第{index + 1}个 block 不是对象")
            continue
        kind = _block_kind(block)
        if kind in {"latex", "formula"}:
            check_latex(block.get("latex") or block.get("formula"), f"block[{index}].latex")
        elif kind == "plot2d":
            check_latex(block.get("expr"), f"block[{index}].plot2d.expr")
        elif kind in {"geometry", "figure3d"}:
            math_bearing = True
            scene = block.get("figure") or block.get("scene")
            if _scene_has_content(scene):
                checks.append({"item": f"block[{index}].geometry", "ok": True, "detail": "structured scene"})
            elif _text(block.get("preset") or block.get("preset_id")):
                checks.append({"item": f"block[{index}].geometry", "ok": True, "detail": "structured preset"})
            else:
                reasons.append(f"block[{index}] 缺少可渲染图形场景")
        elif kind == "ggb":
            math_bearing = True
            payload = block.get("ggb") if isinstance(block.get("ggb"), Mapping) else block
            commands = payload.get("commands") if isinstance(payload, Mapping) else None
            if isinstance(commands, list) and any(_text(command) for command in commands):
                checks.append({"item": f"block[{index}].ggb", "ok": True, "detail": "structured commands"})
            else:
                reasons.append(f"block[{index}] 缺少可执行作图命令")
        elif _block_content(block):
            checks.append({"item": f"block[{index}].content", "ok": True, "detail": "grounded semantic block"})
        else:
            reasons.append(f"block[{index}] 缺少教学内容")

    for claim_key in ("math_claims", "geometry_claims"):
        claims = slide.get(claim_key)
        if claims:
            math_bearing = True
            claim_values = _latex_values(claims)
            for index, value in enumerate(claim_values):
                check_latex(value, f"{claim_key}[{index}]")
            if not claim_values:
                checks.append({"item": claim_key, "ok": True, "detail": "structured claim payload retained"})

    upstream_status = _verification_status(slide)
    if upstream_status == "failed":
        hard_failed = True
        reasons.append("上游 verification_result 已标记 failed")
    elif upstream_status == "needs_review":
        checks.append({
            "item": "upstream_verification",
            "ok": True,
            "detail": "仅作线索；以教师端确定性校验结果为准",
        })
    if not math_bearing and blocks and evidence:
        checks.append({"item": "semantic_grounding", "ok": True, "detail": "source evidence attached"})
    status = "failed" if hard_failed else ("needs_review" if reasons or not checks else "verified")
    return {
        "status": status,
        "method": "teacher-rich-deterministic-v1",
        "checks": checks,
        "reasons": reasons,
    }


def enrich_rich_slide(slide: Mapping[str, Any], *, source_evidence: Any = None) -> dict[str, Any]:
    """注入服务端证据并替换模型自报的验证结果。"""
    enriched = copy.deepcopy(dict(slide))
    if source_evidence is not None:
        enriched["source_evidence"] = copy.deepcopy(source_evidence)
    enriched["verification_result"] = verify_rich_slide(enriched)
    return enriched


def _rich_slides_from_body(body: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """请求边界只接受带有 blocks 的 rich slide，拒绝字符串/扁平骨架。"""

    raw = body.get("rich_slides") if isinstance(body, Mapping) else None
    if not isinstance(raw, list):
        return []
    return [copy.deepcopy(dict(slide)) for slide in raw if isinstance(slide, Mapping) and isinstance(slide.get("blocks"), list) and slide.get("blocks")]


def quality_is_ready(quality: Mapping[str, Any] | None) -> bool:
    """只有明确的 ready 状态才能进入教师 Deck 持久化路径。"""

    return isinstance(quality, Mapping) and _text(quality.get("status")) == "ready"


def _build_page(
    source: Mapping[str, Any],
    *,
    title: str,
    page_type: str,
    points: list[str],
    formula: str | None,
    visual: dict[str, Any] | None,
    semantic_blocks: list[Any],
    slots: Mapping[str, Any] | None,
    notes: str,
    task: dict[str, str] | None = None,
    order: int = 1,
    anchor_bar: str = "",
) -> dict[str, Any]:
    from app.pagination.layout import LayoutError, layout_slide
    from app.pagination.schemas import SlideSemanticSpec

    safe_type = page_type if page_type in _VALID_PAGE_TYPES else "concept_intro"
    slot = _slot_for(slots, safe_type)
    if visual and int(slot.get("visual_capacity", 1) or 0) == 0:
        # 教师模板不能吞掉学生端真实视觉：只提升该页容量，不改变其他模板页。
        slot["visual_capacity"] = 1
    max_text = max(1, int(slot.get("max_text", 5) or 5))
    visible_points = points[: min(5, max_text)]
    payload: dict[str, Any] = {
        "page_type": safe_type,
        "title": _shorten(title, 60) or "未命名页",
        "points": visible_points,
        "formula": formula,
        "teacher_note": notes or None,
    }
    if visual:
        payload["visual"] = visual
    try:
        spec = SlideSemanticSpec.model_validate(payload)
        elements = [element.model_dump(exclude_none=True) for element in layout_slide(spec, slot)]
    except LayoutError:
        # 结构已经通过 rich contract；布局冲突只允许减少当前页文字，不能伪造视觉。
        spec = SlideSemanticSpec.model_validate({**payload, "points": visible_points[:1]})
        elements = [element.model_dump(exclude_none=True) for element in layout_slide(spec, slot)]
    return {
        "id": f"slide-{order}",
        "layout": _PAGE_LAYOUTS[safe_type],
        "elements": elements,
        "notes": notes,
        "page_type": safe_type,
        "anchor_bar": anchor_bar,
        "ir_version": RICH_IR_VERSION,
        "semantic_blocks": copy.deepcopy(semantic_blocks),
        "source_evidence": _normalize_source_evidence(source.get("source_evidence")),
        "verification_result": copy.deepcopy(source.get("verification_result") or {"status": "unknown"}),
        "task": copy.deepcopy(task) if task else None,
        "formula_key": normalize_latex(formula) if formula else None,
        "teacher_confirmed": False,
    }


def project_rich_lesson(
    slides: Sequence[Mapping[str, Any]], *, slots: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """把学生 rich lesson 投影为教师课件，同时返回整体质量状态。"""

    normalized = _dedupe_formulas(slides)
    projected: list[dict[str, Any]] = []
    reasons: list[str] = []
    has_task = False
    next_order = 1
    for source in normalized:
        blocks = source.get("blocks") if isinstance(source.get("blocks"), list) else []
        if not blocks:
            reasons.append(f"第{source.get('order', next_order)}页缺少结构化 blocks")
        if not _evidence_present(source):
            reasons.append(f"第{source.get('order', next_order)}页缺少 source_evidence")
        if _verification_status(source) != "verified":
            reasons.append(f"第{source.get('order', next_order)}页数学验证状态不是 verified")
        formulas = [
            _formula_value(block)
            for block in blocks
            if isinstance(block, Mapping) and _block_kind(block) in {"latex", "formula"} and _formula_value(block)
        ]
        task = _task_from_blocks(blocks)
        has_task = has_task or task is not None
        solution_steps = _solution_steps(blocks)
        main_blocks = [
            block for block in blocks
            if not (isinstance(block, Mapping) and _block_kind(block) in {"solution", "solution_steps", "steps"})
        ]
        main_points = _points_from_blocks(main_blocks)
        visual = _visual_from_blocks(blocks)
        page_type = _text(source.get("page_type")) or ("worked_example" if task else "concept_intro")
        main = _build_page(
            source,
            title=_text(source.get("title")) or "未命名页",
            page_type=page_type,
            points=main_points,
            formula=formulas[0] if formulas else None,
            visual=visual,
            semantic_blocks=blocks,
            slots=slots,
            notes=_teacher_note(None, source),
            order=next_order,
        )
        projected.append(main)
        next_order += 1
        if solution_steps:
            previous_summary = main_points[-1] if main_points else _text(source.get("title"))
            for chunk_index in range(0, len(solution_steps), 5):
                chunk = solution_steps[chunk_index:chunk_index + 5]
                step_points = [_shorten(_block_content(step), 40) for step in chunk]
                continuation = _build_page(
                    source,
                    title=f"{_text(source.get('title')) or '解答'}（步骤 {chunk_index + 1}-{chunk_index + len(chunk)}）",
                    page_type="derivation",
                    points=step_points,
                    formula=None,
                    visual=None,
                    semantic_blocks=[{"kind": "solution", "steps": copy.deepcopy(chunk)}],
                    slots=slots,
                    notes=_teacher_note(None, source),
                    order=next_order,
                    anchor_bar=f"接上页 · {_shorten(previous_summary, 18)}" if previous_summary else "接上页",
                )
                projected.append(continuation)
                next_order += 1
                previous_summary = step_points[-1] if step_points else previous_summary
        if task:
            task_page = _build_page(
                source,
                title="课堂任务：" + _shorten(task.get("context") or "请完成任务", 24),
                page_type="practice",
                points=_task_points(task),
                formula=None,
                visual=None,
                semantic_blocks=[next((b for b in blocks if isinstance(b, Mapping) and _block_kind(b) in {"task", "activity", "example", "practice", "question"}), {})],
                slots=slots,
                notes=_teacher_note(task, source),
                task=task,
                order=next_order,
            )
            projected.append(task_page)
            next_order += 1

    if projected and not has_task:
        reasons.append("课件缺少可执行课堂任务（目标/学生行动/可检查产出）")

    return {
        "status": "ready" if projected and not reasons else "needs_review",
        "quality_reasons": reasons,
        "ir_version": RICH_IR_VERSION,
        "slides": projected,
    }


__all__ = [
    "RICH_IR_VERSION",
    "dedupe_formula_fields",
    "normalize_latex",
    "enrich_rich_slide",
    "project_rich_lesson",
    "quality_is_ready",
    "verify_rich_slide",
]
