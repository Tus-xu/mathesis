"""OpenMAIC classroom-document adapter for Math Arena.

The adapter deliberately owns no mathematical inference and no drawing fallback.
It turns an already-generated, evidence-bound lesson into OpenMAIC's public
Stage/Scene document shape so an OpenMAIC renderer or persistence service can
consume the same lesson without a second generation pass.
"""

from __future__ import annotations

from html import escape
from typing import Any


def _field(item: Any, name: str, default: str = "") -> str:
    if isinstance(item, dict):
        value = item.get(name, default)
    else:
        value = getattr(item, name, default)
    return str(value or default)


def format_textbook_evidence(chunks: list[Any], *, max_chunks: int = 4, max_chars: int = 900) -> dict:
    """Create bounded, citeable textbook context from the existing RAG result.

    The source text is retained only as prompt context.  API consumers receive
    stable citation metadata, never an invented citation or a model-generated
    source label.
    """
    citations: list[dict[str, Any]] = []
    prompt_parts: list[str] = []
    for index, chunk in enumerate(chunks[:max_chunks], start=1):
        chunk_id = _field(chunk, "chunk_id")
        title = _field(chunk, "doc_title", "高中数学教材")[:120]
        content = _field(chunk, "content")[:max_chars].strip()
        if not content:
            continue
        try:
            score = round(float(chunk.get("score", 0.0) if isinstance(chunk, dict) else getattr(chunk, "score", 0.0)), 4)
        except (TypeError, ValueError):
            score = 0.0
        citations.append({"id": chunk_id, "title": title, "score": score})
        prompt_parts.append(f"[教材证据 {index}｜{title}]\n{content}")
    return {
        "status": "grounded" if citations else "unavailable",
        "citations": citations,
        "prompt_context": "\n\n".join(prompt_parts),
    }


async def retrieve_textbook_evidence(query: str, *, db: Any, rag: Any | None = None) -> dict:
    """Use the platform's existing scoped hybrid RAG before lesson generation.

    A retrieval outage is represented honestly as ``unavailable``.  It is never
    converted into a fake citation and it does not force an unrelated visual.
    """
    if not str(query or "").strip():
        return format_textbook_evidence([])
    if rag is None:
        from app.kernel.rag import get_rag_pipeline

        rag = get_rag_pipeline()
    try:
        result = await rag.retrieve(
            str(query),
            db=db,
            mode="hybrid",
            scope="student",
            content_type="textbook",
            request_id="classroom-textbook-grounding",
        )
    except Exception:
        return format_textbook_evidence([])
    if not getattr(result, "answerable", False):
        return format_textbook_evidence([])
    return format_textbook_evidence(list(getattr(result, "chunks", None) or []))


def _scene_elements(slide: dict, scene_id: str) -> list[dict[str, Any]]:
    """Map only the generated lesson's visible text/formula blocks to a canvas."""
    elements: list[dict[str, Any]] = []
    title = escape(str(slide.get("title") or "本页讲解"), quote=False)
    elements.append(
        {
            "type": "text",
            "id": f"{scene_id}-title",
            "content": f'<p style="font-size: 32px; font-weight: 700;">{title}</p>',
            "left": 60,
            "top": 40,
            "width": 880,
            "height": 70,
            "rotate": 0,
            "defaultFontName": "Microsoft YaHei",
            "defaultColor": "#102a43",
        }
    )
    visible: list[str] = []
    for block in slide.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for key in ("text", "latex", "question", "analysis", "answer"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                visible.append(value.strip())
                break
    if not visible and slide.get("narration"):
        visible.append(str(slide["narration"]))
    for index, value in enumerate(visible[:5], start=1):
        top = 130 + (index - 1) * 105
        elements.append(
            {
                "type": "text",
                "id": f"{scene_id}-body-{index}",
                "content": f'<p style="font-size: 19px;">{escape(value, quote=False)}</p>',
                "left": 80,
                "top": top,
                "width": 840,
                "height": 82,
                "rotate": 0,
                "defaultFontName": "Microsoft YaHei",
                "defaultColor": "#243b53",
            }
        )
    return elements


def build_openmaic_document(
    *,
    session_id: str,
    title: str,
    mode: str,
    slides: list[dict],
    evidence: dict | None = None,
) -> dict:
    """Build the public ``{stage, scenes}`` OpenMAIC persistence payload.

    This mirrors the official ``@openmaic/dsl`` Stage/Scene split.  Geometry and
    interactive artifacts are intentionally not synthesized here: the document
    carries only the verified lesson content already stored in the session.
    """
    stage_id = str(session_id)
    now = 0
    scenes: list[dict[str, Any]] = []
    for index, slide in enumerate(slides or []):
        order = int(slide.get("order") or index + 1)
        scene_id = f"{stage_id}-scene-{order}"
        scenes.append(
            {
                "id": scene_id,
                "stageId": stage_id,
                "type": "slide",
                "title": str(slide.get("title") or f"第 {order} 页"),
                "order": order - 1,
                "content": {
                    "type": "slide",
                    "schemaVersion": 1,
                    "canvas": {
                        "id": f"{scene_id}-canvas",
                        "viewportSize": 1000,
                        "viewportRatio": 0.5625,
                        "theme": {
                            "backgroundColor": "#ffffff",
                            "themeColors": ["#2563eb", "#16a34a", "#f59e0b", "#7c3aed", "#ef4444"],
                            "fontColor": "#102a43",
                            "fontName": "Microsoft YaHei",
                        },
                        "elements": _scene_elements(slide, scene_id),
                        "script": str(slide.get("narration") or ""),
                    },
                },
                "createdAt": now,
                "updatedAt": now,
            }
        )
    return {
        "stage": {
            "id": stage_id,
            "name": str(title or "高中数学课堂"),
            "description": f"Math Arena 双师课堂（{mode or 'topic'}）",
            "createdAt": now,
            "updatedAt": now,
            "languageDirective": "zh-CN",
            "style": "high-school-math",
            "agentIds": ["ai-lecturer", "ai-tutor"],
        },
        "scenes": scenes,
        "evidence": {
            "status": (evidence or {}).get("status", "unavailable"),
            "citations": list((evidence or {}).get("citations") or []),
        },
    }
