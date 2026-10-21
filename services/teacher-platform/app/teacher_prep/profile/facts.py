"""R19 学情证据 PII 最小投影（8000 submission-facts → 8100 内存统计素材）。

铁律（R19 DECISION-IMPLEMENTATION-DELTA D2/D8/D16）：
- 8000 是 Submission 事实权威；8100 **不复制**整行提交（answer_text/附件/建议理由/
  昵称/file_id 在 8000 出口即不出域）；
- 本模块把 8000 `GET /api/teacher/assignments/{id}/submission-facts` 载荷归一为
  `SubmissionFactsBundle`（**仅内存使用，禁止持久化**；持久化只允许 profile 聚合
  数字与 typed refs，见 evidence.py）；
- `student_key` 是 8000 出口已伪名化的 sha256 前 32 hex（8100 全程不接触明文
  user_id）；仅用于内存中的去重/计数/配对；
- 缺失即显式 unknown：due/confidence/attempt 缺席 → None，不伪造默认值。

本文件属于 `app/teacher_prep`（实体事实层），不是 `teacher_prep_v2` 契约层——
不新增跨端契约名，`SubmissionFactsBundle` 是 8100 内部统计输入模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter
from app.teacher_prep_v2.adapters.errors import from_http_status
from app.teacher_prep_v2.adapters.mapping import compute_late_seconds
from app.teacher_prep_v2.contracts.errors import (
    ContractAuthorityUnreachable,
    ContractMembershipDenied,
)


class ItemFacts(BaseModel):
    """单题作答统计素材（无答案正文、无附件内容）。"""

    model_config = ConfigDict(extra="ignore")

    item_no: int = Field(ge=1)
    q_type: str | None = None
    verdict: str = "pending_review"  # correct | wrong | pending_review
    score: float | None = Field(default=None, ge=0)
    teacher_final_score: float | None = Field(default=None, ge=0)
    suggestion_confidence: float | None = Field(default=None, ge=0, le=1)
    has_answer: bool = False  # 仅布尔（是否作答），内容本身不出域
    needs_review: bool = False
    error_type: str | None = None

    @property
    def is_correct(self) -> bool:
        return self.verdict == "correct"

    @property
    def is_wrong(self) -> bool:
        return self.verdict == "wrong"

    @property
    def is_pending(self) -> bool:
        return self.verdict not in ("correct", "wrong") or self.needs_review


class SubmissionFacts(BaseModel):
    """单份提交统计素材（仅内存；student_key 为 8000 出口伪名化哈希）。"""

    model_config = ConfigDict(extra="ignore")

    submission_id: str = Field(min_length=1)  # 8000 submissions.id（typed ref 锚）
    student_key: str = Field(min_length=8, max_length=64)  # 出口伪名化，非明文 id
    client_submit_id: str | None = None
    status: str = "submitted"  # graded | pending_review | ...
    submitted_at: datetime | None = None
    late_seconds: int | None = Field(default=None, ge=0)  # 由 due 投影计算；缺失=unknown
    attempt_number: int | None = Field(default=None, ge=1)  # 8000 无该列 → 显式 unknown
    items: list[ItemFacts] = Field(default_factory=list)


class SubmissionFactsBundle(BaseModel):
    """一次拉取的作业级统计素材（assignment + eligible 伪名键 + 提交素材）。"""

    model_config = ConfigDict(extra="ignore")

    assignment_id: str | None = None
    class_id: str | None = None
    assignment_title: str | None = None
    deadline: datetime | None = None
    assignment_status: str | None = None
    eligible_student_keys: list[str] = Field(default_factory=list)
    submissions: list[SubmissionFacts] = Field(default_factory=list)
    # tel E2E 修复：8000 随 facts 导出的 item→KP 映射（quiz_items.kp_code），缺失=空表
    item_kp_map: dict[str, list[int]] = Field(default_factory=dict)
    # 拉取时间（后续 aggregate 的默认 now 来源；不影响统计数字本身）
    fetched_at: datetime | None = None


async def fetch_facts_bundle(
    adapter: Api8000Adapter,
    *,
    token: str,
    assignment_id: str,
) -> SubmissionFactsBundle:
    """经 Adapter 拉取并归一（403/404→成员拒绝；5xx→权威不可达 fail closed）。"""
    status, body = await adapter.fetch_submission_facts(token=token, assignment_id=assignment_id)
    if status in (403, 404):
        raise ContractMembershipDenied(
            f"学情素材拉取被 8000 成员关系拒绝（assignment_id={assignment_id}, status={status}）"
        )
    if status >= 500 or status == 0:
        raise ContractAuthorityUnreachable(f"学情素材拉取时 8000 不可达（status={status}）")
    if status != 200 or not isinstance(body, dict):
        raise from_http_status(status, f"submission-facts 意外响应（{status}）")
    return bundle_from_payload(body)


def bundle_from_payload(body: dict[str, Any]) -> SubmissionFactsBundle:
    """8000 载荷 → SubmissionFactsBundle（宽容读取；缺失字段显式 unknown）。"""
    assignment = body.get("assignment") or {}
    deadline_raw = assignment.get("deadline")
    deadline = _parse_dt(deadline_raw)
    submissions: list[SubmissionFacts] = []
    for raw in body.get("submissions") or []:
        if not isinstance(raw, dict):
            continue
        submitted_at = _parse_dt(raw.get("created_at"))
        late_seconds = compute_late_seconds(submitted_at, deadline) if submitted_at else None
        items = [
            ItemFacts(**it)
            for it in (raw.get("items") or [])
            if isinstance(it, dict)
        ]  # 载荷畸形（缺 item_no / 置信度越界）→ ValidationError fail closed，不静默丢题
        submissions.append(
            SubmissionFacts(
                submission_id=str(raw.get("submission_id") or ""),
                student_key=str(raw.get("student_key") or ""),
                client_submit_id=raw.get("client_submit_id"),
                status=str(raw.get("status") or "submitted"),
                submitted_at=submitted_at,
                late_seconds=late_seconds,
                attempt_number=None,  # 8000 无 attempt 列 → 显式 unknown
                items=items,
            )
        )
    return SubmissionFactsBundle(
        assignment_id=assignment.get("assignment_id"),
        class_id=assignment.get("class_id"),
        assignment_title=assignment.get("title"),
        deadline=deadline,
        assignment_status=assignment.get("status"),
        eligible_student_keys=[str(k) for k in body.get("eligible_student_keys") or []],
        submissions=submissions,
        item_kp_map={
            str(k): [int(no) for no in v]
            for k, v in (body.get("item_kp_map") or {}).items()
            if isinstance(v, list)
        },
        fetched_at=None,
    )


def _parse_dt(raw: Any) -> datetime | None:
    if raw is None or isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "ItemFacts",
    "SubmissionFacts",
    "SubmissionFactsBundle",
    "bundle_from_payload",
    "fetch_facts_bundle",
]
