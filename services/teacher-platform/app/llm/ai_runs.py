"""ai_runs 台账（G6：B1 写入 / B2 评估 / B5 对 P1 SLO 取数）。

R11.5-B 接管（DECISIONS §5，生产 fail-closed）：

- 表所有权：`ai_runs` 由正式 Alembic 迁移（d17b2c3e4f5a）所有；
  **移除运行期幂等自建表**（PostgresAiRunsSink 不再执行 DDL——生产禁止隐式 DDL，
  缺表直接抛 RuntimeOwnershipError）。
- 新增审计列（DDL 唯一事实源 = 迁移，勿在本文件重复建表）：lineage jsonb、
  input_hash / output_hash text（输入/输出只记 hash 不落原文 → 脱敏）。
- 台账写入失败：生产 fail-closed（抛错，不静默吞）；local/test 用 Memory/Jsonl sink。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from app.db.ownership import RuntimeOwnershipError

# 迁移 d17b2c3e4f5a 所有表名（无需本文件维护 DDL）
AI_RUNS_TABLE = "ai_runs"


def digest_payload(payload: Any) -> str | None:
    """输入/输出脱敏 hash：规范化 JSON 序列 → sha256；None 输入返回 None。"""
    if payload is None:
        return None
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def redact_error(error: str | None, limit: int = 500) -> str | None:
    """错误脱敏：截断 + 掩码常见密钥形态（sk-/api key/bearer），不落原始凭证。"""
    if not error:
        return None
    text = str(error)
    for pattern in (r"sk-[A-Za-z0-9_\-]{8,}", r"Bearer [A-Za-z0-9._\-]+", r"api[_-]?key[=: ]+[A-Za-z0-9_\-]+"):
        import re

        text = re.sub(pattern, "<redacted>", text, flags=re.IGNORECASE)
    return text[:limit]


@dataclass
class AiRunRecord:
    """一次 LLM 调用的台账行（§4.8 列 + R11.5-B 审计列）。"""

    feature: str
    model: str | None = None
    model_version: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    temperature: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    latency_ms: int | None = None
    user_id: str | None = None
    task_id: str | None = None
    rag_collections: list[str] = field(default_factory=list)
    retrieved_doc_ids: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] | None = None
    evidence_manifest: list[dict[str, Any]] | None = None
    evidence_source_ids: list[str] | None = None
    retrieval_query: str | None = None
    verifier_result: dict[str, Any] | None = None
    error: str | None = None
    # ── R11.5-B 审计列（脱敏：原文不落库，只存 hash + 完整 lineage）──
    lineage: dict[str, Any] | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    # R11.5-C：W3C trace 贯穿（trace_id 列由迁移 e26a6b7c8d9e 增量接管）
    trace_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)


class AiRunsSink(Protocol):
    async def write(self, record: AiRunRecord) -> None: ...


class MemoryAiRunsSink:
    """单测/本地用：行留在进程内列表（仅 local/test）。"""

    def __init__(self) -> None:
        self.records: list[AiRunRecord] = []

    async def write(self, record: AiRunRecord) -> None:
        self.records.append(record)

    def by_feature(self, feature: str) -> list[AiRunRecord]:
        return [r for r in self.records if r.feature == feature]


class JsonlAiRunsSink:
    """开发兜底（无 PG 时；仅 local/test）：每行一条 JSON，追加写。"""

    def __init__(self, path: str) -> None:
        self._path = path

    async def write(self, record: AiRunRecord) -> None:
        import asyncio

        line = json.dumps(asdict(record), ensure_ascii=False, default=str)
        await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class PostgresAiRunsSink:
    """PG 实现（R11.5-B：不执行任何 DDL——表由迁移 d17b2c3e4f5a 所有）。

    fail_closed=True（生产）：缺表直接抛 RuntimeOwnershipError（禁止隐式建表/静默吞）。
    """

    def __init__(self, session_factory, *, fail_closed: bool | None = None) -> None:  # async_sessionmaker[AsyncSession]
        self._session_factory = session_factory
        self._fail_closed = fail_closed

    async def _ensure_table_owned(self, session) -> None:
        """生产 fail-closed 校验；local/test 若表缺失同样报明确错误（不建表）。"""
        from sqlalchemy import text

        row = await session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'ai_runs'"
            )
        )
        if row.scalar() is None:
            raise RuntimeOwnershipError("ai_runs 表缺失（需先执行 Alembic 迁移 d17b2c3e4f5a；生产禁止运行期建表）")

    async def write(self, record: AiRunRecord) -> None:
        from sqlalchemy import text

        async with self._session_factory() as session:
            await self._ensure_table_owned(session)
            await session.execute(
                text(
                    """
                    INSERT INTO ai_runs (id, user_id, task_id, feature, model, model_version,
                      prompt_name, prompt_version, temperature, input_tokens, output_tokens, cost,
                      latency_ms, rag_collections, retrieved_doc_ids, citations, evidence_manifest,
                      evidence_source_ids, retrieval_query, verifier_result, error,
                      lineage, input_hash, output_hash, trace_id)
                    VALUES (:id, :user_id, :task_id, :feature, :model, :model_version,
                      :prompt_name, :prompt_version, :temperature, :input_tokens, :output_tokens, :cost,
                      :latency_ms, :rag_collections, :retrieved_doc_ids, :citations, :evidence_manifest,
                      :evidence_source_ids, :retrieval_query, :verifier_result, :error,
                      :lineage, :input_hash, :output_hash, :trace_id)
                    """
                ),
                {
                    "id": uuid.UUID(record.id),
                    "user_id": uuid.UUID(record.user_id) if record.user_id else None,
                    "task_id": uuid.UUID(record.task_id) if record.task_id else None,
                    "feature": record.feature,
                    "model": record.model,
                    "model_version": record.model_version,
                    "prompt_name": record.prompt_name,
                    "prompt_version": record.prompt_version,
                    "temperature": record.temperature,
                    "input_tokens": record.input_tokens,
                    "output_tokens": record.output_tokens,
                    "cost": record.cost,
                    "latency_ms": record.latency_ms,
                    "rag_collections": record.rag_collections,
                    "retrieved_doc_ids": record.retrieved_doc_ids,
                    "citations": json.dumps(record.citations, ensure_ascii=False) if record.citations else None,
                    "evidence_manifest": json.dumps(record.evidence_manifest, ensure_ascii=False)
                    if record.evidence_manifest
                    else None,
                    "evidence_source_ids": record.evidence_source_ids,
                    "retrieval_query": record.retrieval_query,
                    "verifier_result": json.dumps(record.verifier_result, ensure_ascii=False)
                    if record.verifier_result
                    else None,
                    "error": redact_error(record.error),
                    "lineage": json.dumps(record.lineage, ensure_ascii=False) if record.lineage else None,
                    "input_hash": record.input_hash,
                    "output_hash": record.output_hash,
                    "trace_id": record.trace_id,
                },
            )
            await session.commit()
