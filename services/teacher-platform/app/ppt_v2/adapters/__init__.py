"""PPT Studio V2 适配层（R11）。

AIPlatformAdapter / EmbeddingAdapter / VisionAdapter / SearchAdapter：
通过包装既有平台能力（LlmGateway / Embedder / 博查薄客户端）为 R12+ 提供
AI 侧契约；全部返回 V2 DTO，错误分类见 errors.py，日志脱敏见 redact.py。
"""
from app.ppt_v2.adapters.ai import MODEL_TEXT, MODEL_VISION, AIPlatformAdapter
from app.ppt_v2.adapters.embedding import V2_INDEX_DIMS, EmbeddingAdapter
from app.ppt_v2.adapters.search import SearchAdapter
from app.ppt_v2.adapters.vision import VisionAdapter

__all__ = [
    "AIPlatformAdapter",
    "MODEL_TEXT",
    "MODEL_VISION",
    "EmbeddingAdapter",
    "V2_INDEX_DIMS",
    "SearchAdapter",
    "VisionAdapter",
]
