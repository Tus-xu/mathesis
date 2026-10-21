"""RV09 · ContextManifest —— 把"模型看到了什么"变成**可审计的清单**。

问题
----
把历史消息一股脑塞进提示词有三个后果，而且都不是理论风险：

1. **上下文超预算**：长会话在第 N 轮突然失败，而失败原因看起来像"模型坏了"；
2. **越权可见**：检索片段、别的项目的偏好、教师端记忆一旦混进去，
   用户看不到，审计也查不到（因为没人记录"这次到底放了什么"）；
3. **不可复现**：同一个问题两次得到不同答案，无法判断是模型随机性
   还是上下文不同。

本模块因此只做一件事：**把进入提示词的每一段都登记成一条可审计的记录**，
包括它为什么被包含、为什么被裁掉、以及它属于哪个命名空间。

六类段（任务书要求 3 的封闭词表）
--------------------------------
| kind | 内容 | 可否被裁 |
|---|---|---|
| `request` | 本轮用户请求 | **永不整体裁掉**（超预算时截断并标记） |
| `conversation` | 有限轮次的历史对话 | 可裁（按预算/轮数） |
| `session_summary` | 确定性会话摘要 | 可裁 |
| `domain_state` | 相关领域对象状态（ProblemMap 等的最新修订摘要） | 可裁 |
| `preference` | **已授权**的长期偏好（按命名空间） | 可裁，且未授权**必须**排除 |
| `evidence` | **已授权**且未失效的证据卡 | 可裁，且未授权**必须**排除 |

两条硬不变量
------------
1. **只放清单里登记的段**。渲染出的 `context_text` 与 `manifest.sections` +
   `manifest.excluded` 必须一一对应；测试会断言二者一致。
2. **裁剪必须给出理由**。`trimReason` 为空表示"完整包含"；
   非空表示"被裁掉/被截断"，前端可以据此提示"以下内容不完整"。

`research_ai_run.context_manifest` 只落**清单本身**（引用 + 字符数 + 理由），
不落正文 —— 用户原文已经在 `research_message` 里，重复落库只会扩大泄露面。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

MANIFEST_VERSION = "1.0.0"

SECTION_REQUEST = "request"
SECTION_CONVERSATION = "conversation"
SECTION_SUMMARY = "session_summary"
SECTION_DOMAIN = "domain_state"
SECTION_PREFERENCE = "preference"
SECTION_EVIDENCE = "evidence"

SECTION_KINDS: tuple[str, ...] = (
    SECTION_REQUEST,
    SECTION_CONVERSATION,
    SECTION_SUMMARY,
    SECTION_DOMAIN,
    SECTION_PREFERENCE,
    SECTION_EVIDENCE,
)

#: 裁剪理由（机器可处理）。
TRIM_NONE = ""
TRIM_BUDGET = "budget-exceeded"
TRIM_TURN_WINDOW = "over-turn-window"
TRIM_ROLE_WINDOW = "over-role-window"
TRIM_UNAUTHORIZED = "unauthorized"
TRIM_NAMESPACE = "namespace-mismatch"
TRIM_INVALIDATED = "evidence-invalidated"
TRIM_STALE = "evidence-stale"
TRIM_TOKEN_CAP = "section-char-cap"
TRIM_REQUEST_CLIPPED = "request-oversized-truncated"

#: 默认总预算（字符）。中文 ≈ 1 字 1 token 量级，24k 字符 ≈ 一个较宽的窗口。
DEFAULT_TOTAL_BUDGET = 24_000
#: 单段字符上限（防止"摘要"本身变成第二份全文）。
SECTION_CHAR_CAP: dict[str, int] = {
    SECTION_SUMMARY: 2_000,
    SECTION_DOMAIN: 4_000,
    SECTION_PREFERENCE: 1_200,
    SECTION_EVIDENCE: 4_000,
    SECTION_CONVERSATION: 12_000,
    SECTION_REQUEST: 8_000,
}
#: conversation 最多纳入的轮数（一条 user + 一条 assistant 记 1 轮）。
MAX_CONVERSATION_TURNS = 8

#: **结构性禁止**的来源：这些一旦出现说明有越界读取，直接抛错而不是静默丢弃。
FORBIDDEN_SOURCES: tuple[str, ...] = (
    "teacher",
    "teacher-platform",
    "teacher_platform",
    "other-project",
    "other_project",
    "cross-tenant",
)


class ContextManifestError(Exception):
    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass
class ContextSection:
    kind: str
    ref: str
    chars: int
    included: bool
    trim_reason: str = TRIM_NONE
    authorized: bool = True
    namespace: str = ""
    #: 正文（**只**存在于内存的这条记录里，不落 `research_ai_run`）。
    text: str = field(default="", repr=False)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "ref": self.ref,
            "chars": self.chars,
            "included": self.included,
        }
        if self.trim_reason:
            out["trimReason"] = self.trim_reason
        if not self.authorized:
            out["authorized"] = False
        if self.namespace:
            out["namespace"] = self.namespace
        return out


@dataclass
class ContextManifest:
    manifest_id: str
    namespace: dict[str, str]
    budget: int
    used: int
    truncated: bool
    sections: list[ContextSection]
    excluded: list[ContextSection]
    injection_signals: list[str] = field(default_factory=list)
    version: str = MANIFEST_VERSION

    @property
    def content_hash(self) -> str:
        blob = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifestVersion": self.version,
            "manifestId": self.manifest_id,
            "namespace": dict(self.namespace),
            "budget": {"totalChars": self.budget, "usedChars": self.used, "truncated": self.truncated},
            "sections": [s.as_dict() for s in self.sections],
            "excluded": [s.as_dict() for s in self.excluded],
            "injectionSignals": list(self.injection_signals),
            "contentHash": "",  # 由 to_record() 回填，避免自引用
        }

    def to_record(self) -> dict[str, Any]:
        """落库形状：**不含正文**。"""
        out = self.as_dict()
        out["contentHash"] = self.content_hash
        return out

    def section_refs(self) -> list[str]:
        return [s.ref for s in self.sections]

    def reason_for(self, ref: str) -> str | None:
        for s in self.sections:
            if s.ref == ref:
                return s.trim_reason or None
        for s in self.excluded:
            if s.ref == ref:
                return s.trim_reason or TRIM_BUDGET
        return None


def _chars(text: str) -> int:
    return len(text or "")


def _clip(text: str, limit: int) -> tuple[str, bool]:
    text = text or ""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def build_manifest(
    *,
    manifest_id: str,
    namespace: dict[str, str],
    request_text: str,
    conversation: list[dict[str, Any]] | None = None,
    session_summary: dict[str, Any] | None = None,
    domain_state: dict[str, Any] | None = None,
    preferences: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    budget: int = DEFAULT_TOTAL_BUDGET,
    injection_signals: list[str] | None = None,
) -> tuple[str, ContextManifest]:
    """构造 `(context_text, manifest)`。

    顺序即优先级：`request` → `session_summary` → `domain_state` →
    `conversation`（新→旧） → `preference` → `evidence`。
    预算先给高优先级段，剩下的才给低优先级段 —— 这是"裁掉旧闲聊而不是裁掉问题"。
    """
    if budget <= 0:
        raise ContextManifestError("CONTEXT_BUDGET_INVALID", "budget 必须为正数")

    sections: list[ContextSection] = []
    excluded: list[ContextSection] = []
    remaining = budget
    truncated = False

    def add(
        *,
        kind: str,
        ref: str,
        text: str,
        cap: int,
        authorized: bool = True,
        skip_reason: str = TRIM_NONE,
        ns: str = "",
        optional: bool = True,
    ) -> None:
        nonlocal remaining, truncated
        raw_chars = _chars(text)
        if skip_reason:
            excluded.append(
                ContextSection(
                    kind=kind, ref=ref, chars=raw_chars, included=False,
                    trim_reason=skip_reason, authorized=authorized, namespace=ns,
                )
            )
            return
        clipped, was_clipped = _clip(text, cap)
        reason = TRIM_TOKEN_CAP if was_clipped else TRIM_NONE
        if _chars(clipped) > remaining:
            if not optional:
                # `request` 是唯一的必需段：宁可截断问题，也不静默丢掉它。
                clipped = clipped[: max(remaining, 0)]
                reason = TRIM_REQUEST_CLIPPED
                was_clipped = True
            else:
                excluded.append(
                    ContextSection(
                        kind=kind, ref=ref, chars=raw_chars, included=False,
                        trim_reason=TRIM_BUDGET, authorized=authorized, namespace=ns,
                    )
                )
                truncated = True
                return
        remaining -= _chars(clipped)
        if was_clipped:
            truncated = True
        sections.append(
            ContextSection(
                kind=kind, ref=ref, chars=_chars(clipped), included=True,
                trim_reason=reason, authorized=authorized, namespace=ns, text=clipped,
            )
        )

    # ---- 1. request（必需） ----
    add(
        kind=SECTION_REQUEST,
        ref="message:current",
        text=request_text or "",
        cap=SECTION_CHAR_CAP[SECTION_REQUEST],
        optional=False,
    )

    # ---- 2. session_summary ----
    if session_summary is not None:
        add(
            kind=SECTION_SUMMARY,
            ref=str(session_summary.get("ref") or "summary:latest"),
            text=_render_summary(session_summary),
            cap=SECTION_CHAR_CAP[SECTION_SUMMARY],
        )

    # ---- 3. domain_state ----
    if domain_state:
        add(
            kind=SECTION_DOMAIN,
            ref=str(domain_state.get("ref") or "domain:session-objects"),
            text=_render_domain_state(domain_state),
            cap=SECTION_CHAR_CAP[SECTION_DOMAIN],
        )

    # ---- 4. conversation（新→旧，最多 MAX_CONVERSATION_TURNS 轮） ----
    turns = _group_turns(conversation or [])
    for idx, turn in enumerate(reversed(turns)):
        ref = str(turn.get("ref") or f"message:{turn.get('id') or idx}")
        if idx >= MAX_CONVERSATION_TURNS:
            excluded.append(
                ContextSection(
                    kind=SECTION_CONVERSATION, ref=ref, chars=_chars(turn.get("text") or ""),
                    included=False, trim_reason=TRIM_TURN_WINDOW,
                )
            )
            truncated = True
            continue
        add(
            kind=SECTION_CONVERSATION,
            ref=ref,
            text=f"[{turn.get('role', 'user')}] {turn.get('text') or ''}",
            cap=SECTION_CHAR_CAP[SECTION_CONVERSATION],
        )

    # ---- 5. preference（未授权 / 命名空间不符 ⇒ 必须排除并记录） ----
    for pref in preferences or []:
        ref = str(pref.get("ref") or f"preference:{pref.get('key', '')}")
        ns = str(pref.get("namespace") or "")
        # 来源守卫**先于**授权判断：一个"已授权但来自 Teacher 域"的偏好
        # 才是最危险的情况（它看起来合法，却越了域）。
        _reject_or_exclude(pref.get("source"), ref, "preference")
        if not pref.get("authorized", False):
            excluded.append(
                ContextSection(
                    kind=SECTION_PREFERENCE, ref=ref, chars=_chars(pref.get("value") or ""),
                    included=False, trim_reason=TRIM_UNAUTHORIZED, authorized=False, namespace=ns,
                )
            )
            continue
        if ns and ns != namespace_key_of(namespace):
            excluded.append(
                ContextSection(
                    kind=SECTION_PREFERENCE, ref=ref, chars=_chars(pref.get("value") or ""),
                    included=False, trim_reason=TRIM_NAMESPACE, namespace=ns,
                )
            )
            continue
        add(
            kind=SECTION_PREFERENCE,
            ref=ref,
            text=f"{pref.get('key', '')}: {pref.get('value', '')}",
            cap=SECTION_CHAR_CAP[SECTION_PREFERENCE],
            ns=ns,
        )

    # ---- 6. evidence（授权 + 未失效 + 非 stale） ----
    for card in evidence or []:
        ref = str(card.get("ref") or f"evidence:{card.get('id', '')}")
        _reject_or_exclude(card.get("source"), ref, "evidence")
        if not card.get("authorized", False):
            excluded.append(
                ContextSection(
                    kind=SECTION_EVIDENCE, ref=ref, chars=_chars(card.get("snippet") or ""),
                    included=False, trim_reason=TRIM_UNAUTHORIZED, authorized=False,
                )
            )
            continue
        if card.get("invalidated"):
            excluded.append(
                ContextSection(
                    kind=SECTION_EVIDENCE, ref=ref, chars=_chars(card.get("snippet") or ""),
                    included=False, trim_reason=TRIM_INVALIDATED,
                )
            )
            continue
        if str(card.get("stale_state", "")).upper() == "STALE":
            excluded.append(
                ContextSection(
                    kind=SECTION_EVIDENCE, ref=ref, chars=_chars(card.get("snippet") or ""),
                    included=False, trim_reason=TRIM_STALE,
                )
            )
            continue
        add(
            kind=SECTION_EVIDENCE,
            ref=ref,
            text=f"[{card.get('label', '')}] {card.get('snippet', '')}",
            cap=SECTION_CHAR_CAP[SECTION_EVIDENCE],
        )

    used = budget - remaining
    manifest = ContextManifest(
        manifest_id=manifest_id,
        namespace={k: str(v) for k, v in namespace.items()},
        budget=budget,
        used=used,
        truncated=truncated or any(s.trim_reason for s in sections),
        sections=sections,
        excluded=excluded,
        injection_signals=list(injection_signals or []),
    )
    return render(sections), manifest


def _reject_or_exclude(source: Any, ref: str, kind: str) -> None:
    """结构性越界来源 ⇒ 抛错（而不是静默丢弃）。

    静默丢弃会让"读到了教师端记忆"这件事在审计里消失，那正是最需要留下痕迹的情况。
    """
    src = str(source or "").lower()
    if not src:
        return
    for bad in FORBIDDEN_SOURCES:
        if bad in src:
            raise ContextManifestError(
                "CONTEXT_FORBIDDEN_SOURCE",
                f"{kind} 段的来源 {source!r} 属于禁止域（{bad}）：Research 不得读取 Teacher/其它项目记忆",
                ref=ref,
            )


def _group_turns(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把扁平消息列表按"轮"分组；已带 `turn` 的直接沿用。

    没有分组信息时，把每条消息视为半轮（user 起新轮，assistant 归入当前轮）。
    """
    turns: list[dict[str, Any]] = []
    for msg in conversation:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user")
        text = str(msg.get("text") or "")
        ref = str(msg.get("ref") or f"message:{msg.get('id') or len(turns)}")
        if role == "assistant" and turns and turns[-1].get("role") == "user":
            turns[-1]["role"] = "turn"
            turns[-1]["text"] = f"{turns[-1]['text']}\n[assistant] {text}"
            turns[-1]["ref"] = f"{turns[-1]['ref']}+{ref}"
            continue
        turns.append({"role": role, "text": text, "ref": ref})
    return turns


