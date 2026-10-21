"""Proposal → 领域对象的落库路由（RV09–RV13）。

**唯一不变量**：模型输出永远不能直接写正式状态。
本模块只做两件事：
1. `describe()` —— 把一个 typed proposal 描述成 `PendingAction` 的内容（不写状态）；
2. `apply()` —— **只**在用户 confirm 之后调用，写入新的不可变修订行。

任何绕过 `apply()` 直接写领域对象的代码路径都是缺陷。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import models as M
from .proposals import Proposal

try:  # RV13 · paper 域错误（包级导入，避免在多个分支重复 import）
    from ..paper.errors import PaperSchemaError
except ImportError:  # pragma: no cover - paper 包缺失时的过渡态
    PaperSchemaError = Exception  # type: ignore[assignment,misc]


class ProposalRejected(Exception):
    """proposal 在**语义**层面被拒（schema 合法但违反领域规则）。

    RV12 追加 `detail`（可选、向后兼容）：拒绝必须带**机读上下文**
    （是哪条 claim、当前什么状态），否则用户只能猜。
    """

    def __init__(self, code: str, message: str, **detail: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


ORIGINS = ("USER", "SOURCE", "INFERENCE")
#: 由**服务端**（而非模型）赋予 `origin` 的 content 字段。
#: 这些字段的 origin 不由模型决定，因此不参与 `_ground_origins` 的裁决。
#: 目前只有 `_validate_intake` 里的 `problemStatement`（它由服务端从 intake 构造）。
_SERVER_AUTHORED_KEYS: frozenset[str] = frozenset({"problemStatement"})
CLAIM_STATUSES = ("SOLVED", "CONDITIONAL", "INCONCLUSIVE", "REFUTED")
CLAIM_SUPPORT = ("UNSUPPORTED", "PARTIAL", "SUPPORTED", "CONFLICTED")
REVIEW_CATEGORIES = (
    "EVIDENCE_GAP",
    "MATH_ERROR",
    "DATA_LEAKAGE",
    "REPRO_GAP",
    "CITATION_MISMATCH",
    "OVERCLAIM",
    "WRITING_ISSUE",
)
PAPER_BLOCK_TYPES = (
    "section",
    "heading",
    "paragraph",
    "equation",
    "figure",
    "table",
    "citation",
    "callout",
    "claim_ref",
    "evidence_ref",
    "review_mark",
)
MAP_NODE_TYPES = (
    "question",
    "hypothesis",
    "concept",
    "method",
    "source",
    "model",
    "experiment",
    "claim",
    "risk",
)
MAP_EDGE_TYPES = (
    "decomposes",
    "supports",
    "challenges",
    "requires",
    "derived_from",
    "tests",
    "contradicts",
)


def _canonical_hash(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text(value: Any, limit: int = 4000) -> str:
    return str(value if value is not None else "")[:limit]


# --------------------------------------------------------------------------
# 各对象族的 payload 语义校验 + 落库
# --------------------------------------------------------------------------


def _norm_originated(items: Any, *, field: str, default_origin: str = "INFERENCE") -> list[dict]:
    """把元素列表规范成 `{"text": ..., "origin": ...}`（`USER` 时另带 `quote`）。

    **关键安全行为**：缺失/非法 `origin` 一律降级为 `INFERENCE`（而不是 `USER`）。
    把推断冒充成用户原话，是本产品最不能接受的错误方向。

    `quote` 是 `USER` 的**逐字**锚定物（见 `evidence/origin_grounding.py`）：
    模型声明 `USER` 时必须能原样抄出用户消息里的一段，服务端据此判定归属。
    仅在 `origin == "USER"` 时保留 `quote` —— 挂在别的 origin 上没有语义。
    """
    out: list[dict] = []
    for raw in _as_list(items):
        if isinstance(raw, str):
            out.append({"text": raw, "origin": default_origin})
            continue
        if not isinstance(raw, dict):
            continue
        origin = str(raw.get("origin", "")).upper()
        if origin not in ORIGINS:
            origin = default_origin
        item = {
            "text": _text(raw.get("text") or raw.get("value") or raw.get("statement")),
            "origin": origin,
        }
        if origin == "USER":
            # `quote` 是 USER 的**逐字**锚定物（见 origin_grounding）：
            # 模型必须能原样抄出用户消息里的一段，服务端据此判定归属。
            quote = _text(raw.get("quote"), 2000).strip()
            if quote:
                item["quote"] = quote
        out.append(item)
    return out


def _validate_intake(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    if not payload or not payload.get("statement"):
        raise ProposalRejected("INTAKE_EMPTY", "intake 缺少 problemStatement")
    statement = _norm_originated(
        [{"text": payload.get("statement"), "origin": "USER"}], field="problemStatement"
    )
    # `problemStatement` 的 origin 由**服务端**赋（见 `_SERVER_AUTHORED_KEYS`），
    # 因此它免于 `_ground_origins` 的裁决：模型改写过的陈述不会因缺 quote 被误降级。
    return {
        "problemStatement": statement,
        "subproblems": _norm_originated(payload.get("subproblems"), field="subproblems"),
        "constraints": _norm_originated(payload.get("constraints"), field="constraints"),
        "variables": _norm_originated(payload.get("variables"), field="variables"),
        "knowns": _norm_originated(payload.get("knowns"), field="knowns"),
        "unknowns": _norm_originated(payload.get("unknowns"), field="unknowns"),
        "deliverables": _norm_originated(payload.get("deliverables"), field="deliverables"),
        "successCriteria": _norm_originated(payload.get("successCriteria"), field="successCriteria"),
    }


def _validate_research_map_nodes(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    """历史形状：模型自画的 typed nodes + typed edges。

    已被 RV09 的 `research_map`（服务端从 perspectives 推导图）取代 ——
    见 `domain/research_map.py` 里"为什么图由服务端推导"。
    """
    nodes_in = _as_list(payload.get("nodes"))
    edges_in = _as_list(payload.get("edges"))
    nodes: list[dict] = []
    ids: set[str] = set()
    for raw in nodes_in:
        if not isinstance(raw, dict):
            continue
        node_id = _text(raw.get("id"), 64) or f"n{len(nodes) + 1}"
        ntype = _text(raw.get("type"), 32)
        if ntype not in MAP_NODE_TYPES:
            raise ProposalRejected("MAP_NODE_TYPE_UNKNOWN", f"未知节点类型: {ntype!r}")
        origin = str(raw.get("origin", "")).upper()
        if origin not in ORIGINS:
            origin = "INFERENCE"
        nodes.append({"id": node_id, "type": ntype, "label": _text(raw.get("label"), 512), "origin": origin})
        ids.add(node_id)
    edges: list[dict] = []
    for raw in edges_in:
        if not isinstance(raw, dict):
            continue
        etype = _text(raw.get("type"), 32)
        if etype not in MAP_EDGE_TYPES:
            raise ProposalRejected("MAP_EDGE_TYPE_UNKNOWN", f"未知边类型: {etype!r}")
        src, dst = _text(raw.get("from"), 64), _text(raw.get("to"), 64)
        if src not in ids or dst not in ids:
            # 悬空边 = 图不完整。宁可拒绝，也不要让前端渲染出指向不存在节点的边。
            raise ProposalRejected(
                "MAP_DANGLING_EDGE", f"边 {src!r}->{dst!r} 指向不存在的节点"
            )
        edges.append({"from": src, "to": dst, "type": etype})
    if not nodes:
        raise ProposalRejected("MAP_EMPTY", "research map 至少需要一个节点")
    return {"nodes": nodes, "edges": edges}


def _validate_blueprint(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    route = _text(payload.get("route"), 256)
    if not route:
        raise ProposalRejected("BLUEPRINT_NO_ROUTE", "blueprint 必须选择一条 route")
    assumptions = payload.get("assumptions")
    bad = [a for a in _as_list(assumptions) if isinstance(a, dict) and a.get("unverified") and a.get("origin") != "INFERENCE"]
    if bad:
        raise ProposalRejected(
            "BLUEPRINT_UNVERIFIED_NOT_INFERENCE",
            "未经验证的假设必须标记 origin=INFERENCE，不得冒充 USER/SOURCE",
        )
    return {
        "problem": _text(payload.get("problem")),
        "objective": _text(payload.get("objective")),
        "route": route,
        "evidenceNodes": [_text(x, 128) for x in _as_list(payload.get("evidenceNodes"))],
        "assumptions": [_text(x, 512) if not isinstance(x, dict) else _text(x.get("text"), 512) for x in _as_list(assumptions)],
        "lineage": [_text(x, 256) for x in _as_list(payload.get("lineage"))],
    }


def _validate_problem_map(
    payload: dict, *, session_id: uuid.UUID, db: Session, user_text: str | None = None
) -> dict:
    """RV09 · ProblemMap：八段最小结构 + 缺口登记 + 自相矛盾登记。

    两步刻意分开（见 `problem_map.py` 的模块 docstring）：
    `normalize()` 只管结构（不合法 ⇒ 拒），`validate()` 只管语义核心
    （核心为空 ⇒ 拒，因为"没有成功判据的研究问题"不是研究问题）。

    注意：**信息不足不拒绝**。缺口写进 `insufficiencies` 交给用户补充，
    服务端绝不替用户编一个"成功标准"。

    `user_text`（2026-09-14 由 E2E 发现并修复）：矛盾检测**必须也扫用户原话**。
    原先只扫模型写出的 `researchQuestion` —— 而模型完全可能把用户那句
    "这个数列收敛。这个数列不收敛。"**改写**成一句自洽的问题，
    于是用户输入里的自相矛盾被"洗掉"，检测形同虚设。
    """
    from . import problem_map as PM

    try:
        normalized = PM.normalize(payload or {})
        PM.validate(normalized.content)
    except PM.ProblemMapRejected as e:
        # 领域异常必须转成 `ProposalRejected`：它带的是**可对用户解释**的拒绝码，
        # 而 `ProblemMapRejected` 冒到 API 层就变成一个没有信息的 500。
        raise ProposalRejected(e.code, e.message) from e

    # 用户原话里的机械可判矛盾：与模型改写无关，独立检测。
    if user_text:
        seen = {(c["kind"], c["a"], c["b"]) for c in normalized.contradictions}
        for c in PM.detect_contradiction(user_text):
            key = (c.kind, c.a, c.b)
            if key not in seen:
                seen.add(key)
                normalized.contradictions.append({**c.as_dict(), "detectedIn": "USER_MESSAGE"})
        if normalized.contradictions:
            normalized.content["contradictions"] = normalized.contradictions

    # 自相矛盾时**拒绝落库**：一个自相矛盾的问题地图一旦成为正式状态，
    # 后续所有轮次都会在矛盾的地基上叠加。正确行为是请用户澄清。
    if normalized.contradictions:
        detail = "；".join(
            f"{c.get('kind')}: {c.get('a')} vs {c.get('b')}" for c in normalized.contradictions[:3]
        )
        raise ProposalRejected(
            "PROBLEM_MAP_CONTRADICTORY",
            f"问题地图内部自相矛盾（{detail}）。请先澄清再落正式状态。",
        )
    return normalized.content


def _validate_research_map(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    """RV09 · ResearchMap：2–4 个可区分视角。

    **依赖门**：研究地图必须先有一个已确认的 ProblemMap。
    理由不是流程洁癖：视角的取舍标准来自问题地图的 `successCriteria` /
    `falsificationCriteria`。没有地图就去排视角，等于在不知道要回答什么的情况下
    决定怎么回答。
    """
    from . import research_map as RM

    has_problem_map = (
        db.query(M.ProblemMapRow).filter(M.ProblemMapRow.session_id == session_id).count() > 0
    )
    if not has_problem_map:
        raise ProposalRejected(
            "RESEARCH_MAP_REQUIRES_PROBLEM_MAP",
            "本会话还没有已确认的问题地图：请先确认 ProblemMap，再讨论调研/验证视角",
        )
    try:
        return RM.normalize(payload or {}).content
    except RM.ResearchMapRejected as e:
        raise ProposalRejected(e.code, e.message) from e


def _validate_legacy_map(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    """历史 `map` 形状（typed nodes + edges）。仍受校验，但被标记沿用。

    保留的原因：`0005` 迁移之后，历史会话里可能已经有 `map` 的 PendingAction。
    把它们判成"未知 kind"会让用户看到莫名其妙的失败。因此它继续可用，
    但产物里带 `schemaVariant="legacy-nodes"` 与 `deprecated=true`，
    前端据此提示"这是旧版地图，建议改用多视角地图"。
    """
    content = _validate_research_map_nodes(payload, session_id=session_id, db=db)
    content["schemaVariant"] = "legacy-nodes"
    content["deprecated"] = True
    content["deprecationNote"] = "RV09 起由 research_map（多视角）取代；此形状仅作历史兼容"
    return content


def _validate_route_arena(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    """RV10 · RouteArena：2–3 条实质不同的建模路线 + 服务端 rubric。

    三道门（缺一不可，详见 `route_arena.py`）：
    1. **结构完备门**（纯函数）：每条路线必须有方法/假设/变量/指标/证伪/
       可识别性/资源——缺失的路线被标 REJECTED 并保留原因；
    2. **证据门**（DB）：evidenceRefs 必须可解析——引用假证据的路线被拒；
    3. **竞技场门**：有效（CANDIDATE）路线 ≥2 且 kind ≥2 且 label 可区分。

    裁决词封禁在 normalize 里：winner / totalScore / ranking 出现即整轮拒。
    """
    from ..evidence.resolvable import resolvable_evidence_ids
    from . import route_arena as RA

    try:
        out = RA.normalize(payload or {})
    except RA.RouteArenaRejected as e:
        raise ProposalRejected(e.code, e.message) from e

    content = out.content
    resolved = resolvable_evidence_ids(db, session_id=session_id)
    RA.apply_evidence_gate(content, resolved)
    # 证据门可能把有效路线打到 2 条以下 —— 必须重过竞技场门。
    try:
        RA._check_arena_gates(content.get("routes") or [])
    except RA.RouteArenaRejected as e:
        raise ProposalRejected(e.code, e.message) from e
    return RA.finalize(content, resolved_ids=resolved)


def _validate_research_blueprint(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    """RV10 · ResearchBlueprint：服务端绑定 + 选定路线 + 计划/门/预算。

    绑定由服务端解析（最新已确认的 ProblemMap / ResearchMap / RouteArena），
    模型只提供 routeId"选择"。被拒路线不能被选定。
    """
    from . import research_blueprint as RB

    try:
        out = RB.normalize(payload or {})
        content = RB.bind_context(db, content=out.content, session_id=session_id)
    except RB.BlueprintRejected as e:
        raise ProposalRejected(e.code, e.message) from e
    return content.content


def _validate_experiment_plan(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    """RV11 扩展：计划必须携带**可执行材料**——inline 代码 + 具名输入 + seed。

    代码随提案走 schema 门与人工确认门：确认后的 plan 行是不可变的执行材料
    （dispatch 记录其 sha256，worker 执行前复验完整性）。
    """
    if not payload.get("dataset_ref") and not payload.get("datasetRef"):
        raise ProposalRejected("PLAN_NO_DATASET", "实验计划必须声明 dataset ref")
    code = payload.get("code")
    if code is not None:
        if not isinstance(code, str) or not code.strip():
            raise ProposalRejected("PLAN_CODE_INVALID", "code 必须是非空字符串")
        if len(code) > 200_000:
            raise ProposalRejected("PLAN_CODE_TOO_LARGE", "code 超过 200KB 上限——请拆分实验")
    raw_inputs = payload.get("inputs")
    inputs: list[dict] = []
    if raw_inputs is not None:
        if not isinstance(raw_inputs, list):
            raise ProposalRejected("PLAN_INPUTS_INVALID", "inputs 必须是数组")
        for item in raw_inputs:
            if not isinstance(item, dict) or not item.get("name"):
                raise ProposalRejected("PLAN_INPUTS_INVALID", "inputs 元素必须有 name 与 text")
            inputs.append({"name": _text(item.get("name"), 128), "text": str(item.get("text") or "")})
    seed = payload.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, (int, float)) or seed < 0):
        raise ProposalRejected("PLAN_SEED_INVALID", "seed 必须是非负整数")
    return {
        "objective": _text(payload.get("objective")),
        "code_ref": _text(payload.get("code_ref") or payload.get("codeRef"), 512),
        "code": code,
        "dataset_ref": _text(payload.get("dataset_ref") or payload.get("datasetRef"), 512),
        "environment": payload.get("environment") if isinstance(payload.get("environment"), dict) else {},
        "params": payload.get("params") if isinstance(payload.get("params"), dict) else {},
        "metrics": [_text(x, 128) for x in _as_list(payload.get("metrics"))],
        "seed": int(seed) if isinstance(seed, (int, float)) and not isinstance(seed, bool) else None,
        "inputs": inputs,
        "stages": [s for s in _as_list(payload.get("stages")) if isinstance(s, dict)],
    }


def _validate_claim(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    """claim 提案的第一道门（**只做"能不能提"，不做"成不成立"**）。

    RV12 起：模型给的 `status` / `supportQuality` **不再是权威状态**，
    只作为 `proposedStatus` / `proposedSupportQuality` 记进审计；
    真正的状态由 `claim/state_machine.derive_status` 在**确认时**用真实
    证据 / 实验 / 验证记录算出（见 `apply()` 的 claim 分支）。

    这里保留的硬门是"提案层面的谎言"：
    - 引用不存在/未授权/已失效的证据 ⇒ 拒（否则 SOLVED 可以凭空成立）；
    - 自称 SOLVED 却一条证据都没有 ⇒ 拒（这不是可讨论的降级，是明显的越界）。
    """
    proposed_status = str(payload.get("status", "INCONCLUSIVE")).upper()
    if proposed_status not in CLAIM_STATUSES:
        raise ProposalRejected("CLAIM_STATUS_UNKNOWN", f"未知 claim 状态: {proposed_status!r}")
    quality = str(payload.get("supportQuality") or payload.get("support_quality") or "UNSUPPORTED").upper()
    if quality not in CLAIM_SUPPORT:
        raise ProposalRejected("CLAIM_SUPPORT_UNKNOWN", f"未知支持质量: {quality!r}")

    evidence_refs = [_text(x, 64) for x in _as_list(payload.get("evidenceRefs") or payload.get("evidence_refs"))]
    # 证据引用必须真实存在**且未失效且已授权**。
    # 判据已收敛到 `evidence/resolvable.py`（权威表 ∪ 遗留表，D-TABLE-1）——
    # 只看"行存在"会把授权位变成装饰品，故并集里**只收**已授权且未失效的遗留行。
    if evidence_refs:
        from ..evidence.resolvable import resolvable_evidence_ids

        known = resolvable_evidence_ids(db, session_id=session_id)
        missing = [e for e in evidence_refs if e not in known]
        if missing:
            raise ProposalRejected(
                "CLAIM_EVIDENCE_UNRESOLVED",
                f"引用了不存在、未授权或已失效的证据: {missing}",
            )
    if proposed_status == "SOLVED" and not evidence_refs:
        raise ProposalRejected(
            "CLAIM_SOLVED_WITHOUT_EVIDENCE", "SOLVED 必须有证据支撑；无证据只能 INCONCLUSIVE"
        )
    return {
        "text": _text(payload.get("text") or payload.get("statement")),
        #: **不是**权威状态：只进审计（`aiProposedStatus`）。
        "proposedStatus": proposed_status,
        "proposedSupportQuality": quality,
        "relation": _text(payload.get("relation") or "unknown", 32),
        "evidenceRefs": evidence_refs,
        "experimentRefs": [_text(x, 64) for x in _as_list(payload.get("experimentRefs"))],
        "scope": payload.get("scope") if isinstance(payload.get("scope"), dict) else {},
        "assumptions": [a for a in _as_list(payload.get("assumptions")) if isinstance(a, dict)],
        "limitations": [_text(x, 256) for x in _as_list(payload.get("limitations"))],
        "claimKind": _text(payload.get("claimKind") or payload.get("claim_kind") or "PARAPHRASE", 32),
    }


def _uuid_or_none(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _resolve_paper_refs(blocks: list[dict], *, session_id: uuid.UUID, db: Session) -> None:
    """论文块的**引用门**：`claim_ref` / `evidence_ref` / `citation` 必须真实可解析。

    为什么必须做（否则上游所有约束都能被一条引用绕过）：
    - 论文是 claim 的下游消费者。若 `claim_ref` 可以引用**未发布**的结论，
      "人工确认才发布关键 claim"就形同虚设 —— 论文一交，门就没了；
    - `evidence_ref` / `citation` 不解析 = 凭空引用（RV07 的教训：引用精度必须是
      可回源的正确率，而不是"格式无损率"）。
    """
    from ..db.models import CitationRow, ResearchSessionRow
    from ..evidence.resolvable import resolvable_evidence_ids

    session = db.get(ResearchSessionRow, session_id)
    tenant_id = session.tenant_id if session is not None else None

    known_evidence: set[str] | None = None
    for block in blocks:
        refs = list(block.get("refs") or [])
        if not refs:
            continue
        btype = block["type"]

        if btype == "claim_ref":
            from ..claim import ledger as claim_ledger

            for ref in refs:
                key = _uuid_or_none(ref)
                # API 里的 claim id 是 **claim_key**（跨修订稳定身份），不是修订行 id；
                # 必须经 ledger 解析到**最新**修订（发布标记只存在于最新行）。
                row = claim_ledger.get_claim_row(db, key) if key is not None else None
                if row is None or str(row.session_id) != str(session_id):
                    raise ProposalRejected(
                        "PAPER_CLAIM_REF_UNRESOLVED",
                        f"claim_ref 指向不存在或跨会话的 claim: {ref}",
                        claimId=ref,
                    )
                if row.published_at is None or bool(row.requires_review):
                    raise ProposalRejected(
                        "PAPER_CLAIM_REF_UNPUBLISHED",
                        "claim_ref 只能引用**已发布**的 claim（人工确认才发布关键结论）",
                        claimId=ref,
                        status=row.status,
                        requiresReview=bool(row.requires_review),
                    )
                # 状态快照：论文块必须能回答"这个结论凭什么成立"。
                # statusLabel 从**唯一真源**（claim.types.STATUS_LABELS）取，
                # 不依赖落库 content 里是否碰巧带了它。
                from ..claim.types import STATUS_LABELS

                content = dict(row.content or {})
                block["claimSnapshot"] = {
                    "claimId": str(row.claim_key or row.id),
                    "status": str(row.status),
                    "statusLabel": STATUS_LABELS.get(str(row.status), str(row.status)),
                    "supportBand": str(row.support_band or ""),
                    "supportQuality": str(row.support_quality or ""),
                    "gateId": str(content.get("gateId") or ""),
                    "published": True,
                    "publishedAt": row.published_at.isoformat() if row.published_at else "",
                }
        elif btype == "evidence_ref":
            if known_evidence is None:
                known_evidence = resolvable_evidence_ids(db, session_id=session_id)
            missing = [r for r in refs if r not in known_evidence]
            if missing:
                raise ProposalRejected(
                    "PAPER_EVIDENCE_REF_UNRESOLVED",
                    f"evidence_ref 指向不存在、未授权或已失效的证据: {missing}",
                    evidenceIds=missing,
                )
        elif btype == "citation":
            for ref in refs:
                row = db.get(CitationRow, key) if (key := _uuid_or_none(ref)) else None
                if (
                    row is None
                    or (tenant_id is not None and str(row.tenant_id) != str(tenant_id))
                    or (row.session_id is not None and str(row.session_id) != str(session_id))
                ):
                    raise ProposalRejected(
                        "PAPER_CITATION_UNRESOLVED",
                        f"citation 指向不存在或越界的引用: {ref}",
                        citationId=ref,
                    )


def _validate_paper_patch(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    """RV13 · paper_patch 校验：支持 **typed ops**（新）与 legacy blocks（旧）两形状。

    ops 形状走 `paper.patch.validate_ops` 四道门（schema / evidence coverage /
    output safety / 状态锁），**提案时与确认时各跑一次** —— 提案时的拒绝
    不产生 PendingAction；确认时复检防止"提案之后事实变了"。
    """
    ops = [o for o in _as_list(payload.get("ops")) if isinstance(o, dict)]
    blocks = [b for b in _as_list(payload.get("blocks")) if isinstance(b, dict)]
    base_revision = int(payload.get("baseRevision") or 0)
    rationale = _text(payload.get("rationale"))
    evidence_refs = [_text(x, 64) for x in _as_list(payload.get("evidenceRefs"))]

    if ops:
        from ..db.models import PaperSpecRow
        from ..paper.patch import validate_ops
        from ..paper.service import paper_resolvers

        latest = (
            db.query(PaperSpecRow)
            .filter(PaperSpecRow.session_id == session_id)
            .order_by(PaperSpecRow.revision.desc())
            .first()
        )
        current_blocks = (
            [dict(b) for b in ((latest.content or {}).get("blocks") or [])]
            if latest is not None
            else []
        )
        r = paper_resolvers(db, session_id=session_id, tenant_id=_session_tenant(db, session_id))
        for o in ops:
            o.pop("_enrich", None)
        try:
            validated = validate_ops(
                ops,
                current_blocks=current_blocks,
                resolvable_evidence=r["resolvable_evidence"],
                resolvable_claims=r["resolvable_claims"],
                claim_status=r["claim_status"],
                resolvable_citations=r["resolvable_citations"],
                resolvable_anchors=r["resolvable_anchors"],
                citation_hashes=r["citation_hashes"],
                anchor_details=r["anchor_details"],
                is_ai=True,
            )
        except PaperSchemaError as e:
            raise ProposalRejected(e.code, e.message) from e
        return {
            "baseRevision": base_revision,
            "rationale": rationale,
            "evidenceRefs": evidence_refs,
            "ops": validated,
        }

    if not blocks:
        raise ProposalRejected("PAPER_NO_BLOCKS", "paper patch 必须包含至少一个 block")
    out: list[dict] = []
    for b in blocks:
        btype = _text(b.get("type"), 32)
        if btype not in PAPER_BLOCK_TYPES:
            raise ProposalRejected("PAPER_BLOCK_TYPE_UNKNOWN", f"未知 block 类型: {btype!r}")
        out.append(
            {
                "type": btype,
                "id": _text(b.get("id"), 64) or f"b{len(out) + 1}",
                "text": _text(b.get("text")),
                "refs": [_text(x, 128) for x in _as_list(b.get("refs"))],
            }
        )
    _resolve_paper_refs(out, session_id=session_id, db=db)
    return {
        "baseRevision": base_revision,
        "rationale": rationale,
        "evidenceRefs": evidence_refs,
        "blocks": out,
    }


def _session_tenant(db: Session, session_id: uuid.UUID) -> uuid.UUID:
    from ..db.models import ResearchSessionRow

    row = db.get(ResearchSessionRow, session_id)
    return row.tenant_id if row is not None else uuid.uuid4()


def _validate_review(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    findings = [f for f in _as_list(payload.get("findings")) if isinstance(f, dict)]
    if not findings:
        raise ProposalRejected("REVIEW_NO_FINDINGS", "review 必须包含至少一条 finding")
    out: list[dict] = []
    for f in findings:
        cat = str(f.get("category", "")).upper()
        if cat not in REVIEW_CATEGORIES:
            raise ProposalRejected("REVIEW_CATEGORY_UNKNOWN", f"未知 finding 类别: {cat!r}")
        out.append(
            {
                "category": cat,
                "severity": _text(f.get("severity") or "minor", 16),
                "location": _text(f.get("location"), 512),
                "suggestion": _text(f.get("suggestion")),
                "relatedClaimId": _text(f.get("relatedClaimId"), 64),
                "relatedEvidenceRefs": [_text(x, 64) for x in _as_list(f.get("relatedEvidenceRefs"))],
            }
        )
    return {"findings": out}


def _validate_review_finding(payload: dict, *, session_id: uuid.UUID, db: Session) -> dict:
    """RV15 · Review Finding v2 校验（Reviewer AI 提案门）。

    委托给 `core/review/taxonomy.validate_finding_proposal`——校验判据只有一处；
    这里只做形状收敛与 ProposalRejected 适配。
    """
    from ..review.taxonomy import ReviewDomainError, validate_finding_proposal

    findings = [f for f in _as_list(payload.get("findings")) if isinstance(f, dict)]
    if not findings:
        raise ProposalRejected("REVIEW_NO_FINDINGS", "review 提案必须包含至少一条 finding")
    if len(findings) > 50:
        raise ProposalRejected("REVIEW_TOO_MANY_FINDINGS", "单轮提案不得超过 50 条 finding")
    out: list[dict] = []
    for raw in findings:
        item = dict(raw)
        item.pop("createdByRun", None)
        item.pop("created_by_run", None)
        try:
            out.append(validate_finding_proposal(item))
        except ReviewDomainError as e:
            raise ProposalRejected(e.code, e.message, **getattr(e, "detail", {}) or {}) from e
    return {
        "findings": out,
        "createdByRun": _text(payload.get("createdByRun"), 128),
    }


#: `kind` → (object_ref, 语义校验器)
VALIDATORS: dict[str, tuple[str, Callable[..., dict]]] = {
    "intake": ("intake", _validate_intake),
    # RV09 正式形状
    "problem_map": ("problem_map", _validate_problem_map),
    "research_map": ("research_map", _validate_research_map),
    # RV10：路线竞技场 + 研究蓝图（object_ref=blueprint 复用 research_blueprint 表）
    "route_arena": ("route_arena", _validate_route_arena),
    "research_blueprint": ("blueprint", _validate_research_blueprint),
    # 历史兼容（保留但不引导；`map` 的产物会带 deprecated 标记）
    "map": ("research_map", _validate_legacy_map),
    "blueprint": ("blueprint", _validate_blueprint),
    "experiment_plan": ("experiment_plan", _validate_experiment_plan),
    "claim": ("claim", _validate_claim),
    "paper_patch": ("paper_spec", _validate_paper_patch),
    "review": ("review", _validate_review),
    # RV15 · Review Finding v2（Reviewer AI 提案入口；校验见 core/review/taxonomy）
    "review_finding": ("review_finding", _validate_review_finding),
}

#: 需要 PendingAction 确认门的 kind。`answer` **不**改正式状态，因此不需要。
CONFIRM_REQUIRED_KINDS = frozenset(VALIDATORS)


@dataclass
class PreparedProposal:
    kind: str
    object_ref: str
    content: dict
    content_hash: str


def _ground_origins(
    db: Session,
    content: dict,
    *,
    session_id: uuid.UUID,
    proposal: Proposal,
    user_text: str | None = None,
) -> dict:
    """把元素级 `origin` 从"模型自述"改成"服务端裁决"。

    `origin="SOURCE"` 必须由**提案级** `evidence_refs` 指向的、且当前仍为
    `ACTIVE` 且非 stale 的证据卡背书；否则降级 `INFERENCE`。
    `origin="USER"` 必须附**逐字** `quote`（用户消息片段），否则降级。
    **只降级不升级** —— 模型无法再靠自述拿到"来源"/"用户"徽标。
    `user_text` 是本轮用户消息原文，用于裁决模型声明的 `USER`；
    `_SERVER_AUTHORED_KEYS` 命中的字段（服务端构造）整段跳过裁决。

    实现见 `core/evidence/origin_grounding.py`（2026-09-14 人工授权接线）。
    """
    # 延迟 import：`evidence/service.py` 顶层依赖 `domain/errors.py`，
    # 顶层反向 import 会成环（仓库既有惯例同此）。
    from ..evidence.origin_grounding import ground_content_origins
    from ..evidence.service import grounded_card_ids_for_session

    grounded = grounded_card_ids_for_session(db, session_id=session_id)
    grounded_content, _report = ground_content_origins(
        content,
        grounded_ids=grounded,
        user_text=user_text,
        proposal_refs=list(proposal.evidence_refs or []),
        server_authored_keys=_SERVER_AUTHORED_KEYS,
        # `deep=True`（RV09 起）：`research_map` 的交付物/不确定性/停止条件
        # 位于 `perspectives[]` 的下一层，只裁决顶层会让模型自述的 `SOURCE`
        # 在经过服务端推导的图节点里原样存活。
        deep=True,
    )
    return grounded_content


def prepare(
    db: Session,
    proposal: Proposal,
    *,
    session_id: uuid.UUID,
    actor_id: uuid.UUID,
    user_text: str | None = None,
) -> PreparedProposal | None:
    """校验并规范化 proposal；`answer` 返回 None（不落正式状态）。

    `user_text` 是本轮用户消息原文，用于裁决模型声明的 `USER`
    （见 `_ground_origins` 与 `evidence/origin_grounding.py`）。
    """
    entry = VALIDATORS.get(proposal.kind)
    if entry is None:
        return None
    object_ref, validator = entry
    # 只有需要"用户原话"的校验器才收 `user_text`（逐字实现，不做万能 **kwargs，
    # 那样会把拼错参数变成静默 no-op）。
    validator_kwargs: dict[str, Any] = {"session_id": session_id, "db": db}
    if proposal.kind == "problem_map":
        validator_kwargs["user_text"] = user_text
    content = validator(proposal.payload or {}, **validator_kwargs)
    # 先裁决 origin，再统计分布 —— 顺序不能反，否则 `origins` 会统计未裁决的值。
    content = _ground_origins(
        db, content, session_id=session_id, proposal=proposal, user_text=user_text
    )
    if proposal.kind == "research_map":
        # `origin` 裁决会改动 perspectives 里的元素；推导出的图必须跟着重建，
        # 否则被降级的交付物仍会以"来源"徽标出现在图节点上。
        from . import research_map as RM

        content = RM.refresh_graph(content)
    content = {**content, "origins": _origin_summary(content)}
    return PreparedProposal(
        kind=proposal.kind,
        object_ref=object_ref,
        content=content,
        content_hash=_canonical_hash(content),
    )


def _origin_summary(content: dict) -> dict:
    """统计每个 origin 的元素数。前端据此标注"哪些是推断"。

    RV09 起**递归**统计：`research_map` 的元素在 `perspectives[]` 的下一层，
    只数顶层会让"这份地图有 0 条推断"成为一句假话 —— 而它恰好是
    用户判断可信度最依赖的那个数字。
    """
    counts = dict.fromkeys(ORIGINS, 0)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            origin = value.get("origin")
            if isinstance(origin, str) and origin in counts:
                counts[origin] += 1
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for v in content.values():
        walk(v)
    return counts


# --------------------------------------------------------------------------
# confirm 之后的落库（**唯一**写正式状态的入口）
# --------------------------------------------------------------------------

_TABLE_BY_OBJECT_REF: dict[str, Any] = {
    "problem_map": M.ProblemMapRow,
    "research_map": M.ResearchMapRow,
    "route_arena": M.RouteArenaRow,
    "blueprint": M.ResearchBlueprintRow,
    "experiment_plan": M.ExperimentPlanRow,
    "paper_spec": M.PaperSpecRow,
}


def _next_revision(db: Session, table: Any, session_id: uuid.UUID) -> int:
    current = db.scalar(
        select(func.max(table.revision)).where(table.session_id == session_id)
    )
    return int(current or 0) + 1


def apply(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    object_ref: str,
    content: dict,
    client_request_id: str,
    trace_id: str,
    supersedes_revision: int | None = None,
) -> dict:
    """把**已确认**的内容写成新的修订行。返回 `{object_ref, id, revision}`。"""
    now = datetime.now(timezone.utc)

    # ---- RV15 · Review Finding v2：指纹去重路由（不复制相同 finding） ----
    if object_ref == "review_finding":
        from ..review import service as review_service

        return review_service.apply_confirmed_findings(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            content=content,
            trace_id=trace_id,
        )

    # ---- 非修订链对象：ReviewFinding（legacy，一"条"而非一"版"） ----
    if object_ref == "review":
        findings = [f for f in _as_list(content.get("findings")) if isinstance(f, dict)]
        created: list[str] = []
        for f in findings:
            row_id = uuid.uuid4()
            db.add(
                M.ReviewFindingRow(
                    id=row_id,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    paper_spec_id=None,
                    category=str(f.get("category", "WRITING_ISSUE")),
                    severity=str(f.get("severity", "minor"))[:16],
                    location=str(f.get("location", ""))[:512],
                    related_claim_id=None,
                    related_evidence_refs=[str(x) for x in (f.get("relatedEvidenceRefs") or [])],
                    suggestion=str(f.get("suggestion", ""))[:4000],
                    status="OPEN",
                    resolution_evidence_refs=[],
                    trace_id=trace_id,
                    created_at=now,
                )
            )
            created.append(str(row_id))
        db.flush()
        return {
            "object_ref": "review",
            "id": created[0],
            "ids": created,
            "count": len(created),
            # 非修订链对象：用条数作为"修订号"，让确认响应形状与其它对象一致
            "revision": len(created),
        }

    table = _TABLE_BY_OBJECT_REF.get(object_ref)
    claim_decision = None
    claim_key_for_audit: uuid.UUID | None = None

    if object_ref == "intake":
        table = M.ResearchIntakeRow
    elif object_ref == "claim":
        table = M.ClaimRow
    elif object_ref == "problem_map":
        table = M.ProblemMapRow
    if object_ref == "paper_spec":
        # RV13 · typed ops patch：确认时用**当前事实**复检四道门后落新修订；
        # legacy blocks：同样过 canonical 化（公式 canonical/快照回填）。
        from ..paper import service as paper_service

        if content.get("ops"):
            # RV13.1 · 人工逐项处置（:revise 写入的 review）先合并进 ops：
            # 被拒绝的 op 不落库、edits 覆盖文本/公式/理由。合并本身在 revise 时
            # 已做过结构检查，这里信任内容并让四道门做**事实级**复检。
            review = content.get("review")
            if review:
                from ..paper.patch import apply_review

                try:
                    effective_ops = apply_review(
                        [dict(o) for o in content.get("ops") or []], review
                    )
                except PaperSchemaError as e:
                    raise ProposalRejected(e.code, e.message) from e
                if not effective_ops:
                    raise ProposalRejected(
                        "PAPER_PATCH_EMPTY_AFTER_REVIEW",
                        "逐项拒绝后 patch 已无剩余操作；请拒绝整条提案",
                    )
                content = {**content, "ops": effective_ops}
            paper_result = paper_service.apply_confirmed_patch(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                trace_id=trace_id,
                content=content,
            )
            db.flush()
            return {
                "object_ref": object_ref,
                "id": paper_result["paperId"],
                "revision": paper_result["revision"],
            }
        try:
            # legacy blocks 已在提案期通过 `_validate_paper_patch` 校验并清洗，
            # 且可能携带提案期回填的 claimSnapshot —— 此处**不再**严格重校验，
            # 只做 canonical 化（公式 canonical / 引用快照回填）。
            enriched = paper_service.enrich_blocks(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                blocks=[dict(b) for b in (content.get("blocks") or []) if isinstance(b, dict)],
                is_ai=False,
            )
            content = {**content, "blocks": enriched}
        except PaperSchemaError as e:
            raise ProposalRejected(e.code, e.message) from e

    if table is None:
        raise ProposalRejected("OBJECT_REF_UNKNOWN", f"未知 object_ref: {object_ref!r}")

    revision = _next_revision(db, table, session_id)
    row_id = uuid.uuid4()
    common = {
        "id": row_id,
        "session_id": session_id,
        "tenant_id": tenant_id,
        "revision": revision,
        "content": content,
        "content_hash": _canonical_hash(content),
        "client_request_id": client_request_id,
        "confirmed_at": now,
        "confirmed_by": actor_id,
        "trace_id": trace_id,
        "created_at": now,
    }

    if object_ref == "claim":
        # RV12：**状态由服务端 Gate 推导**，模型自报的 proposedStatus 只进审计。
        # 这是"AI 只 propose claim/status"在代码上的落点：确认（apply）时才算状态。
        from ..claim import ledger as claim_ledger
        from ..claim.state_machine import derive_status

        claim_key = uuid.uuid4()
        evidence_refs = [str(x) for x in (content.get("evidenceRefs") or [])]
        experiment_refs = [str(x) for x in (content.get("experimentRefs") or [])]
        snapshot = claim_ledger.snapshot_from_refs(
            db,
            tenant_id=tenant_id,
            session_id=session_id,
            claim_kind=str(content.get("claimKind") or "PARAPHRASE"),
            evidence_refs=evidence_refs,
            experiment_refs=experiment_refs,
        )
        decision = derive_status(snapshot)
        claim_decision = decision
        claim_key_for_audit = claim_key
        acl = {
            "tenant_id": str(tenant_id),
            "session_id": str(session_id),
            "project_id": "",
            "owner_id": str(actor_id),
            "visibility": "session",
            "allowed_roles": [],
        }
        claim_content = {
            "statement": str(content.get("text") or ""),
            "scope": content.get("scope") or {},
            "assumptions": content.get("assumptions") or [],
            "claimKind": str(content.get("claimKind") or "PARAPHRASE"),
            "limitations": list(content.get("limitations") or []),
            "evidenceLinks": [dict(l) for l in snapshot.evidence_links],
            "experimentLinks": [dict(x) for x in snapshot.experiment_links],
            "counterEvidence": [dict(c) for c in snapshot.counter_evidence],
            "confidenceBasis": {
                "method": "rubric-v1",
                "band": snapshot.support_band,
                "score": 0.0,
                "factors": {},
                "computedAt": now.isoformat(),
                "notes": "由服务端 Gate 在确认时计算。",
            },
            "audit": {
                "origin": "AI_PROPOSAL",
                "aiProposedStatus": str(content.get("proposedStatus") or "INCONCLUSIVE"),
                "aiProposedSupportQuality": str(content.get("proposedSupportQuality") or "UNSUPPORTED"),
                "materializedAt": now.isoformat(),
                "method": "server-gate",
            },
            "status": decision.status,
            "supportQuality": snapshot.quality,
            "requiresReview": bool(decision.requires_review),
            "reviewReasons": list(decision.review_reasons),
            "gateId": decision.gate_id,
            "satisfied": list(decision.satisfied),
            "missing": list(decision.missing),
            "conditions": list(decision.conditions),
            "refutation": list(decision.refutation),
            "claimKey": str(claim_key),
        }
        common = {**common, "content": claim_content, "content_hash": _canonical_hash(claim_content)}
        row = M.ClaimRow(
            **common,
            text=str(content.get("text", "")),
            status=decision.status,
            support_quality=snapshot.quality,
            relation=str(content.get("relation", "unknown")),
            evidence_refs=evidence_refs,
            experiment_refs=experiment_refs,
            claim_key=claim_key,
            owner_id=actor_id,
            acl=acl,
            claim_kind=str(content.get("claimKind") or "PARAPHRASE"),
            support_band=snapshot.support_band,
            requires_review=bool(decision.requires_review),
            review_reasons=list(decision.review_reasons),
        )
    elif object_ref == "blueprint":
        row = M.ResearchBlueprintRow(**common)
    elif object_ref == "model_spec":
        row = M.ModelSpecRow(
            **common,
            label=str(content.get("label", "")),
            constraints=list(content.get("constraints", []) or []),
            evidence_coverage=list(content.get("evidenceCoverage", []) or []),
            gaps=list(content.get("gaps", []) or []),
        )
    else:
        row = table(**common)

    db.add(row)
    db.flush()

    if object_ref == "claim" and claim_decision is not None:
        # 状态迁移审计：消息路径确认同样必须留下"为什么是这个状态"。
        db.add(
            M.ClaimTransitionRow(
                claim_key=claim_key_for_audit or row.id,
                claim_id=row.id,
                session_id=session_id,
                tenant_id=tenant_id,
                revision=revision,
                from_status="",
                to_status=str(row.status),
                gate_id=str(claim_decision.gate_id),
                satisfied=list(claim_decision.satisfied),
                missing=list(claim_decision.missing),
                conditions=list(claim_decision.conditions),
                refutation=list(claim_decision.refutation),
                review_reasons=list(claim_decision.review_reasons),
                requires_review=bool(row.requires_review),
                actor_kind="HUMAN",
                actor_id=actor_id,
                action="message-proposal.accept",
                published=False,
                trace_id=trace_id,
                created_at=now,
            )
        )
        db.flush()

    return {"object_ref": object_ref, "id": str(row_id), "revision": revision}
