"""VisionAdapter —— V2 图像理解 + OCR 显式 opt-in（R11）。

包装 AIPlatformAdapter.vision（spark profile → xophunyuanocr）。
- understand()：返回结构化 {purpose, objects, relations}；原图原样传递，绝不替换/重建；
- ocr_opt_in()：老师显式点“提取文字”才调用；结果恒为辅助草稿（is_draft=True），
  配合 R13 的 AssetRef（preserve_original=true）语义。
"""
from __future__ import annotations

from app.ppt_v2.adapters.ai import MODEL_VISION, AIPlatformAdapter
from app.ppt_v2.adapters.dto import OcrDraft, VisionAnalysis, extract_json_object
from app.ppt_v2.adapters.errors import AdapterFatalError

UNDERSTAND_PROMPT = (
    "请分析这张教学图片，只输出一个合法 JSON 对象（不要 Markdown 代码块）：\n"
    '{"purpose": "该图在教学中的用途（一句话）", '
    '"objects": ["图中主要对象"], '
    '"relations": ["对象间几何/数量关系"]}'
)

OCR_PROMPT = "请提取这张图片中的所有文字，只输出纯文本（保持原顺序），不要任何额外解释。"


class VisionAdapter:
    """V2 视觉适配：结构化理解 + OCR 显式 opt-in。"""

    def __init__(self, ai: AIPlatformAdapter | None = None) -> None:
        self._ai = ai or AIPlatformAdapter()

    @property
    def model(self) -> str:
        return MODEL_VISION

    async def understand(self, image: str) -> VisionAnalysis:
        """结构化图像理解（用途/对象/关系）。输出非 JSON → AdapterFatalError。"""
        text = await self._ai.vision(image, UNDERSTAND_PROMPT)
        try:
            payload = extract_json_object(text)
            return VisionAnalysis(
                purpose=str(payload.get("purpose", "")).strip(),
                objects=[str(o) for o in payload.get("objects", [])],
                relations=[str(r) for r in payload.get("relations", [])],
                model=MODEL_VISION,
            )
        except (ValueError, TypeError, AttributeError) as exc:
            raise AdapterFatalError(f"vision 结构化结果解析失败: {type(exc).__name__}") from exc

    async def ocr_opt_in(self, image: str) -> OcrDraft:
        """老师显式要求才调用的 OCR 提取；结果恒为辅助草稿（不替代原图）。"""
        text = await self._ai.vision(image, OCR_PROMPT)
        return OcrDraft(text=text.strip(), model=MODEL_VISION, is_draft=True)