def _render_summary(summary: dict[str, Any]) -> str:
    content = summary.get("content") or summary
    return "会话摘要：" + json.dumps(content, ensure_ascii=False, sort_keys=True)


def _render_domain_state(state: dict[str, Any]) -> str:
    objects = state.get("objects") or state
    return "会话领域状态：" + json.dumps(objects, ensure_ascii=False, sort_keys=True)


def render(sections: list[ContextSection]) -> str:
    """把已包含的段渲染成提示词正文（与 manifest 一一对应）。"""
    lines: list[str] = []
    for s in sections:
        marker = ""
        if s.trim_reason:
            marker = f" [已裁剪: {s.trim_reason}]"
        lines.append(f"<<<SECTION kind={s.kind} ref={s.ref}{marker}>>>")
        lines.append(s.text)
        lines.append("<<<END SECTION>>>")
    return "\n".join(lines)


def namespace_key_of(namespace: dict[str, Any]) -> str:
    """命名空间键（与 `core/memory/namespaces.py` 同口径）。

    放在这里是为了让"上下文里的偏好属于谁"与"记忆读写按谁隔离"用**同一个**函数，
    避免两套拼法（那正是跨租户泄漏的常见成因）。
    """
    from ..memory.namespaces import namespace_key

    return namespace_key(
        tenant_id=namespace.get("tenantId") or namespace.get("tenant_id") or "",
        user_id=namespace.get("userId") or namespace.get("user_id") or "",
        research_id=namespace.get("researchId") or namespace.get("research_id") or "",
        purpose=namespace.get("purpose") or "",
    )


__all__ = [
    "DEFAULT_TOTAL_BUDGET",
    "FORBIDDEN_SOURCES",
    "MANIFEST_VERSION",
    "MAX_CONVERSATION_TURNS",
    "SECTION_CHAR_CAP",
    "SECTION_CONVERSATION",
    "SECTION_DOMAIN",
    "SECTION_EVIDENCE",
    "SECTION_KINDS",
    "SECTION_PREFERENCE",
    "SECTION_REQUEST",
    "SECTION_SUMMARY",
    "TRIM_BUDGET",
    "TRIM_INVALIDATED",
    "TRIM_NAMESPACE",
    "TRIM_NONE",
    "TRIM_REQUEST_CLIPPED",
    "TRIM_ROLE_WINDOW",
    "TRIM_STALE",
    "TRIM_TOKEN_CAP",
    "TRIM_TURN_WINDOW",
    "TRIM_UNAUTHORIZED",
    "ContextManifest",
    "ContextManifestError",
    "ContextSection",
    "build_manifest",
    "namespace_key_of",
    "render",
]
