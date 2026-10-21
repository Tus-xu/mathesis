"""V2 Adapter DTO（R11）。

适配层对外只暴露本模块的 V2 DTO，不泄露供应商响应内部结构。
日志/引用只允许使用 text_hash / ref 与必要统计，禁止原文、key、签名 URL。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出中提取 JSON 对象：容忍 ```json 围栏与前后噪声。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return json.loads(cleaned)


@dataclass(frozen=True)
class EmbeddingVector:
    """单条中文/英文文本的 embedding 结果（V2 教材索引写入前置）。"""

    text_hash: str  # sha256(text) 前 16 位（redacted 引用）
    vector: list[float]
    model: str
    dim: int
    normalized: bool = True


@dataclass(frozen=True)
class ChatReply:
    """非流式文本补全结果。"""

    text: str
    model: str | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class VisionAnalysis:
    """Vision 结构化理解（用途/对象/关系；配合 R13 原图 AssetRef）。"""

    purpose: str
    objects: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    model: str | None = None
    raw_summary: str | None = None  # 模型原文摘要（脱敏后可选）


@dataclass(frozen=True)
class OcrDraft:
    """OCR 显式 opt-in 结果；永远是辅助草稿，不替代原图。"""

    text: str
    model: str | None = None
    is_draft: bool = True


@dataclass(frozen=True)
class SearchHit:
    """一条可溯源搜索结果（source provenance）。"""

    title: str
    url: str
    snippet: str
    site: str
    published_time: str | None
    source: str = "bocha"


@dataclass(frozen=True)
class SearchOutcome:
    hits: list[SearchHit]
    query_hash: str
    cached: bool


@dataclass(frozen=True)
class CapabilityProbe:
    """R11 capability matrix 单行：只含状态/延迟/model/dimension/error code，无秘密。"""

    name: str
    ok: bool
    latency_ms: int
    detail: str
    model: str | None = None
    dimension: int | None = None
    error_code: str | None = None
