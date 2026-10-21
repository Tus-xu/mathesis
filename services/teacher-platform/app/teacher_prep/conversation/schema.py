"""R17 Conversation-first 编排契约：PrepIntent（LLM 载荷）+ 视图模型（timeline/artifact/actions）。

铁律（DECISIONS.md §6/§7/§8）：
- LLM 只产生 intent / proposal 载荷，schema 校验（extra='forbid'）后才成为意图；
- API 返回 conversation timeline + proposal cards + living artifact snapshot +
  evidence refs + available actions + job summary；**不返回强制步骤条**
  （无 steps / wizard / currentStep 字段，契约测试钉死）；
- 动态业务文案 / 选项 / 状态来自授权 snapshot/event；稳定通用 UI 文案用
  `copy_key`（前端统一 i18n），页面组件不散落硬编码业务文案；
- intent 是 deltas：缺省字段 = 保持现状（会话编排应用增量，不整表覆盖）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..text_safety import HTML_TAG_RE as _HTML_TAG_RE  # noqa: E402  -- E-1

# 意图契约版本（语义断裂 bump；reader fail closed）
INTENT_SCHEMA_VERSION = "1.0.0"

_LOCATOR_REF_RE = re.compile(
    r"^textbook_v2/locator/v1/source/[0-9a-fA-F-]{36}"
    r"(?:/section/[0-9a-fA-F-]{36})?"
    r"(?:/chunk/[0-9a-fA-F-]{36}|/chunk-range/\d+-\d+)?"
    r"(?:/(?:unit|problem|asset)/[0-9a-fA-F-]{36})?$"
    r"|^textbook_v2/source/[0-9a-fA-F-]{36}"
    r"(?:/section/[0-9a-fA-F-]{36})?"
    r"(?:/(?:unit|problem|chunk)/[0-9a-fA-F-]{36})?$"
)
# 真标签白名单正则（E-1：数学不等式 0<a<1 时 f(a)>0 不得误判为 HTML）；import 置于文件头（E402）
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


# ── PrepIntent（LLM 载荷；deltas 语义）─────────────────────────────────────────

class IntentObjectiveItem(BaseModel):
    """一条教学目标（文本禁止 HTML；可选 grounding ref）。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=400)
    source_ref: str | None = Field(default=None, max_length=512)

    @field_validator("text")
    @classmethod
    def _no_html(cls, v: str) -> str:
        if _HTML_TAG_RE.search(v):
            raise ValueError("目标文本禁止包含 HTML 标签")
        return v.strip()

    @field_validator("source_ref")
    @classmethod
    def _ref_shape(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _LOCATOR_REF_RE.match(v):
            raise ValueError("source_ref 必须是可解析的教材 SourceLocator ref")
        return v


class PrepIntent(BaseModel):
    """从自然语言解析出的结构化意图（deltas：None = 保持现状）。

    - `intent_type`：new_prep（新备课）/ continue_prep（继续备课）/ modify（修改既有参数）；
    - `textbook_id / section_id`：教材 / 章节（UUID）；
    - `class_id`：班级（UUID，变更需执行瞬间 8000 成员重验）；
    - `knowledge_points`：知识点（list 提供即整体替换；None 保持）；
    - `lesson_scope / lesson_hours`：课时；
    - `objectives`：教学目标（list 提供即整体替换；None 保持）；
    - `confidence`：模型自评（仅展示，不作事实）；
    - 禁任意 HTML / 禁任意组件名（本 schema 无组件字段）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = INTENT_SCHEMA_VERSION
    # R2 对话去表单机：question（问知识点/问考法）与 confirm（确认既有提案）是**对话**，
    # 不是备课参数变更——二者必须走 prose 回应 / 真实确认动作，不得再触发蓝图整包重投影。
    intent_type: Literal["new_prep", "continue_prep", "modify", "casual_chat", "question", "confirm"] = "modify"
    textbook_id: str | None = None
    section_id: str | None = None
    class_id: str | None = None
    knowledge_points: list[str] | None = Field(default=None, max_length=50)
    lesson_scope: str | None = Field(default=None, max_length=200)
    lesson_hours: int | None = Field(default=None, ge=1, le=99)
    objectives: list[IntentObjectiveItem] | None = Field(default=None, max_length=12)
    note: str | None = Field(default=None, max_length=500)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("textbook_id", "section_id", "class_id")
    @classmethod
    def _uuid_shape(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _UUID_RE.match(v):
            raise ValueError("教材/章节/班级 id 必须是 UUID 形态")
        return v

    @field_validator("lesson_scope")
    @classmethod
    def _no_html_scope(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if _HTML_TAG_RE.search(v):
            raise ValueError("课时说明禁止包含 HTML 标签")
        return v.strip()

    @field_validator("knowledge_points")
    @classmethod
    def _points_no_html(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned: list[str] = []
        for p in v:
            p = (p or "").strip()
            if not p:
                continue
            if _HTML_TAG_RE.search(p):
                raise ValueError("知识点禁止包含 HTML 标签")
            cleaned.append(p)
        return cleaned


def intent_snapshot_hash(intent: PrepIntent) -> str:
    """意图规范 hash：model_dump(mode='json') → canonical → sha256（可复现/对账）。"""
    payload = intent.model_dump(mode="json")
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def intent_dto(intent: PrepIntent | None) -> dict[str, Any] | None:
    return intent.model_dump(mode="json") if intent is not None else None


# ── 视图模型（workspace / timeline / actions；无步骤条）────────────────────────

class MessageView(BaseModel):
    """timeline 单条：组件引用（type+props）或纯文本；错误/可编辑标记显式。"""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    seq: int = Field(ge=1)
    role: Literal["user", "assistant", "system"]
    kind: str
    component: dict[str, Any] | None = None  # {"type": allowlisted, "props": {...}}
    text: str | None = None
    intent: dict[str, Any] | None = None
    context_snapshot_id: str | None = None
    blueprint_id: str | None = None
    run_status: str | None = None
    error_label: str | None = None
    error_code: int | None = None
    editable: bool = False
    created_at: str


class ArtifactSnapshot(BaseModel):
    """living artifact：上下文 + 蓝图 + 学前诊断 + 双 Spec（服务端权威投影；无步骤字段）。"""

    model_config = ConfigDict(extra="forbid")

    context: dict[str, Any] | None = None
    blueprint: dict[str, Any] | None = None
    diagnostic: dict[str, Any] | None = None  # R18 学前诊断任务摘要（教师审阅视图）
    lesson_plan: dict[str, Any] | None = None  # L-221-2 LessonPlanSpec 摘要
    teaching_deck: dict[str, Any] | None = None  # R20 TeachingDeckSpec（含完整 spec，供 8200 恢复）
    class_profile_summary: str | None = None  # R3「已考虑」胶囊：画像学情摘要（无画像 → None）


class ActionView(BaseModel):
    """available action：action 枚举 + target + i18n copy_key（无硬编码业务文案）。"""

    model_config = ConfigDict(extra="forbid")

    action: str
    target: str | None = None
    copy_key: str
    disabled: bool = False


class JobSummaryView(BaseModel):
    """job summary：DB 权威作业状态投影（不做假进度）。"""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: str
    status: str
    attempt: int = 0
    max_attempts: int = 5
    updated_at: str | None = None
    last_error: str | None = None


class WorkspaceSnapshot(BaseModel):
    """GET/POST 会话的完整工作区投影（Conversation-first，无 Stepper/Wizard）。"""

    model_config = ConfigDict(extra="forbid")

    session: dict[str, Any]  # session_dto 投影（含 revision / last_event_seq / class_name）
    timeline: list[MessageView] = Field(default_factory=list)
    artifact: ArtifactSnapshot = Field(default_factory=ArtifactSnapshot)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    available_actions: list[ActionView] = Field(default_factory=list)
    job_summary: list[JobSummaryView] = Field(default_factory=list)
    basic_mode: bool = False


__all__ = [
    "INTENT_SCHEMA_VERSION",
    "PrepIntent",
    "IntentObjectiveItem",
    "intent_snapshot_hash",
    "intent_dto",
    "MessageView",
    "ArtifactSnapshot",
    "ActionView",
    "JobSummaryView",
    "WorkspaceSnapshot",
    "canonical_json",
]
