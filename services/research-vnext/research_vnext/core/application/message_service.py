"""消息应用服务（CR-6 / ADR-RV-003 Option C 双层模型）。

**关键不变量**：
1. message 是**会话记录**，不承载正式领域真相；
2. LLM 输出只能是 typed proposal，落正式状态必须经 PendingAction → confirm；
3. 消息写入本身是受 `expected_revision` 保护的命令；
4. `client_message_id` 复用 `client_request_id` 幂等语义（服务端摘要为权威判据）；
5. `timeline` 是持久消息的**投影**。

**事务结构（诚实说明，不假装是单事务）**：
- `tx1`：锁会话行 → 分配 `seq` → 写 user message + domain event + outbox。**立即提交**。
  这样"用户说的话"在任何后续失败下都不会丢。
- LLM 调用在事务**之外**（不让模型时延占着数据库行锁）。
- `tx2`：写 assistant message（+ 需要时 PendingAction）+ domain event + outbox +
  幂等记录，一次提交。

`tx1` 与 `tx2` **各自**满足"事件与 outbox 同事务"的铁律；
跨事务的原子性由幂等记录 + `client_request_id` 兜住：崩溃在两者之间时，
重试会发现 user 行已存在而**不重复插入**，然后补齐 assistant 侧。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..contracts.command import ResearchCommandEnvelope  # noqa: F401  (契约类型留作参考)
from ..db.engine import session_scope
from ..db.models import (
    DomainEventRow,
    OutboxEventRow,
    PendingActionAuditRow,
    PendingActionRow,
    ResearchAiRunRow,
    ResearchMessageRow,
    ResearchSessionRow,
)
from ..domain import proposals as P
from ..domain import proposal_handlers as H
from ..domain import output_guard as guard
from ..domain.errors import DomainError, IdempotencyConflict, RevisionConflict
from ..domain.events import DomainEvent
from ..domain.snapshot import build_snapshot
from ..llm import gateway
from ..llm import capacity as gateway_capacity
from ..llm import pricing
from ..llm.prompts import registry as prompts
from ..memory import short_term as memory
from ..observability.redaction import redact_mapping
from ..observability.trace import TraceContext
from ..application import context_service
from ..application import session_service as svc

MESSAGE_COMMAND_TYPE = "session.message"
MAX_MESSAGE_CHARS = 8000
CLIENT_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
PENDING_TTL_HOURS = 24

#: assistant 侧可用状态 —— 前端用 `research-message === 'available'` 决定 composer。
STATE_AVAILABLE = "available"
STATE_DISABLED = "capability-disabled"
STATE_UNAVAILABLE = "unavailable"


class MessageValidationError(DomainError):
    code = "MESSAGE_INVALID"
    http_status = 422


class MessageInFlight(DomainError):
    """同一 `client_message_id` 的上一轮尚未写完 —— 不能静默当作成功。"""

    code = "MESSAGE_IN_FLIGHT"
    http_status = 409


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _notice(error_code: str, model: str) -> str:
    """模型不可用/输出不合格时的**诚实**说明。绝不编造助手回答。"""
    if error_code == "LLM_NOT_CONFIGURED":
        return (
            "模型不可用：本服务未配置 LLM 凭据"
            "（RESEARCH_LLM_API_KEY / SPARK_API_PASSWORD）。"
            "你的消息已保存；配置凭据后重试即可获得助手回复。"
        )
    if error_code in ("PROPOSAL_SCHEMA_INVALID", "PROPOSAL_NOT_JSON", "PROPOSAL_NOT_OBJECT", "PROPOSAL_EMPTY"):
        return (
            "模型返回的内容不符合结构化提案契约，已被服务端拒绝（不会写入任何正式状态）。"
            "你的消息已保存；可重试。"
        )
    if error_code == "PROPOSAL_FORBIDDEN_KEY":
        return "模型尝试输出被禁止的内容类别（HTML/CSS/组件名），已被服务端拒绝。你的消息已保存。"
    if error_code == "LLM_MODEL_ACCOUNT_MISMATCH":
        # 单一供应商 + 按账号隔离的模型授权：配错账号是**可操作的配置错误**，
        # 重试不会恢复。说明必须指向"换哪把 key"，而不是"稍后重试"。
        return (
            "模型不可用：配置的凭据未被授权访问该模型（供应商返回 403）。"
            "本平台的模型授权按账号隔离，配错账号不会因为重试而恢复；"
            "请按 /llmz 的提示改用正确账号的凭据。你的消息已保存。"
        )
    if error_code == "LLM_RATE_LIMITED":
        return (
            "模型暂时不可用：单一供应商账号的速率上限已被触及（429）。"
            "本平台没有第二供应商可降级，请稍后重试。你的消息已保存。"
        )
    if error_code == "LLM_UNAUTHORIZED":
        return "模型不可用：供应商返回 401，凭据无效或已过期。你的消息已保存。"
    if error_code == "PROMPT_NOT_PUBLISHED":
        # 这是**部署配置**问题，不是模型问题：不发布就绝不用它跑真实研究者。
        return (
            "助手当前不可用：服务端没有已发布的提示词版本（PROMPT_NOT_PUBLISHED）。"
            "这是部署配置问题，重试不会恢复 —— 需要运维发布提示词版本。你的消息已保存。"
        )
    if error_code == "LLM_OUTPUT_TRUNCATED":
        # 截断与"模型不听话"是**两回事**，运维处置也不同：
        # 前者加预算，后者改提示词。因此单独成型，并给出可操作的下一步。
        return (
            "模型本次输出被 max_tokens 上限截断（finish_reason=length），"
            "服务端按契约拒绝该轮：不写入任何正式状态。"
            "这是**预算**问题而不是模型能力问题 —— 请提高 RESEARCH_LLM_MAX_TOKENS 后重试。"
            "你的消息已保存。"
        )
    if error_code == "OUTPUT_FORBIDDEN_MARKUP":
        # 模型输出了 HTML/脚本/模板标记 ⇒ 可能已被不可信数据带偏。整轮拒绝。
        return (
            "模型本次回复包含被禁止的内容类别（HTML / 脚本 / 对话模板标记），已被服务端整轮拒绝："
            "不写入任何正式状态，也不会展示这份输出。你的消息已保存，可重试。"
        )
    if error_code == "RESEARCH_MAP_REQUIRES_PROBLEM_MAP":
        return (
            "还不能给出调研/验证视角：本会话尚未确认问题地图（ProblemMap）。"
            "请先在对话里确认问题地图 —— 视角的取舍标准来自地图里的成功与证伪判据。"
            "你的消息已保存。"
        )
    if error_code == "RESEARCH_MAP_IS_CHECKLIST":
        return (
            "模型给出的是步骤清单，不是多视角研究地图（RESEARCH_MAP_IS_CHECKLIST），已被拒绝。"
            "研究地图需要 2–4 个可区分视角，各自带交付物、停止条件与不确定性。你的消息已保存。"
        )
    if error_code == "PROBLEM_MAP_CONTRADICTORY":
        return (
            "模型整理出的问题地图内部自相矛盾，已被拒绝落库（不以矛盾为前提继续推进）。"
            "已在下方向你列出冲突点，请澄清后重试。你的消息已保存。"
        )
    if error_code.startswith(
        ("CLAIM_", "PROBLEM_MAP_", "RESEARCH_MAP_", "ROUTE_ARENA_", "ROUTE_", "BLUEPRINT_")
    ) or error_code in (
        "MAP_NODE_TYPE_UNKNOWN",
        "MAP_EDGE_TYPE_UNKNOWN",
        "MAP_DANGLING_EDGE",
        "MAP_EMPTY",
        "BLUEPRINT_NO_ROUTE",
        "BLUEPRINT_UNVERIFIED_NOT_INFERENCE",
        "PLAN_NO_DATASET",
        "PAPER_NO_BLOCKS",
        "PAPER_BLOCK_TYPE_UNKNOWN",
        "REVIEW_NO_FINDINGS",
        "REVIEW_CATEGORY_UNKNOWN",
        "INTAKE_EMPTY",
        "CLAIM_STATUS_UNKNOWN",
        "CLAIM_SUPPORT_UNKNOWN",
    ):
        # 模型给的 JSON 合法，但**违反领域规则** —— 这是"提案被拒"，
        # 不是"调用失败"。混为一谈会让运维去查网络，而真正要改的是 prompt。
        return (
            f"模型提出的结构化提案未通过领域校验（{error_code}），已被服务端拒绝："
            "不会写入任何正式状态。你的消息已保存。"
        )
    if error_code == "LLM_EMPTY_CHOICES":
        return f"模型（{model}）返回空响应，无法生成助手回复。你的消息已保存。"
    return f"模型调用失败（{error_code}），未能生成助手回复。你的消息已保存。"


#: 这些拒绝码不是"提案写错了"，而是"**信息不足 / 前提不成立**"。
#: 对它们必须给出可补充的清单，而不是一句"重试"——重试同一个含糊问题不会有不同结果。
CLARIFICATION_CODES = frozenset(
    {
        "PROBLEM_MAP_CONTRADICTORY",
        "PROBLEM_MAP_QUESTION_INSUFFICIENT",
        "PROBLEM_MAP_MISSING_CORE_SECTION",
        "PROBLEM_MAP_NO_QUESTION",
        "RESEARCH_MAP_REQUIRES_PROBLEM_MAP",
        # RV10：blueprint 的绑定前提 —— 缺哪个已确认对象就请用户先确认哪个。
        "BLUEPRINT_REQUIRES_PROBLEM_MAP",
        "BLUEPRINT_REQUIRES_RESEARCH_MAP",
        "BLUEPRINT_REQUIRES_ROUTE_ARENA",
        # `INTAKE_EMPTY`（2026-09-14 由 E2E 发现）：
        # 模型承认"我定不出问题陈述" —— 这正是信息不足，
        # 必须以**可补充清单**呈现，而不是一句"提案未通过领域校验"。
        # 归错类的后果很具体：用户看到的是"重试"，而重试同一个含糊输入不会有不同结果。
        "INTAKE_EMPTY",
    }
)

#: 缺口段 → 给用户看的中文说法（确定性文案，**不**经模型生成）。
_GAP_ROLE = {
    "researchQuestion": "研究问题（一句话，说明求什么 / 证明什么）",
    "scope": "范围（哪些要做）",
    "nonGoals": "非目标（哪些明确不做）",
    "knowns": "已确知的条件",
    "unknowns": "未知项",
    "constraints": "约束（时间/工具/精度/合规）",
    "successCriteria": "成功判据（什么结果算回答了这个研究问题）",
    "falsificationCriteria": "证伪判据（什么结果会推翻它）",
    "assumptions": "前提假设",
    "risks": "风险与缓解",
    "requiredEvidence": "需要什么证据才算回答",
    "problemMap": "已确认的问题地图（ProblemMap）",
    "researchMap": "已确认的研究地图（ResearchMap）",
    "routeArena": "已确认的路线竞技场（RouteArena）",
}


def _clarification_for(
    e: "H.ProposalRejected", *, proposal: P.Proposal, user_text: str = ""
) -> dict | None:
    """把"信息不足/前提不成立"翻译成**可补充的清单**。

    文案由服务端确定性生成 —— 这里刻意不调用模型：请用户补充的内容
    必须是"服务端校验发现的缺口"，而不是模型自由发挥的追问。

    `user_text` 用于矛盾场景：冲突可能出现在**用户原话**而不是模型的改写里
    （见 `proposal_handlers._validate_problem_map` 的说明），
    因此这里同样要扫用户原话，否则卡片上只会写"发生冲突"却指不出哪两句话冲突。
    """
    if e.code not in CLARIFICATION_CODES:
        return None

    gaps: list[dict] = []
    if e.code == "PROBLEM_MAP_CONTRADICTORY":
        found: list[dict] = []
        try:
            from ..domain import problem_map as PM

            normalized = PM.normalize(proposal.payload or {})
            found.extend(normalized.contradictions)
            if user_text:
                found.extend(c.as_dict() for c in PM.detect_contradiction(user_text))
        except Exception:  # noqa: BLE001 - 复核失败不改变结论，只影响清单详细度
            found = []
        seen: set[tuple] = set()
        for c in found:
            key = (c.get("kind"), c.get("a"), c.get("b"))
            if key in seen:
                continue
            seen.add(key)
            gaps.append(
                {
                    "kind": "CONTRADICTION",
                    "section": "researchQuestion",
                    "a": c.get("a", ""),
                    "b": c.get("b", ""),
                    "detail": c.get("detail", ""),
                }
            )
        if not gaps:
            gaps = [{"kind": "CONTRADICTION", "section": "researchQuestion", "detail": e.message}]
    elif e.code == "RESEARCH_MAP_REQUIRES_PROBLEM_MAP":
        gaps = [{"section": "problemMap", "reason": "MISSING_CONFIRMED_PROBLEM_MAP"}]
    elif e.code == "BLUEPRINT_REQUIRES_PROBLEM_MAP":
        gaps = [{"section": "problemMap", "reason": "MISSING_CONFIRMED_PROBLEM_MAP"}]
    elif e.code == "BLUEPRINT_REQUIRES_RESEARCH_MAP":
        gaps = [{"section": "researchMap", "reason": "MISSING_CONFIRMED_RESEARCH_MAP"}]
    elif e.code == "BLUEPRINT_REQUIRES_ROUTE_ARENA":
        gaps = [{"section": "routeArena", "reason": "MISSING_CONFIRMED_ROUTE_ARENA"}]
    elif e.code == "INTAKE_EMPTY":
        gaps = [{"section": "researchQuestion", "reason": "NO_PROBLEM_STATEMENT"}]
    elif e.code == "PROBLEM_MAP_NO_QUESTION":
        gaps = [{"section": "researchQuestion", "reason": "NO_PROBLEM_STATEMENT"}]
    else:
        gaps = [{"section": "researchQuestion", "reason": e.code, "detail": e.message}]

    lines = ["这次我没法把问题整理成可确认的结构，原因是：", ""]
    for gap in gaps:
        role = _GAP_ROLE.get(str(gap.get("section")), str(gap.get("section")))
        if gap.get("kind") == "CONTRADICTION":
            lines.append(f"- 冲突：`{gap.get('a')}` 与 `{gap.get('b')}` 互相否定（{gap.get('detail', '')}）")
        else:
            lines.append(f"- 缺少：{role}")
    lines.append("")
    lines.append("请补充/澄清后再发一次。在你确认之前，我不会替你补这些内容。")

    return {
        "code": e.code,
        "askText": "\n".join(lines),
        "gaps": gaps,
        "requiresUserInput": True,
    }


def _reason_code(error_code: str) -> str:
    """能力 `reason` 用**小写下划线**（机器可处理），与错误码同源但形式归一。

    前端把 `reason` 直接展示给运维/用户，混用 `LLM_NOT_CONFIGURED` 与
    `llm_not_configured` 会制造两套判据；这里统一成后者。
    """
    return (error_code or "llm_error").lower()


# --------------------------------------------------------------------------
# 幂等（复用 command_record 语义：服务端摘要为唯一权威）
# --------------------------------------------------------------------------


def _payload_digest(command_type: str, payload: dict, expected_revision: int | None) -> str:
    """用**同一个** `canonical_digest_of` 机制算摘要。

    ADR-RV-003 明确要求：不得重新引入"只信调用方"的幂等检查，
    因此 message 命令与 envelope 命令共用同一份规范式实现。
    """
    return svc.canonical_digest_of(command_type, payload, expected_revision)


# --------------------------------------------------------------------------
# 附件（RV08）：只接受**已入库资产**的引用，字节永远不进消息
# --------------------------------------------------------------------------

#: 单条消息最多附件数
MAX_ATTACHMENTS = 10


def _attachment_kind(media_type: str) -> str:
    """按嗅探后的 media_type 归类（供前端选择渲染器）。"""
    mt = (media_type or "").lower()
    if mt.startswith("image/"):
        return "image"
    if mt == "application/pdf":
        return "pdf"
    if mt.startswith("text/"):
        return "text"
    if mt in {"text/csv", "application/json"}:
        return "dataset"
    return "other"


def _normalize_attachments(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    attachments: list | None,
) -> list[dict]:
    """校验附件引用并规范化为可存库/可投影的形状。

    对象级规则与 `assets.service.authorize_asset` 对齐：
    必须存在、同 tenant、且 actor 是 owner 或在 readers ACL 中。
    任何不满足都**明确报错**，不静默丢弃。
    """
    if not attachments:
        return []
    if not isinstance(attachments, list):
        raise MessageValidationError("attachments 必须是数组")
    if len(attachments) > MAX_ATTACHMENTS:
        raise MessageValidationError(f"attachments 最多 {MAX_ATTACHMENTS} 项")

    from ..assets.models import ResearchAssetRow

    out: list[dict] = []
    for item in attachments:
        raw_id = item if isinstance(item, str) else (item or {}).get("asset_id") or (item or {}).get("assetId")
        if not raw_id:
            raise MessageValidationError("附件项缺少 asset_id")
        try:
            asset_id = uuid.UUID(str(raw_id))
        except (ValueError, AttributeError) as e:
            raise MessageValidationError(f"asset_id 非法：{raw_id!r}") from e

        row = db.get(ResearchAssetRow, asset_id)
        if row is None:
            raise MessageValidationError(f"附件不存在：{asset_id}")
        if row.tenant_id != tenant_id:
            raise MessageValidationError("附件不属于当前 tenant")
        readers = set((row.acl or {}).get("readers") or [])
        if row.owner_id != actor_id and str(actor_id) not in readers:
            raise MessageValidationError("附件缺少读取权限")

        out.append(
            {
                "asset_id": str(row.id),
                "kind": _attachment_kind(row.media_type),
                "filename": row.original_filename or "",
                "media_type": row.media_type or "",
                "size_bytes": int(row.size_bytes or 0),
            }
        )
    return out


def _find_record(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, crid: str):
    from ..db.models import CommandRecordRow

    return (
        db.query(CommandRecordRow)
        .filter(
            CommandRecordRow.tenant_id == tenant_id,
            CommandRecordRow.actor_id == actor_id,
            CommandRecordRow.client_request_id == crid,
        )
        .one_or_none()
    )


# --------------------------------------------------------------------------
# 内部：事件 + outbox（同事务）
# --------------------------------------------------------------------------


def _emit(db: Session, ev: DomainEvent) -> DomainEventRow:
    db.add(
        DomainEventRow(
            event_id=ev.event_id,
            session_id=ev.session_id,
            event_type=ev.event_type,
            schema_version=ev.schema_version,
            revision=ev.revision,
            occurred_at=ev.occurred_at,
            trace_id=ev.trace_id,
            payload=redact_mapping(ev.payload),
            actor_id=ev.actor_id,
            aggregate_type=ev.aggregate_type,
        )
    )
    db.add(
        OutboxEventRow(
            event_id=ev.event_id,
            session_id=ev.session_id,
            event_type=ev.event_type,
            schema_version=ev.schema_version,
            payload=redact_mapping(ev.payload),
            trace_id=ev.trace_id,
            status="PENDING",
            attempts=0,
            published_at=None,
            created_at=ev.occurred_at,
        )
    )
    return ev


# --------------------------------------------------------------------------
# 写：send_message
# --------------------------------------------------------------------------


def _next_seq(db: Session, session_id: uuid.UUID) -> int:
    """分配下一个 `seq`。

    调用方**必须**已经锁住会话行（`_lock_session_row`）：`SELECT max(seq)+1`
    本身不是原子的，只有在同一事务里持有会话行锁时，并发发送才会被串行化，
    从而不会撞上 `(session_id, seq)` 唯一约束。
    """
    current = db.scalar(
        select(func.max(ResearchMessageRow.seq)).where(ResearchMessageRow.session_id == session_id)
    )
    return int(current or 0) + 1


def _lock_session_row(db: Session, session_id: uuid.UUID) -> ResearchSessionRow:
    """`SELECT ... FOR UPDATE` 锁住会话行 —— 并发 message 写入的串行化点。"""
    row = (
        db.query(ResearchSessionRow)
        .filter(ResearchSessionRow.id == session_id)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        from ..domain.errors import SessionNotFound

        raise SessionNotFound(f"会话不存在: {session_id}")
    return row


def get_message_snapshot(db: Session, session_id: uuid.UUID, *, message_state: str = STATE_AVAILABLE,
                         message_reason: str = "") -> dict:
    """把会话投影成 `ResearchSnapshot`（`GET /sessions/{id}` 与 POST 共用）。"""
    row = db.get(ResearchSessionRow, session_id)
    if row is None:
        from ..domain.errors import SessionNotFound

        raise SessionNotFound(f"会话不存在: {session_id}")
    return build_snapshot(db, row, message_state=message_state, message_reason=message_reason)


def default_message_state() -> tuple[str, str]:
    """未指定时的能力状态：未配置凭据 ⇒ 诚实标记不可用。"""
    if gateway.llm_configured():
        return STATE_AVAILABLE, ""
    return STATE_UNAVAILABLE, "llm_not_configured"


async def send_message(
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    message: str,
    expected_revision: int | None,
    client_message_id: str | None,
    attachments: list | None,
    trace: TraceContext,
) -> tuple[dict, str, int]:
    """返回 `(snapshot, idempotency_status, http_status)`。

    `idempotency_status ∈ {EXECUTED, REPLAYED}`；http 状态 201（新写入）/ 200（重放）。
    """
    text = (message or "").strip()
    if not text:
        raise MessageValidationError("message 不能为空")
    if len(message) > MAX_MESSAGE_CHARS:
        raise MessageValidationError(f"message 超过 {MAX_MESSAGE_CHARS} 字符上限")

    with session_scope() as db:
        normalized_attachments = _normalize_attachments(
            db, tenant_id=tenant_id, actor_id=actor_id, attachments=attachments
        )

    crid = (client_message_id or "").strip()
    if crid and not CLIENT_REQUEST_ID_RE.match(crid):
        raise MessageValidationError(
            "client_message_id 必须匹配 ^[A-Za-z0-9._:-]{8,128}$"
        )
    had_crid = bool(crid)
    if not crid:
        crid = f"auto-{uuid.uuid4().hex}"

    digest_payload = {
        "message": text,
        # 附件参与幂等摘要：同 crid + 同文本 + **不同附件**不得被判为同一命令
        "attachments": [a["asset_id"] for a in normalized_attachments],
    }
    server_digest = _payload_digest(MESSAGE_COMMAND_TYPE, digest_payload, expected_revision)

    # ---------------- 幂等检查（服务端摘要为权威） ----------------
    with session_scope() as db:
        existing = _find_record(db, tenant_id=tenant_id, actor_id=actor_id, crid=crid)
        if existing is not None and had_crid:
            if existing.canonical_digest is None:
                raise IdempotencyConflict(
                    f"client_message_id {crid!r} 已有历史记录但缺少服务端摘要，"
                    "无法证明等价，拒绝按重放处理"
                )
            import hmac as _hmac

            if not _hmac.compare_digest(existing.canonical_digest, server_digest):
                raise IdempotencyConflict(
                    f"client_message_id {crid!r} 已用于不同内容（服务端摘要不一致）"
                )
            if existing.response_body:
                body = dict(existing.response_body)
                state = body.pop("_message_state", STATE_AVAILABLE)
                reason = body.pop("_message_reason", "")
                body["capabilities"] = _recompute_capabilities(db, session_id, state, reason)
                return body, "REPLAYED", 200
            # 记录存在但响应体为空 ⇒ 上一轮死在 LLM 阶段。
            # 继续走,但**不重复插入** user 行（见下方 already_user）。

    # ---------------- tx1：user message + event + outbox（立即提交） ----------------
    user_seq: int | None = None
    with session_scope() as db:
        row = _lock_session_row(db, session_id)

        if expected_revision is not None and expected_revision != row.revision:
            raise RevisionConflict(expected_revision, row.revision, f"snapshot:{row.id}")

        already = (
            db.query(ResearchMessageRow)
            .filter(
                ResearchMessageRow.session_id == session_id,
                ResearchMessageRow.client_request_id == crid,
                ResearchMessageRow.role == "user",
            )
            .one_or_none()
        )
        if already is not None:
            user_seq = already.seq
        else:
            user_seq = _next_seq(db, session_id)
            user_payload: dict | None = None
            if normalized_attachments:
                user_payload = {"attachments": normalized_attachments}
            db.add(
                ResearchMessageRow(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    tenant_id=tenant_id,
                    role="user",
                    text=text,
                    payload=user_payload,
                    evidence_refs=[],
                    seq=user_seq,
                    created_at=_now(),
                    created_by_run_id=None,
                    trace_id=trace.trace_id,
                    client_request_id=crid,
                )
            )
            row.revision += 1
            row.updated_at = _now()
            db.flush()
            ev = DomainEvent.create(
                event_type="session.message_created",
                session_id=session_id,
                revision=row.revision,
                payload={"role": "user", "seq": user_seq, "chars": len(text)},
                trace_id=trace.trace_id,
                actor_id=actor_id,
            )
            _emit(db, ev)
            row.last_event_id = ev.event_id

    # ---------------- 上下文清单 + 提示词发布门禁（RV09，**调用之前**） ----------------
    #
    # 这两步必须在调用**之前**完成，理由不同：
    # - 上下文清单：它是"模型看到了什么"的唯一权威记录。若在调用后才组装，
    #   一旦组装失败，我们就有一次无法解释的调用；
    # - 发布门禁：未发布的 prompt 直接拒绝运行，不能先花掉配额再报错。
    request_scan = guard.scan_input(text)
    context_record: dict = {}
    context_text = ""
    prompt_not_published = False
    try:
        with session_scope() as db:
            session_row = db.get(ResearchSessionRow, session_id)
            context_text, context_record = context_service.build_context(
                db,
                session=session_row,
                actor_id=actor_id,
                request_text=text,
                exclude_client_request_id=crid,
                request_scan_signals=request_scan.signals,
            )
    except Exception as e:  # noqa: BLE001 - 组装失败必须降级而不是 500
        context_record = {"manifestVersion": "1.0.0", "error": f"CONTEXT_BUILD_FAILED:{type(e).__name__}"}

    try:
        prompt = prompts.require_published(prompts.ASSISTANT_PROPOSE_ID)
    except prompts.PromptNotPublished as e:
        prompt = prompts.PROPOSE
        prompt_not_published = True
        run_status = "REJECTED"
        error_code = e.code

    # ---------------- 供应商容量闸（**在发出请求之前**） ----------------
    #
    # 本平台只有一个供应商账号（见 MODEL-INVENTORY.md），没有供应商级容灾。
    # 因此必须在**调用之前**挡住并发突刺，而不是等供应商回 429 ——
    # 后者意味着请求已经消耗了那唯一账号的配额。
    capacity = gateway_capacity.get_capacity()
    lease = capacity.acquire(user_id=actor_id, session_id=session_id)

    # ---------------- LLM（事务外） ----------------
    backend = gateway.get_backend()
    completion_text = ""
    run_status = "FAILED" if not prompt_not_published else "REJECTED"
    error_code = error_code if prompt_not_published else ""
    model_name = backend.model
    provider_name = getattr(backend, "name", "unknown")
    in_tok = out_tok = latency = 0
    proposal: P.Proposal | None = None
    output_scan = guard.OutputScan()

    messages = [
        {"role": "system", "content": prompt.system},
        {
            "role": "user",
            "content": prompt.render(
                context_manifest=context_text or "（本会话暂无可用上下文）",
                user_text=prompts.wrap_untrusted(text),
                title="",
                mode="",
                timeline_len=str(user_seq),
            ),
        },
    ]
    model_escalated = False
    try:
        if prompt_not_published:
            # 不发出请求：未发布的 prompt 不允许消耗配额。
            raise prompts.PromptNotPublished(prompts.ASSISTANT_PROPOSE_ID, None, "")
        # RV09：结构化提案默认走"长结构化输出"模型（`llm_proposal_model()`）。
        # 依据是真实实测 —— `spark-x2.5-4b` 会把 16384 的预算全花在 reasoning 上，
        # 落在 `finish_reason=length`，表现为"模型不听话"其实是**预算被推理吃光**。
        proposal_model = gateway.llm_proposal_model()
        completion = await backend.acompletion(
            model=proposal_model if proposal_model != backend.model else None,
            messages=messages,
            stream=False,
        )
        model_escalated = completion.model != backend.model
        completion_text = completion.text
        model_name = completion.model
        provider_name = completion.provider
        in_tok, out_tok, latency = (
            completion.input_tokens,
            completion.output_tokens,
            completion.latency_ms,
        )
        # 截断识别放在解析**之前**：被截断的输出必然是"不是合法 JSON"，
        # 若先解析就只会得到 PROPOSAL_NOT_JSON —— 而它的运维处置是错的
        # （会去改提示词，真正要改的是预算）。RV09 的真实 E2E 实测中，
        # 失败样本的 output_tokens **恰好都等于上限**，这就是截断的特征。
        finish_reason = str((completion.raw or {}).get("finish_reason") or "")
        if finish_reason == "length":
            error_code = "LLM_OUTPUT_TRUNCATED"
            run_status = "REJECTED"
        else:
            # 输出侧守卫：**先**看有没有越界标记，再谈解析。
            # 一个包含 `<script>` 的回复即使 JSON 部分合法，也不再可信 ——
            # 它说明模型可能已被数据块里的内容带偏。
            output_scan = guard.scan_output(completion_text)
            if not output_scan.ok:
                error_code = "OUTPUT_FORBIDDEN_MARKUP"
                run_status = "REJECTED"
            else:
                proposal = P.parse_proposal(completion_text)
                run_status = "SUCCEEDED"
    except prompts.PromptNotPublished:
        pass  # 已在上面置好 REJECTED
    except gateway.LlmGateError as e:
        # 调用本身失败 ⇒ FAILED（模型没给出可用输出）
        error_code = e.code
        run_status = "FAILED"
    except P.ProposalSchemaError as e:
        # 模型**给了**输出，但不满足契约 ⇒ REJECTED（这是"拒绝"，不是"调用失败"）。
        # 两者必须可区分：前者要重试，后者要改 prompt/模型。
        error_code = e.code
        run_status = "REJECTED"
    except Exception as e:  # noqa: BLE001 - 任何异常都必须降级，绝不放任 500
        error_code = f"UNEXPECTED_{type(e).__name__}"
        run_status = "FAILED"
    finally:
        capacity.release(lease)

    # 成本：**不知道就说不知道**（`usd=None` + 原因），绝不写成 0。
    cost = pricing.estimate_cost(
        model=model_name, input_tokens=in_tok, output_tokens=out_tok, provider=provider_name
    )

    # ---------------- tx2：assistant message（+ PendingAction）+ event + outbox + 幂等 ----------------
    with session_scope() as db:
        row = _lock_session_row(db, session_id)
        run_id = uuid.uuid4()
        assistant_seq = _next_seq(db, session_id)
        assistant_id = uuid.uuid4()

        prepared: H.PreparedProposal | None = None
        payload_for_message: dict | None = None
        assistant_text = ""
        clarification: dict | None = None
        rejection_detail = ""

        if run_status == "SUCCEEDED" and proposal is not None:
            try:
                prepared = H.prepare(
                    db, proposal, session_id=session_id, actor_id=actor_id, user_text=text
                )
            except H.ProposalRejected as e:
                error_code = e.code
                run_status = "REJECTED"
                prepared = None
                # 拒绝详情（例如"r2 因 ROUTE_EVIDENCE_UNRESOLVED 被拒"）必须到达用户——
                # 只给一个错误码，用户无从判断是哪条路线、因什么被拒。
                rejection_detail = e.message
                # 信息不足/自相矛盾 ≠ 提案错误。这两种情况**不该**只给一句道歉：
                # 用户需要知道缺什么才能补。缺口来自服务端校验结果，不是模型新编的。
                clarification = _clarification_for(e, proposal=proposal, user_text=text)

        if run_status == "SUCCEEDED":
            assistant_text = proposal.text.strip() or "（模型未给出文字说明）"
            payload_for_message = {
                "proposal": {
                    "kind": proposal.kind,
                    "requires_confirmation": bool(proposal.requires_confirmation),
                    "object_ref": prepared.object_ref if prepared else "",
                    "content": prepared.content if prepared else {},
                }
            }
            message_state = STATE_AVAILABLE
            message_reason = ""
        elif clarification is not None:
            assistant_text = clarification["askText"]
            payload_for_message = {"clarification": clarification}
            # 明确保持 `available`：请用户补充的前提是输入框还能用。
            message_state = STATE_AVAILABLE
            message_reason = _reason_code(error_code)
        else:
            assistant_text = _notice(error_code, model_name)
            if rejection_detail:
                # 拒绝详情是服务端校验的原文（哪条路线、因什么被拒），不是模型自由发挥。
                assistant_text = f"{assistant_text}\n\n{rejection_detail}"
            payload_for_message = {
                "notice": {
                    "error_code": error_code,
                    "model": model_name,
                    "detail": rejection_detail,
                }
            }
            message_state = STATE_UNAVAILABLE if error_code == "LLM_NOT_CONFIGURED" else STATE_DISABLED
            message_reason = _reason_code(error_code)

        db.add(
            ResearchMessageRow(
                id=assistant_id,
                session_id=session_id,
                tenant_id=tenant_id,
                role="assistant",
                text=assistant_text,
                payload=payload_for_message,
                evidence_refs=list(proposal.evidence_refs) if proposal else [],
                seq=assistant_seq,
                created_at=_now(),
                created_by_run_id=run_id,
                trace_id=trace.trace_id,
                client_request_id=crid,
            )
        )
        # 顺序重要：message 先入库，ai_run 后（逻辑上 run 产出 message）
        db.flush()
        db.add(
            ResearchAiRunRow(
                id=run_id,
                session_id=session_id,
                tenant_id=tenant_id,
                message_id=assistant_id,
                provider=provider_name,
                model=model_name,
                prompt_ref=prompt.ref,
                status=run_status,
                error_code=error_code,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=latency,
                trace_id=trace.trace_id,
                created_at=_now(),
                # RV09：上下文清单（**不含正文**）、发出版本与发布态、成本与依据。
                context_manifest=context_record,
                prompt_version=prompt.version,
                prompt_published=not prompt_not_published,
                cost_usd=cost.usd,
                cost_basis=cost.basis,
            )
        )

        pending_id: str | None = None
        if prepared is not None and proposal is not None and proposal.requires_confirmation:
            pa = PendingActionRow(
                id=uuid.uuid4(),
                session_id=session_id,
                tenant_id=tenant_id,
                action_type=f"confirm_{prepared.object_ref}",
                payload={
                    "proposal_kind": proposal.kind,
                    "object_ref": prepared.object_ref,
                    "content": prepared.content,
                    "content_hash": prepared.content_hash,
                    "assistant_text": assistant_text,
                },
                status="PENDING",
                revision=row.revision,
                required_capability="research.session.write",
                object_ref=prepared.object_ref,
                source_message_id=assistant_id,
                created_at=_now(),
                expires_at=_now() + timedelta(hours=PENDING_TTL_HOURS),
            )
            db.add(pa)
            db.flush()
            pending_id = str(pa.id)
            db.add(
                PendingActionAuditRow(
                    pending_action_id=pa.id,
                    action="CREATED",
                    actor_id=actor_id,
                    trace_id=trace.trace_id,
                    at=_now(),
                    detail={"object_ref": prepared.object_ref, "kind": proposal.kind},
                )
            )

        row.revision += 1
        row.updated_at = _now()
        db.flush()

        ev = DomainEvent.create(
            event_type="session.assistant_message_created",
            session_id=session_id,
            revision=row.revision,
            payload={
                "seq": assistant_seq,
                "kind": (proposal.kind if proposal else "notice"),
                "model": model_name,
                "state": message_state,
                "pending_action_id": pending_id,
                # RV09：把"这一轮依据了哪个 prompt 版本、上下文清单是谁"
                # 写进事件流，使得从 SSE 上游就能追到上下文与成本。
                "prompt_ref": prompt.ref,
                "prompt_version": prompt.version,
                "prompt_published": not prompt_not_published,
                "context_manifest_id": str(context_record.get("manifestId") or ""),
                "context_truncated": bool((context_record.get("budget") or {}).get("truncated", False)),
                "cost_basis": cost.basis,
                "clarification_code": (clarification or {}).get("code", ""),
                # RV09：结构化提案是否走了"长输出"模型（对照用）。
                # 这一位让"换了模型之后成功率有没有变"可以从事后数据回答，
                # 而不必重新做一次 A/B。
                "model_escalated": model_escalated,
            },
            trace_id=trace.trace_id,
            actor_id=actor_id,
        )
        _emit(db, ev)
        row.last_event_id = ev.event_id
        if pending_id:
            _emit(
                db,
                DomainEvent.create(
                    event_type="session.pending_action_created",
                    session_id=session_id,
                    revision=row.revision,
                    payload={"pending_action_id": pending_id, "object_ref": prepared.object_ref if prepared else ""},
                    trace_id=trace.trace_id,
                    actor_id=actor_id,
                ),
            )
        svc._regenerate_summary(db, row, trace)
        db.flush()

        # RV09 · 短期记忆 checkpoint（会话 / thread 级）。
        #
        # 它记录"这条对话走到哪了"，**只**在同会话内可见（见 memory/short_term.py）。
        # 放在 tx2 里：assistant 消息与"进度到哪"必须一同提交，
        # 否则崩溃恢复后会出现"消息在、进度没跟上"的半状态。
        memory.save_checkpoint(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            thread_key=memory.DEFAULT_THREAD,
            last_message_seq=assistant_seq,
            state=_checkpoint_state(
                db,
                session_id=session_id,
                prepared=prepared,
                proposal=proposal,
                run_status=run_status,
                error_code=error_code,
                clarification=clarification,
            ),
        )

        snapshot = build_snapshot(
            db, row, message_state=message_state, message_reason=message_reason
        )
        from ..db.models import CommandRecordRow

        db.add(
            CommandRecordRow(
                client_request_id=crid,
                tenant_id=tenant_id,
                actor_id=actor_id,
                command_type=MESSAGE_COMMAND_TYPE,
                idempotency_fingerprint=server_digest,
                canonical_digest=server_digest,
                payload_ref=f"inline:{server_digest[:16]}",
                session_id=session_id,
                response_body={**snapshot, "_message_state": message_state, "_message_reason": message_reason},
                created_at=_now(),
            )
        )

    return snapshot, "EXECUTED", 201


def _checkpoint_state(
    db: Session,
    *,
    session_id: uuid.UUID,
    prepared: H.PreparedProposal | None,
    proposal: P.Proposal | None,
    run_status: str,
    error_code: str,
    clarification: dict | None,
) -> dict:
    """生成 checkpoint 状态（**白名单键**，见 `memory/short_term.STATE_KEYS`）。

    刻意只放"下一步要用的东西"，不放对话内容本身：
    checkpoint 若变成第二份会话记录，就会出现"两处不一致时信谁"的问题。
    """
    from ..db.models import ProblemMapRow, ResearchMapRow

    def _latest_rev(table) -> int | None:
        row = (
            db.query(table)
            .filter(table.session_id == session_id)
            .order_by(table.revision.desc())
            .first()
        )
        return int(row.revision) if row is not None else None

    if prepared is not None:
        stage = f"AWAITING_CONFIRMATION:{prepared.object_ref}"
        intent = f"propose:{prepared.kind}"
        open_questions: list[str] = []
    elif clarification is not None:
        stage = "AWAITING_USER_INPUT"
        intent = "clarify"
        open_questions = [
            str(g.get("section") or "") for g in (clarification.get("gaps") or []) if g.get("section")
        ]
    elif run_status == "SUCCEEDED":
        stage = "ACTIVE"
        intent = f"answer:{proposal.kind if proposal else 'unknown'}"
        open_questions = []
    else:
        stage = "DEGRADED"
        intent = "model_unavailable"
        open_questions = [error_code or "unknown_error"]

    return {
        "stage": stage,
        "lastIntent": intent,
        "openQuestions": open_questions,
        "lastProblemMapRevision": _latest_rev(ProblemMapRow),
        "lastResearchMapRevision": _latest_rev(ResearchMapRow),
        "notes": error_code if run_status != "SUCCEEDED" else "",
    }


def _recompute_capabilities(db: Session, session_id: uuid.UUID, state: str, reason: str) -> list[dict]:
    """重放时**保留原始**能力状态，只重算会话相关字段。"""
    row = db.get(ResearchSessionRow, session_id)
    if row is None:
        return []
    fresh = build_snapshot(db, row, message_state=state, message_reason=reason)
    return fresh["capabilities"]
