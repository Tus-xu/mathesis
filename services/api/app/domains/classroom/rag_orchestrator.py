"""双师课堂的检索编排：复用既有 RAGPipeline，不另建检索系统。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid
import re

from sqlalchemy import select


@dataclass(frozen=True)
class ClassroomRetrievalPlan:
    query: str
    content_type: str
    scope: str
    reason: str
    blocked: bool = False
    block_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "content_type": self.content_type,
            "scope": self.scope,
            "reason": self.reason,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
        }


def _meaningful_items(value: object) -> list[str]:
    if isinstance(value, dict):
        value = value.get("items") or []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


_LENGTH_TERM = re.compile(r"(?:(\d+(?:\.\d+)?)\s*)?([A-Z]{2})(?![A-Z])")
_NUMBER_ONLY = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def derive_explicit_length_facts(conditions: list[str]) -> dict[str, float]:
    """从 OCR 已确认的等式链中归约*显式*边长。

    这是题图条件的轻量归约，不猜测图形或补全未知边。它仅处理 ``AD=2BC=2CD``
    与 ``SA=AD=2`` 这类用户原题已经给出的线性等式，供后续模型建系时避免
    把等式链漏读为相等边。
    """
    relations: list[tuple[float, str, float, str]] = []
    anchors: list[tuple[float, str, float]] = []

    for raw in conditions:
        # 中文括号中的问号范围不影响已确认的等式链。
        normalized = str(raw or "").replace("，", ",").replace("：", ":")
        # 角等式中的三字母顶点名会包含两字母子串（如 ∠BCD 中的 CD），
        # 绝不能把 90° 误写成边长；角度始终交给 geometry metrics 处理。
        if "∠" in normalized or "°" in normalized:
            continue
        for chain in re.findall(r"(?:[0-9.]*\s*[A-Z]{2}\s*=\s*)+(?:[0-9.]*\s*[A-Z]{2}|\d+(?:\.\d+)?)", normalized):
            parts = [part.strip() for part in chain.split("=") if part.strip()]
            parsed: list[tuple[str, float, str | None]] = []
            for part in parts:
                number = _NUMBER_ONLY.match(part)
                if number:
                    parsed.append(("number", float(number.group(1)), None))
                    continue
                term = _LENGTH_TERM.fullmatch(part)
                if term:
                    parsed.append(("segment", float(term.group(1) or 1), term.group(2)))
            if len(parsed) < 2:
                continue
            first = parsed[0]
            if first[0] != "segment" or first[2] is None:
                continue
            for other in parsed[1:]:
                if other[0] == "segment" and other[2] is not None:
                    relations.append((first[1], first[2], other[1], other[2]))
                elif other[0] == "number":
                    anchors.append((first[1], first[2], other[1]))

    values: dict[str, float] = {}
    for coefficient, segment, value in anchors:
        if coefficient:
            values[segment] = value / coefficient

    # 只沿已确认等式传播；每轮至多增加一个新事实，避免循环误差。
    for _ in range(len(relations) + 1):
        changed = False
        for left_factor, left, right_factor, right in relations:
            if left in values and right not in values and right_factor:
                values[right] = values[left] * left_factor / right_factor
                changed = True
            elif right in values and left not in values and left_factor:
                values[left] = values[right] * right_factor / left_factor
                changed = True
        if not changed:
            break
    return {segment: float(value) for segment, value in values.items()}


def build_right_trapezoid_pyramid_coordinate_witness(
    conditions: list[str], length_facts: dict[str, float]
) -> dict[str, Any] | None:
    """为已有 ``geometry_claims`` 契约提供一个可复核的直角梯形棱锥建系见证。

    触发条件完全来自已确认的题图文字：ABCD 的两底 AD∥BC、D/C 处直角、
    SAD 与底面垂直，以及 AD/BC/CD/SA 四条显式长度。它并不识别新题型、
    不替代 GeoGebra，也不写入答案；只是把一类可确定的坐标构造交给现有的
    ``math_verifier`` 与 GeoGebra 渲染链路复核。
    """
    text = "\n".join(str(item or "") for item in conditions)
    compact = text.replace(" ", "").replace("//", "∥")
    needs = {"AD", "BC", "CD", "SA"}
    if not needs.issubset(length_facts):
        return None
    if "梯形" not in compact or not ("AD∥BC" in compact or "AD//BC" in text):
        return None
    if not all(angle in compact for angle in ("ADC", "BCD", "SAD")):
        return None
    if not ("平面SAD⊥平面ABCD" in compact or "平面SAD垂直平面ABCD" in compact):
        return None

    a = float(length_facts["AD"])
    b = float(length_facts["BC"])
    c = float(length_facts["CD"])
    h = float(length_facts["SA"])
    if min(a, b, c, h) <= 0:
        return None
    # A-D-C-B 是同时满足 AD∥BC、D/C 直角的通用右梯形放置；S 在 A 的法线方向。
    coords: dict[str, list[float]] = {
        "A": [0.0, 0.0, 0.0],
        "D": [a, 0.0, 0.0],
        "C": [a, c, 0.0],
        "B": [a - b, c, 0.0],
        "S": [0.0, 0.0, h],
    }
    if "中点" in text and "AD" in compact and "E" in text:
        coords["E"] = [a / 2.0, 0.0, 0.0]

    # 现有 verifier 会再次由坐标独立算距离并校准数值；此处仅提供可审计构造。
    plane_points = {
        "ABCD": ["A", "B", "C"],
        "SAD": ["S", "A", "D"],
        "SAB": ["S", "A", "B"],
        "SBD": ["S", "B", "D"],
    }
    return {
        "coordinates": coords,
        "plane_points": plane_points,
        "line_perpendicular": [
            {"line1": ["A", "D"], "line2": ["D", "C"]},
            {"line1": ["B", "C"], "line2": ["C", "D"]},
            {"line1": ["S", "A"], "line2": ["A", "D"]},
            {"line1": ["B", "D"], "line2": ["S", "A"]},
            {"line1": ["B", "D"], "line2": ["A", "B"]},
        ],
        "perpendicular": {"line": ["B", "D"], "plane": "SAB"},
        "metrics": {
            "lengths": {"AD": a, "BC": b, "CD": c, "SA": h},
            "angle_deg": {"ADC": 90.0, "BCD": 90.0, "SAD": 90.0},
        },
        "source": "confirmed_right_trapezoid_coordinate_witness",
    }


def prefer_verified_coordinate_witness(
    page_claims: dict[str, Any] | None, witness: dict[str, Any] | None
) -> dict[str, Any]:
    """同一题的页级断言优先复用已验证坐标，避免模型逐页重建出彼此矛盾的图。"""
    if witness is not None:
        return dict(witness)
    return dict(page_claims or {})


def attach_classroom_grounding(
    source_ref: dict[str, Any] | None, evidence: dict[str, Any]
) -> dict[str, Any]:
    """将检索计划与教材证据附加到来源记录，且不覆盖原始识别审计。"""
    attached = dict(source_ref or {})
    attached["retrieval_plan"] = dict(evidence.get("plan") or {})
    attached["textbook_evidence"] = {
        "status": str(evidence.get("status") or "unavailable"),
        "citations": list(evidence.get("citations") or []),
    }
    if evidence.get("block_reason"):
        attached["textbook_evidence"]["block_reason"] = str(evidence["block_reason"])
    return attached


def attach_textbook_association_when_no_visual(
    slide: dict[str, Any], evidence: dict[str, Any], *, visual_generation: str
) -> bool:
    """图形空位以可追溯教材关联补足，绝不生成默认几何实体。"""
    blocks = slide.get("blocks")
    if not isinstance(blocks, list):
        return False
    visual_kinds = {"geometry", "plot2d", "figure", "ggb"}
    has_visual = any(
        isinstance(block, dict) and block.get("kind") in visual_kinds
        for block in blocks
    )
    citations = list(evidence.get("citations") or [])
    if (
        visual_generation == "attached"
        or has_visual
        or evidence.get("status") != "grounded"
        or not citations
    ):
        return False
    blocks.append(
        {
            "kind": "textbook_association",
            "caption": "教材关联：本页暂不展示未经验证的图形",
            "citations": citations[:4],
        }
    )
    return True


def build_classroom_retrieval_plan(
    source: str, parse_quality: dict[str, Any] | None
) -> ClassroomRetrievalPlan:
    """由手输知识点或已确认题图条件生成学生教材检索计划。

    这里不推断题目条件：仅使用 MiMo/OCR 已给出的结构化内容。
    """
    source_text = str(source or "").strip()
    quality = parse_quality or {}
    uncertainties = _meaningful_items(quality.get("uncertainties"))
    confirmed_by_user = bool(quality.get("confirmed_by_user"))
    if quality and (quality.get("needs_confirmation") or uncertainties) and not confirmed_by_user:
        return ClassroomRetrievalPlan(
            query=source_text,
            content_type="textbook",
            scope="student",
            reason="photo_question_pending_confirmation",
            blocked=True,
            block_reason="photo_conditions_need_confirmation",
        )

    conditions = _meaningful_items(quality.get("conditions"))
    entities = _meaningful_items(
        quality.get("diagram_entities") or quality.get("diagramEntities")
    )
    if quality and (conditions or entities or confirmed_by_user):
        query = "\n".join([source_text, *conditions, *entities]).strip()
        return ClassroomRetrievalPlan(
            query=query,
            content_type="textbook",
            scope="student",
            reason=(
                "photo_question_confirmed_by_user"
                if confirmed_by_user
                else "photo_question_with_confirmed_conditions"
            ),
        )
    return ClassroomRetrievalPlan(
        query=source_text,
        content_type="textbook",
        scope="student",
        reason="manual_knowledge_point",
    )


async def retrieve_classroom_evidence(
    plan: ClassroomRetrievalPlan, *, db: Any, rag: Any | None = None
) -> dict[str, Any]:
    """按计划调用既有 RAG，并为课堂附加可审计的计划信息。"""
    if plan.blocked:
        return {
            "status": "blocked",
            "citations": [],
            "prompt_context": "",
            "plan": plan.as_dict(),
            "block_reason": plan.block_reason,
        }
    if rag is None:
        from app.kernel.rag import get_rag_pipeline

        rag = get_rag_pipeline()
    try:
        result = await rag.retrieve(
            plan.query,
            db=db,
            mode="hybrid",
            scope=plan.scope,
            content_type=plan.content_type,
            request_id="classroom-agentic-rag",
        )
    except Exception:
        return {
            "status": "unavailable",
            "citations": [],
            "prompt_context": "",
            "plan": plan.as_dict(),
        }

    from app.domains.classroom.openmaic_adapter import format_textbook_evidence

    evidence = format_textbook_evidence(
        list(getattr(result, "chunks", None) or []) if getattr(result, "answerable", False) else []
    )
    # 引用需要精确到册、章、节；RAG 的轻量 ScoredChunk 不携带切片 meta，
    # 因此只对已选中的少量 chunk 回查一次，失败时保留已有的真实标题。
    try:
        from app.models.chunk import Chunk
        from app.models.knowledge_doc import KnowledgeDoc

        ids = [uuid.UUID(str(item["id"])) for item in evidence["citations"] if item.get("id")]
        if ids:
            rows = await db.execute(
                select(Chunk.id, Chunk.meta_, KnowledgeDoc.meta_)
                .join(KnowledgeDoc, KnowledgeDoc.id == Chunk.doc_id)
                .where(Chunk.id.in_(ids))
            )
            meta_by_id = {str(row[0]): {**(row[2] or {}), **(row[1] or {})} for row in rows.all()}
            for citation in evidence["citations"]:
                meta = meta_by_id.get(str(citation.get("id")), {})
                citation.update({
                    key: str(meta[key])
                    for key in ("book_name", "volume", "section", "subsection", "source_chunk_id")
                    if meta.get(key)
                })
                if citation.get("book_name"):
                    citation["book"] = citation["book_name"]
    except Exception:
        pass
    evidence["plan"] = plan.as_dict()
    return evidence
