"""R17 allowlisted typed component catalog（服务器只能下发已注册组件类型 + schema 校验后的 props）。

铁律（DECISIONS.md §7/§8）：
- 服务器 / 模型不得下发任意 HTML/CSS、任意组件名和可执行表达式；
- 前端只渲染 catalog 注册的组件类型；props 经 pydantic schema 校验（extra='forbid'）：
  - 未知组件名 → 拒绝；
  - props 含 schema 外字段 → 拒绝；
  - 任一字符串值命中 HTML 标记 / 可执行表达式模式 → 拒绝（不落库、不下发）；
- 稳定通用 UI 文案（“取消”“重试”等）通过 `copy_keys`（i18n key）下发，不由后端
  逐字编造业务文案；动态业务内容（教材名/章节/知识点/选项/状态）来自授权
  snapshot/event 数据。

本模块是服务端强制边界：消息落库前必须过 `validate_component`。
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ApiError, ErrorCode

from ..text_safety import HTML_TAG_RE as _HTML_TAG_RE  # noqa: E402  -- E-1
from ..text_safety import strip_media_syntax  # noqa: E402  -- T2.5 引文图片串清洗

# ── 禁止模式（服务端硬闸门，不依赖前端）───────────────────────────────────────
# HTML 标记：真标签白名单（含自闭合/注释）——数学不等式文本（0<a<1 时 f(a)>0）
# 不得误判（见 text_safety 模块说明，E-1 修复）；import 置于文件头（E402）
#
# 可执行表达式 / 危险属性 / 协议 / DOM sink —— 一律在**原文**上检查，
# 数学片段无法藏匿这些真危险模式。
# ⚠ 2026-09-23（备课中心重构）：`document.`/`window.` 裸模式与真实教材正文存在碰撞
# （实测 4 条 conversation.run 因 `props.refs[6].snippet: 包含可执行表达式` 变 FAILED/DLQ，
# 而该 snippet 来自教材切片派生文本、且不落在 snapshot 原文上 —— 即由证据卡构造过程产生的
# 伪阳性，直接阻断整条备课链路）。收窄为**带具体成员/调用**的形态：仍覆盖真实注入
# （写 DOM / 读 cookie / 跳转 / 取 window 属性 / eval 调用），但不再把散文里的
# "document." 之类普通短语当攻击。其余模式（`<%=`、`javascript:`、`data:text/html`、
# 内联事件 `on*= `、`innerHTML=`、`dangerouslySetInnerHTML`）保持原样。
# 命中时会把 path/命中串/上下文写入 tmp 转储（`_dump_expr_hit`），便于后续定位与收窄。
_EXPR_RE = re.compile(
    r"<%\s*=|javascript:|data:text/html|on[a-z]+\s*=|"
    r"dangerouslySetInnerHTML|innerHTML\s*=|"
    r"document\.(?:write|writeln|cookie|createElement|getElementById|querySelector|location)|"
    r"window\.(?:location|open|document|name|eval|parent|top)|eval\s*\(",
    re.IGNORECASE,
)


def _dump_expr_hit(path: str, matched: str, value: str) -> None:
    """命中可执行表达式模式时落盘（**仅诊断**，需显式开启开关，默认惰性）。

    生产/CI 不设该环境变量 → 不产生任何文件与 IO。用于后续定位模式误判
    （本轮即靠它确认 `document.` 裸模式与教材散文碰撞）。
    """
    if not os.environ.get("PREP_DEBUG_SCAN_HITS"):
        return
    try:
        import pathlib

        idx = value.find(matched)
        ctx = value[max(0, idx - 80) : idx + len(matched) + 80].replace("\n", " ")
        p = pathlib.Path(os.environ["PREP_DEBUG_SCAN_HITS"])
        with p.open("a", encoding="utf-8") as f:
            f.write(f"[{path}] token={matched!r} ctx=...{ctx}...\n")
    except Exception:  # noqa: BLE001 —— 诊断路径绝不影响闸门
        pass

# 模板插值（Vue/Handlebars `{{ expr }}`）。
# 缺陷（2026-09-14 真实链路 P0，与 text_safety E-1 同类但漏修此分支）：
# 教材 direct-chunk 正文天然含 LaTeX，嵌套花括号（`\overline{{{x}}}`、`\sum_{i=1}^{n}`）
# 会产出与模板插值**同形**的 `{{`，使含数学公式的 evidence_rail / blueprint 卡片在落库
# 闸门被 42200 拒绝、整条备课命令失败（实测：`refs[6].snippet` 误判 10 次；全库上下文
# 命中 `{{{x}}` 1066 次、`{{i = 1}}` 320 次、`{{x}}` 90 次）。
# 处理：LaTeX 数学片段（`$…$` / `$$…$$` / `\(…\)` / `\[…\]`）内的 `{{` 不视为模板插值。
# 依据：本 catalog 的字符串字段全部由前端以**文本插值**渲染（teacher-prep-v2 全域无
# `v-html`、无 `innerHTML`），且 Vue 不会对数据字符串做二次模板编译 —— 数学片段里的
# `{{` 是惰性文本。剥离只影响模板插值这一条，URL scheme / DOM sink / 事件处理器仍在
# 原文上检查。
_TEMPLATE_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_LATEX_SPAN_RE = re.compile(
    r"\$\$.*?\$\$|\$[^$\n]*\$|\\\(.*?\\\)|\\\[.*?\\\]",
    re.DOTALL,
)


class ComponentSpecInvalid(ApiError):
    def __init__(self, reason: str):
        super().__init__(ErrorCode.VALIDATION, f"组件契约校验失败：{reason}")


def _scan_strings(value: Any, path: str, issues: list[str]) -> None:
    """递归扫描 props 全部字符串值：HTML / 可执行表达式 → 拒绝。

    模板插值检查在**剥离 LaTeX 数学片段后**的文本上进行（见 `_TEMPLATE_RE` 注释）；
    HTML 与其余可执行表达式模式仍在原文上检查。
    """
    if isinstance(value, str):
        if _HTML_TAG_RE.search(value):
            issues.append(f"{path}: 包含 HTML 标记（模型/服务器不得下发任意 HTML/CSS）")
        expr = _EXPR_RE.search(value)
        if expr or _TEMPLATE_RE.search(_LATEX_SPAN_RE.sub(" ", value)):
            if expr:
                _dump_expr_hit(path, expr.group(0), value)
            issues.append(f"{path}: 包含可执行表达式（仅允许纯数据）")
    elif isinstance(value, dict):
        for k, v in value.items():
            _scan_strings(v, f"{path}.{k}", issues)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _scan_strings(v, f"{path}[{i}]", issues)


def _collapse_doubled_braces(s: str) -> str:
    """把 `{{`/`}}` 迭代折叠为单括号直至收敛（`{{{x}}}` → `{x}`）。"""
    while "{{" in s or "}}" in s:
        s = s.replace("{{", "{").replace("}}", "}")
    return s


def _normalize_brace_artifacts(value: Any) -> Any:
    """LLM 偶发输出**转义型双大括号** LaTeX 残留（如 refs 里未包 `$…$` 的 `{{{x}}}`）。

    与模板插值同形 → 2026-10-10 实测 9.3 统计案例蓝图 5/5 次尝试同因
    `refs[6].title/snippet` 被拒、conversation.run 整链 FAILED。
    处理：`$…$`/`$$…$$`/`\\(…\\)`/`\\[…\]` 数学片段**原样保留**（教材语料大量
    存在合法双括号，见 _LATEX_SPAN_RE 注释）；片段外的 `{{`/`}}` 折叠为单括号。
    依据与 `_TEMPLATE_RE` 注释一致：本 catalog 字段全部由前端纯文本插值渲染
    （无 v-html、Vue 不二次编译数据字符串），双花括号是惰性文本。
    HTML / 可执行表达式闸门不受影响：模式与花括号数量无关，且归一化后仍在
    同一扫描中把关（`{{document.cookie}}` 折叠为 `{document.cookie}` 后照样被拒）。
    """
    if isinstance(value, str):
        if "{{" not in value and "}}" not in value:
            return value
        out: list[str] = []
        pos = 0
        for m in _LATEX_SPAN_RE.finditer(value):
            out.append(_collapse_doubled_braces(value[pos : m.start()]))
            out.append(m.group(0))
            pos = m.end()
        out.append(_collapse_doubled_braces(value[pos:]))
        return "".join(out)
    if isinstance(value, dict):
        return {k: _normalize_brace_artifacts(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_brace_artifacts(v) for v in value]
    return value


# ── 组件 props schema（全部 extra='forbid'；字符串按长度与模式约束）──────────────

CopyKeys = dict[str, str]


class UserTextProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


class AssistantTextProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    copy_keys: CopyKeys = Field(default_factory=dict, max_length=8)


class ContextCardProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str | None = None
    source_title: str | None = Field(default=None, max_length=120)
    section_id: str | None = None
    section_title: str | None = Field(default=None, max_length=200)
    section_path: str | None = Field(default=None, max_length=40)
    class_id: str | None = None
    lesson_scope: str | None = Field(default=None, max_length=200)
    lesson_hours: int | None = Field(default=None, ge=1, le=99)
    knowledge_points: list[str] = Field(default_factory=list, max_length=50)
    objectives: list[str] = Field(default_factory=list, max_length=12)
    snapshot_hash: str | None = None
    warnings: list[str] = Field(default_factory=list, max_length=30)


class BlueprintBucketProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: Literal[
        "objectives", "prior_knowledge", "concepts", "theorems", "examples",
        "inquiries", "design_intent", "key_points", "difficult_points", "lesson_flow",
    ]
    title: str = Field(min_length=1, max_length=80)
    items: list[dict[str, str | None]] = Field(default_factory=list, max_length=20)  # {text, source_ref?}


class BlueprintInfoItem(BaseModel):
    """确认卡「备课信息」单元（golden1 G-golden1：课题/教材/年级班级/课时 2×2 表格）。"""

    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=20)
    value: str = Field(min_length=1, max_length=200)


class BlueprintCardProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blueprint_id: str
    revision: int = Field(ge=1)
    status: Literal["proposal", "confirmed", "rejected"]
    title: str = Field(min_length=1, max_length=120)
    buckets: list[BlueprintBucketProps] = Field(default_factory=list, max_length=20)
    context_snapshot_hash: str | None = None
    copy_keys: CopyKeys = Field(default_factory=dict, max_length=8)
    # R2 确认卡形态（G10，golden1：备课信息 2×2 + 教学意图 3 bullets + 下一步建议 + 三按钮）。
    # 全部可选：存量已落库 blueprint_card 缺这些字段仍可渲染（向后兼容）。
    info: list[BlueprintInfoItem] = Field(default_factory=list, max_length=8)
    intent_bullets: list[str] = Field(default_factory=list, max_length=6)
    next_step: str | None = Field(default=None, max_length=300)


class EvidenceRefProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(min_length=1, max_length=512)  # SourceLocator（locator.py 可解析）
    kind: str = Field(max_length=32)
    title: str = Field(max_length=200)
    snippet: str = Field(default="", max_length=400)


class EvidenceRailProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 上限从 50 提到 120：单章教材（direct-chunk）在「8 个 bucket + facts + 蓝图 objectives/
    # lesson_flow」全量去重后稳定超过 50（实测 2.3 节 51 条 → 落库闸门 42200 拒绝，
    # 备课 run 恒失败）。此处只放宽**容量**，不放松任何模式闸门。
    refs: list[EvidenceRefProps] = Field(default_factory=list, max_length=120)
    copy_keys: CopyKeys = Field(default_factory=dict, max_length=8)


class JobStatusCardProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    job_type: str = Field(max_length=64)
    status: str = Field(max_length=32)
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    updated_at: str | None = None
    copy_keys: CopyKeys = Field(default_factory=dict, max_length=8)


class SelectionOptionProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str = Field(min_length=1, max_length=200)
    # LP-01 契约对齐（2026-09-23）：options.py 的章节选项自带 `group`（「第 N 章」）
    # 与 `recommended`（知识点命中 → 排前并带推荐标记）。此前 catalog 未同步这两个
    # 字段 → `_selection_cards` 构造的 props 恒被 extra=forbid 拒绝（42200），
    # 首条消息即 failed（实测：椭圆请求 → SelectionCardProps 26 处 extra_forbidden）。
    # 仅补契约（可选、有界），不放松 extra=forbid 铁律。
    group: str | None = Field(default=None, max_length=32)
    recommended: bool = False


class SelectionCardProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["class", "section", "textbook", "knowledge_points"]
    # 2026-10-04 对话闭环修复：当前已选教材（TextbookSource.id，可空）——与前端
    # 修复方约定的契约字段；教材卡缺教材时为 null，前端据此展示已选态并在改选时
    # 显式回传 textbook_id（不再误传成 section_id 造成脏数据）。
    source_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    options: list[SelectionOptionProps] = Field(default_factory=list, max_length=200)
    copy_keys: CopyKeys = Field(default_factory=dict, max_length=8)


class BasicModeCardProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_label: str = Field(max_length=64)  # 脱敏机器标签：provider_unavailable/timeout/...
    error_code: int = Field(default=0, ge=0)
    retryable: bool = True
    editable: dict[str, Any] = Field(default_factory=dict)  # 当前可编辑上下文字段（纯数据）
    copy_keys: CopyKeys = Field(default_factory=dict, max_length=8)
    # 失败可恢复：产品化文案 + 有界失败详情（非堆栈）。随 durable 消息落库，
    # 教师刷新/重进后仍能读到「为什么失败 + 可重试」，而不是空白或假成功。
    error_message: str | None = Field(default=None, max_length=200)
    error_detail: str | None = Field(default=None, max_length=300)


class DiagnosticItemViewProps(BaseModel):
    """诊断单题教师审阅视图：目标/类型/题干/答案/解析/难度/kp/证据（全量可编辑）。

    答案/解析/难度/kp 是教师侧参考——映射为 R13 PreClassDiagnosticTask 时**不下发**
    （防学生端答案泄露，见 diagnostic/mapping.py）。
    """

    model_config = ConfigDict(extra="forbid")
    item_no: int = Field(ge=1)
    objective: Literal["prior_knowledge", "misconception", "confidence"]
    item_type: Literal["single_choice", "fill_blank", "short_answer", "confidence_rating"]
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=500)
    explanation: str = Field(min_length=1, max_length=800)
    difficulty: Literal["easy", "medium", "hard"]
    kp: str = Field(min_length=1, max_length=64)
    source_ref: str | None = Field(default=None, max_length=512)
    max_score: float | None = Field(default=None, ge=0.5, le=10)


class DiagnosticCardProps(BaseModel):
    """学前诊断任务卡（R18）：教师审阅 → 编辑/删题/调时长 → 发布/跳过。

    - `status`：proposal（LLM 提案）/ draft（教师编辑）/ published（已下发）/ skipped；
    - `pending_publish`：发布高风险写进行中（PendingAction 已创建、8000 未落定）；
    - `published_assignment_id`：8000 assignments.id（发布成功回填，失败为 None）；
    - `source_refs`：题目证据 SourceLocator 集合（grounding 校验通过才出现在卡上）。
    """

    model_config = ConfigDict(extra="forbid")
    diagnostic_id: str
    blueprint_id: str
    revision: int = Field(ge=1)
    status: Literal["proposal", "draft", "published", "skipped"]
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    estimated_duration_minutes: int = Field(ge=5, le=10)
    knowledge_points: list[str] = Field(default_factory=list, max_length=10)
    items: list[DiagnosticItemViewProps] = Field(default_factory=list, max_length=8)
    pending_publish: bool = False
    published_assignment_id: str | None = None
    copy_keys: CopyKeys = Field(default_factory=dict, max_length=12)


class ErrorCardProps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_label: str = Field(max_length=64)
    error_code: int = Field(default=0, ge=0)
    retryable: bool = False
    copy_keys: CopyKeys = Field(default_factory=dict, max_length=8)


class NodeRevisionCardProps(BaseModel):
    """R3 AI 定向修改完成卡：只针对单个教学环节的修订结果。

    - `node_index` / `node_title`：被修改的环节（0-based 序号 + 展示名）；
    - `revision` / `blueprint_id`：落库后的新蓝图版本锚点；
    - `changed_fields`：本节点实际变化的字段（title/duration_minutes/组字段）；
    - `summary`：AI 对本次修改的一句话摘要（可空）。
    """

    model_config = ConfigDict(extra="forbid")
    node_index: int = Field(ge=0, le=99)
    node_title: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=1)
    blueprint_id: str
    summary: str = Field(default="", max_length=300)
    source: Literal["ai_suggestion", "teacher", "system"] = "ai_suggestion"
    changed_fields: list[str] = Field(default_factory=list, max_length=8)
    copy_keys: CopyKeys = Field(default_factory=dict, max_length=8)


# ── 注册表（唯一 allowlist：新增组件必须先在此注册 + schema 校验 + 契约测试）─────

class DecisionOptionProps(BaseModel):
    """决策卡单个按钮（R2 改选确认：prose + 明确确认动作，非章节胶囊云）。

    - `action=select_section`：点击回传结构化章节 id（同选择卡 id 直传路径，不经 LLM）；
    - `action=send_text`：点击以 `text` 作为教师消息发送（真实对话消息，持久化）。
    """

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    action: Literal["select_section", "send_text"]
    section_id: str | None = None
    textbook_id: str | None = None
    text: str | None = Field(default=None, max_length=400)


class DecisionCardProps(BaseModel):
    """决策卡：需要老师明确拍板的少数选项（≤4），不用胶囊云代替确认动作。"""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    options: list[DecisionOptionProps] = Field(default_factory=list, max_length=4)
    copy_keys: CopyKeys = Field(default_factory=dict, max_length=8)


COMPONENT_CATALOG: dict[str, type[BaseModel]] = {
    "user_text": UserTextProps,
    "assistant_text": AssistantTextProps,
    "context_card": ContextCardProps,
    "blueprint_card": BlueprintCardProps,
    "evidence_rail": EvidenceRailProps,
    "job_status_card": JobStatusCardProps,
    "selection_card": SelectionCardProps,
    "basic_mode_card": BasicModeCardProps,
    "diagnostic_card": DiagnosticCardProps,
    "error_card": ErrorCardProps,
    "node_revision_card": NodeRevisionCardProps,
    "decision_card": DecisionCardProps,
}

ALLOWED_COMPONENT_TYPES = frozenset(COMPONENT_CATALOG)


def validate_component(component_type: str | None, props: dict[str, Any] | None) -> dict[str, Any] | None:
    """组件契约硬闸门：类型注册 + schema 校验（extra=forbid）+ 字符串扫描。

    返回规范化 props（None 输入 → None）。任何违规抛 ComponentSpecInvalid（42200）。
    """
    if component_type is None:
        if props is None:
            return None
        raise ComponentSpecInvalid("component_type 缺失但 props 存在")
    if component_type not in COMPONENT_CATALOG:
        raise ComponentSpecInvalid(
            f"组件类型不在 allowlist（{component_type}）——服务器只能下发已注册组件类型"
        )
    if props is None:
        props = {}
    schema_cls = COMPONENT_CATALOG[component_type]
    try:
        validated = schema_cls.model_validate(props)
    except Exception as exc:  # noqa: BLE001 —— pydantic ValidationError → 契约失败
        raise ComponentSpecInvalid(f"{component_type} props schema 校验失败：{exc}") from exc
    if component_type == "evidence_rail":
        # T2.5 读时清洗：存量 evidence_rail 消息可能含教材 chunk 的 markdown 图片串；
        # 闸门是读写共用唯一收口，此处确定性剥离（不回写历史行）
        validated = validated.model_copy(
            update={
                "refs": [
                    r.model_copy(
                        update={"title": strip_media_syntax(r.title), "snippet": strip_media_syntax(r.snippet)}
                    )
                    for r in validated.refs
                ]
            }
        )
    issues: list[str] = []
    normalized = _normalize_brace_artifacts(validated.model_dump(mode="json"))
    _scan_strings(normalized, "props", issues)
    if issues:
        raise ComponentSpecInvalid("; ".join(issues[:5]))
    return normalized


__all__ = [
    "COMPONENT_CATALOG",
    "ALLOWED_COMPONENT_TYPES",
    "ComponentSpecInvalid",
    "validate_component",
]
