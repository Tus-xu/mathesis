"""RV13 · AI typed patch ops 与安全门。

任务书第 5/6 条的落点：
- AI 只返回 **typed patch proposal**：每个 op 指定 `op / target / expectedNodeRev /
  node / rationale / evidenceRefs`；
- 应用前必须过四道门：**schema**（节点合法）、**evidence coverage**（引用可解析）、
  **output safety**（强结论必须有 claim 链接；INCONCLUSIVE/REFUTED 节点锁文本）、
  **人工确认**（本模块只做前三道；写库只发生在人工 accept 之后）。

`claimSnapshot` / `citationSnapshot` / `lineage` 一律由服务端在 validate 阶段
用真源解析并放进 op 的 `_enrich`，`apply_ops` 只做纯结构变换 ——
**AI 永远不能自带 server-owned 字段**。
"""

from __future__ import annotations

from typing import Any

from .errors import PaperSchemaError
from .formula import FormulaError, parse_latex
from .nodes import normalize_node, validate_blocks
from .sanitize import sanitize_text

PATCH_OPS: tuple[str, ...] = ("insert", "update", "delete", "move")

#: "强结论"标记词表（**封闭常量**；宁可误拦让人补 claim 链接，不可漏拦让 AI 白拿结论）
STRONG_CONCLUSION_MARKERS: tuple[str, ...] = (
    "证明了", "证明如下", "严格优于", "显著优于", "从而得出",
    "本文证明", "我们证明", "由此可证", "定理成立",
)

#: 需要**状态锁**的 claim 状态：AI 不得改写其文本（人工编辑不受限）
LOCKED_CLAIM_STATUSES: frozenset[str] = frozenset({"INCONCLUSIVE", "REFUTED"})

_MAX_RATIONALE = 2000


def has_strong_conclusion(text: str) -> bool:
    """封闭词表判定。文本清洗后再比对，防 `<b>证明</b>了` 之类的绕过。"""
    clean = sanitize_text(str(text or ""))
    return any(m in clean for m in STRONG_CONCLUSION_MARKERS)


def lock_reason_for_claim_status(status: str, *, is_ai: bool) -> str | None:
    """状态锁：INCONCLUSIVE / REFUTED 的结论节点，AI 不得改写文本。

    人工编辑路径（PUT /paper）`is_ai=False` —— 用户自己改写并承担审计责任；
    这正是"AI 不能把不确定性改写成确定结论，但研究者可以"的边界。
    """
    if not is_ai:
        return None
    if str(status or "").upper() in LOCKED_CLAIM_STATUSES:
        return f"claim 当前状态为 {status}：AI 不得改写该结论的表述"
    return None


def _claim_label(status: str) -> str:
    from ..claim.types import STATUS_LABELS

    return STATUS_LABELS.get(str(status), str(status))


# --------------------------------------------------------------------------
# validate：schema + evidence coverage + output safety（纯函数，DB 由调用方投影）
# --------------------------------------------------------------------------


def validate_ops(
    ops: list[dict[str, Any]],
    *,
    current_blocks: list[dict[str, Any]],
    resolvable_evidence: set[str] | frozenset[str],
    resolvable_claims: set[str] | frozenset[str],
    claim_status: dict[str, str],
    resolvable_citations: set[str] | frozenset[str],
    resolvable_anchors: set[str] | frozenset[str],
    citation_hashes: dict[str, str] | None = None,
    anchor_details: dict[str, dict[str, Any]] | None = None,
    is_ai: bool = True,
) -> list[dict[str, Any]]:
    """校验并富化 patch ops。返回**新的** op 列表（含 `_enrich` 服务端快照）。

    全部引用解析通过**调用方注入的投影**（谁可解析/什么状态），保持本模块纯函数、
    可单测；DB 投影由 `proposal_handlers` / `paper.service` 负责。
    """
    if current_blocks:
        current_blocks = validate_blocks(current_blocks, stored=True)
    else:
        current_blocks = []
    by_id = {n["id"]: n for n in current_blocks}
    hashes = dict(citation_hashes or {})
    anchors = dict(anchor_details or {})
    out: list[dict[str, Any]] = []
    # 顺序感知：同一 patch 内**先前 op 新建的节点 id** 对后续 op 可见
    # （先插一段、再在它后面插公式是合法的编辑序列）。
    working_ids: set[str] = set(by_id)
    inserted_ids: set[str] = set()

    for raw in ops:
        if not isinstance(raw, dict):
            raise PaperSchemaError("PAPER_OP_TYPE_UNKNOWN", "patch op 必须是对象")
        op = str(raw.get("op") or "").strip()
        if op not in PATCH_OPS:
            raise PaperSchemaError("PAPER_OP_TYPE_UNKNOWN", f"未知 patch op: {op!r}")

        evidence_refs = [str(x)[:64] for x in (raw.get("evidenceRefs") or []) if str(x).strip()]
        missing_ev = [e for e in evidence_refs if e not in resolvable_evidence]
        if missing_ev:
            raise PaperSchemaError(
                "PAPER_EVIDENCE_UNRESOLVED",
                f"patch 引用了不可解析的证据: {missing_ev}",
                evidenceIds=missing_ev,
            )

        clean: dict[str, Any] = {"op": op, "rationale": "", "evidenceRefs": evidence_refs}

        target = str(raw.get("target") or "").strip()
        needs_target = op in {"update", "delete", "move"}
        if needs_target:
            if not target:
                raise PaperSchemaError("PAPER_OP_TARGET_REQUIRED", f"{op} 必须指定 target 节点")
            if target not in working_ids:
                raise PaperSchemaError(
                    "PAPER_OP_TARGET_MISSING", f"{op} 的 target 节点不存在: {target!r}"
                )
            clean["target"] = target
            expected_rev = raw.get("expectedNodeRev")
            if not isinstance(expected_rev, int) or isinstance(expected_rev, bool):
                raise PaperSchemaError(
                    "PAPER_OP_EXPECTED_NODE_REV_REQUIRED", f"{op} 必须携带 expectedNodeRev"
                )
            # nodeRev 校验只对**已存在**的节点做；本 patch 内新建的节点无从 stale
            if target in by_id and expected_rev != int(by_id[target].get("nodeRev") or 1):
                raise PaperSchemaError(
                    "PAPER_NODE_REV_STALE",
                    f"节点 {target} 已是修订 {by_id[target].get('nodeRev')}，"
                    f"patch 基于 {expected_rev}",
                    nodeId=target,
                    expectedNodeRev=expected_rev,
                )
            clean["expectedNodeRev"] = expected_rev

        rationale = sanitize_text(str(raw.get("rationale") or ""), limit=_MAX_RATIONALE)
        if not rationale:
            raise PaperSchemaError("PAPER_OP_RATIONALE_REQUIRED", f"{op} 缺少 rationale")
        clean["rationale"] = rationale

        after = raw.get("after")
        if after is not None:
            after_id = str(after).strip()
            if after_id not in working_ids:
                raise PaperSchemaError("PAPER_OP_AFTER_MISSING", f"after 节点不存在: {after_id!r}")
            clean["after"] = after_id

        node_raw = raw.get("node")
        if op in {"insert", "update"}:
            if not isinstance(node_raw, dict):
                raise PaperSchemaError("PAPER_OP_NODE_REQUIRED", f"{op} 必须携带 node")
            if is_ai:
                # AI 不得触碰 server-owned 字段（normalize_node 会再拦一次，
                # 这里提前给出更友好的定位）
                for owned in ("claimSnapshot", "citationSnapshot", "lineage"):
                    if owned in node_raw:
                        code = (
                            "PAPER_CLAIM_SNAPSHOT_SERVER_OWNED"
                            if owned == "claimSnapshot"
                            else "PAPER_SERVER_OWNED_FIELD"
                        )
                        raise PaperSchemaError(code, f"AI patch 不得自带 {owned}")

        enrich: dict[str, Any] = {}

        if op in {"insert", "update"}:
            node = dict(node_raw)

            # ---- claim 状态锁（先于 claimRef 解析门）：INCONCLUSIVE/REFUTED 禁改文本 ----
            new_text = str(node.get("text") or "")
            old_text = str(by_id[target].get("text") or "") if op == "update" and target in by_id else ""
            text_changed = op == "insert" or new_text != old_text
            if op == "update" and text_changed and target in by_id:
                old = by_id[target]
                current_status = ""
                if old.get("claimRef"):
                    current_status = str(claim_status.get(old["claimRef"], "")).upper()
                if not current_status and isinstance(old.get("claimSnapshot"), dict):
                    current_status = str(old["claimSnapshot"].get("status", "")).upper()
                reason = lock_reason_for_claim_status(current_status, is_ai=is_ai)
                if reason:
                    raise PaperSchemaError(
                        "PAPER_CLAIM_STATUS_LOCKED",
                        reason,
                        nodeId=target,
                        claimStatus=current_status,
                    )

            # ---- claimRef 解析 + 状态门 ----
            claim_ref = str(node.get("claimRef") or "").strip()
            if claim_ref:
                if claim_ref not in resolvable_claims:
                    raise PaperSchemaError(
                        "PAPER_CLAIM_REF_UNRESOLVED",
                        f"claimRef 指向不存在的 claim: {claim_ref!r}",
                        claimId=claim_ref,
                    )
                status = str(claim_status.get(claim_ref, "")).upper()
                if status != "SOLVED":
                    raise PaperSchemaError(
                        "PAPER_CLAIM_REF_UNPUBLISHED",
                        f"强结论只能链接已发布的 claim（当前 {status or '未知'}）",
                        claimId=claim_ref,
                        status=status,
                    )
                enrich["claimSnapshot"] = {
                    "claimId": claim_ref,
                    "status": status,
                    "statusLabel": _claim_label(status),
                    "published": True,
                }

            # ---- 强结论门：标记词 ⇒ 必须有 SOLVED claim 链接 ----
            if text_changed and has_strong_conclusion(new_text) and not claim_ref:
                raise PaperSchemaError(
                    "PAPER_CONCLUSION_UNLINKED",
                    "强结论必须链接 RV12 Claim（claimRef）；无链接只能中性表述",
                )

            # ---- equation：公式必须可解析；canonical/astHash 由服务端重算 ----
            if str(node.get("type")) == "equation":
                latex = str(node.get("latex") or node.get("text") or "").strip()
                if latex:
                    try:
                        parse_latex(latex)
                    except FormulaError as e:
                        raise PaperSchemaError(
                            "PAPER_FORMULA_UNPARSABLE", f"公式无法解析: {e}"
                        ) from e

            # ---- citation：可解析 + 版本绑定（fail-closed） ----
            if str(node.get("type")) == "citation":
                refs = [str(r) for r in (node.get("refs") or [])]
                missing = [r for r in refs if r not in resolvable_citations]
                if missing:
                    raise PaperSchemaError(
                        "PAPER_CITATION_UNRESOLVED",
                        f"citation 指向不存在的引用: {missing}",
                        citationIds=missing,
                    )
                versions = dict(node.get("versions") or {})
                if versions:
                    mismatch = {
                        r: v for r, v in versions.items() if hashes.get(r) != v
                    }
                    if mismatch:
                        raise PaperSchemaError(
                            "PAPER_CITATION_VERSION_MISMATCH",
                            f"citation 版本与当前 csl_hash 不一致: {sorted(mismatch)}",
                            citations=mismatch,
                        )
                elif hashes:
                    # AI 未带版本 ⇒ 服务端按**当前**版本落快照（不预测、不编造）
                    enrich["citationSnapshot"] = {
                        r: {"cslHash": hashes[r]} for r in refs if r in hashes
                    }

            # ---- figure/table：locator 必须可解析 ----
            if str(node.get("type")) in {"figure", "table"}:
                refs = [str(r) for r in (node.get("refs") or [])]
                missing = [r for r in refs if r not in resolvable_anchors]
                if missing:
                    raise PaperSchemaError(
                        "PAPER_FIGURE_LOCATOR_UNRESOLVED",
                        f"figure/table locator 指向不存在的锚点: {missing}",
                        anchorIds=missing,
                    )
                if anchors:
                    enrich["lineage"] = {
                        r: {
                            "assetId": anchors[r].get("assetId", ""),
                            "assetVersion": anchors[r].get("assetVersion", 0),
                            "contentHash": anchors[r].get("contentHash", ""),
                        }
                        for r in refs
                        if r in anchors
                    }

            clean["node"] = node
            if op == "insert":
                if str(node.get("id")) in working_ids:
                    raise PaperSchemaError(
                        "PAPER_NODE_ID_DUPLICATE", f"节点 id 重复: {node.get('id')}"
                    )
                working_ids.add(str(node.get("id")))
                inserted_ids.add(str(node.get("id")))

        if op == "delete":
            working_ids.discard(target)

        clean["_enrich"] = enrich
        out.append(clean)
    return out


# --------------------------------------------------------------------------
# review：人工逐项处置（revise）与 ops 的合并（纯函数）
# --------------------------------------------------------------------------


def apply_review(
    ops: list[dict[str, Any]],
    review: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """把人工逐项处置（`:revise` 写入的 `content["review"]`）合并进 ops。

    review 形状（**索引稳定**模型：accepted/rejected/edits 全部按 op 下标寻址，
    因此多次 revise 只需整体替换，前端不需要担心下标漂移）::

        {"accepted": [int, ...], "rejected": [int, ...],
         "edits": [{"index": int, "text"?: str, "latex"?: str, "rationale"?: str}]}

    语义边界：
    - rejected 的 op 被移除；accepted/edits 保留。同一索引同时 accept 与 reject
      视为矛盾（`PAPER_REVIEW_CONFLICT`），宁可拒绝也不猜用户意图；
    - edits 只允许改 `text` / `latex` / `rationale` —— 结构字段（type/refs/target/
      after/expectedNodeRev）禁改，要改结构就拒绝该 op 重新提案；
    - 移除后剩余 ops 必须**仍构成合法编辑序列**（update/delete/move 的 target、
      insert 的 after 都必须可解析）——否则确认时四道门会以更晦涩的错误失败，
      不如在这里直接给出定位（`PAPER_REVIEW_BREAKS_SEQUENCE`）。

    返回**新的** op 列表（深拷贝，不含被拒绝项；`_enrich` 保留原值，
    确认时 `apply_confirmed_patch` 会整体作废重算）。此处**不**重跑四道门 ——
    那是 accept 时的职责；这里只做"改完之后还站得住"的结构检查。
    """
    if not review:
        return [dict(o) for o in ops]
    if not isinstance(review, dict):
        raise PaperSchemaError("PAPER_REVIEW_INVALID", "review 必须是对象")

    accepted = _review_indexes(review.get("accepted"), len(ops))
    rejected = _review_indexes(review.get("rejected"), len(ops))
    conflict = sorted(accepted & rejected)
    if conflict:
        raise PaperSchemaError(
            "PAPER_REVIEW_CONFLICT",
            f"同一 op 不能同时接受与拒绝: {conflict}",
            indexes=conflict,
        )

    edits_by_index: dict[int, dict[str, Any]] = {}
    raw_edits = review.get("edits") or []
    if not isinstance(raw_edits, list):
        raise PaperSchemaError("PAPER_REVIEW_INVALID", "edits 必须是数组")
    for e in raw_edits:
        if not isinstance(e, dict):
            raise PaperSchemaError("PAPER_REVIEW_INVALID", "edit 必须是对象")
        idx = e.get("index")
        if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(ops)):
            raise PaperSchemaError(
                "PAPER_REVIEW_INDEX_INVALID", f"edit.index 越界: {idx!r}", index=idx
            )
        allowed = {"text", "latex", "rationale"}
        unknown = {str(k) for k in e if k not in allowed | {"index"}}
        if unknown:
            raise PaperSchemaError(
                "PAPER_REVIEW_FIELD_FORBIDDEN",
                f"edit 只允许修改 text/latex/rationale，得到: {sorted(unknown)}",
                fields=sorted(unknown),
            )
        edits_by_index[idx] = e

    # 合并 edit + 剔除 rejected
    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(ops):
        if idx in rejected:
            continue
        op = dict(raw)
        node = dict(op.get("node") or {}) if isinstance(op.get("node"), dict) else None
        edit = edits_by_index.get(idx, {})
        if node is None and ({"text", "latex"} & set(edit)):
            raise PaperSchemaError(
                "PAPER_REVIEW_FIELD_FORBIDDEN",
                f"op#{idx}（{op.get('op')}）没有可编辑的节点内容",
                index=idx,
            )
        if node is not None:
            if "text" in edit:
                node["text"] = sanitize_text(str(edit["text"] or ""))
            if "latex" in edit:
                latex = str(edit["latex"] or "").strip()
                if str(node.get("type")) == "equation":
                    try:
                        parse_latex(latex)
                    except FormulaError as e:
                        raise PaperSchemaError(
                            "PAPER_FORMULA_UNPARSABLE", f"编辑后的公式无法解析: {e}"
                        ) from e
                node["latex"] = latex
                # latex 变了，提案期 canonical/astHash 一律作废（确认时服务端重算）
                node.pop("canonical", None)
                node.pop("astHash", None)
            op["node"] = node
        if "rationale" in edit:
            op["rationale"] = sanitize_text(str(edit["rationale"] or ""), limit=_MAX_RATIONALE)
        out.append(op)

    if not out:
        return []

    # 序列完整性（精确判定）：review 只能引入一类新破坏 —— **显式接受**的 op
    # 引用了被拒绝 insert 所创建的节点 id（target / after）。两个边界：
    # - 未决 op（不在 accepted/rejected 中）不在此拦截：部分评审时其命运未定，
    #   最终 :accept 时 `apply_confirmed_patch` 会重跑 `validate_ops` 兜底悬空引用；
    # - 指向基线节点的引用不受 review 影响（基线未被 review 改动）；
    #   其余序列问题在提案期已被 `validate_ops` 拦下，能进到这里说明原 patch 合法。
    removed_insert_ids = {
        str((raw.get("node") or {}).get("id") or "")
        for idx, raw in enumerate(ops)
        if idx in rejected and str(raw.get("op") or "") == "insert"
    }
    removed_insert_ids.discard("")
    if removed_insert_ids:
        kept_idxs = [i for i in range(len(ops)) if i not in rejected]
        for orig_idx, op in zip(kept_idxs, out):
            if orig_idx not in accepted:
                continue
            dangling = [
                ref
                for ref in (str(op.get("target") or ""), str(op.get("after") or ""))
                if ref and ref in removed_insert_ids
            ]
            if dangling:
                raise PaperSchemaError(
                    "PAPER_REVIEW_BREAKS_SEQUENCE",
                    f"拒绝该 op 后，被接受的 op 引用的节点不再存在: {dangling}",
                    nodeIds=dangling,
                )
    return out


def _review_indexes(raw: Any, length: int) -> set[int]:
    """校验 accepted/rejected 索引数组：必须是界内整数（bool 除外）。"""
    if raw is None:
        return set()
    if not isinstance(raw, list):
        raise PaperSchemaError("PAPER_REVIEW_INVALID", "accepted/rejected 必须是整数数组")
    out: set[int] = set()
    for v in raw:
        if not isinstance(v, int) or isinstance(v, bool) or not (0 <= v < length):
            raise PaperSchemaError(
                "PAPER_REVIEW_INDEX_INVALID", f"索引越界: {v!r}", index=v
            )
        out.add(v)
    return out


# --------------------------------------------------------------------------
# apply：纯结构变换（validated ops）
# --------------------------------------------------------------------------


def apply_ops(
    blocks: list[dict[str, Any]], ops: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """把 validated ops 应用到 blocks。**必须先过 `validate_ops`**。

    语义：update 后 `nodeRev` 递增；insert 要求唯一 id；delete 直接移除；
    move 只改位置。server-owned 快照来自 `_enrich`（validate 阶段服务端解析）。
    """
    working = [dict(n) for n in blocks]
    order = [n["id"] for n in working]
    by_id = {n["id"]: n for n in working}

    for op in ops:
        kind = op["op"]
        enrich = dict(op.get("_enrich") or {})

        if kind == "insert":
            node = normalize_node(op["node"])
            if node["id"] in by_id:
                raise PaperSchemaError("PAPER_NODE_ID_DUPLICATE", f"节点 id 重复: {node['id']}")
            node = _enrich_node(node, enrich)
            by_id[node["id"]] = node
            after = op.get("after")
            if after and after in order:
                order.insert(order.index(after) + 1, node["id"])
            else:
                order.append(node["id"])

        elif kind == "update":
            target = op["target"]
            old = by_id[target]
            new = normalize_node({**op["node"], "id": target})
            new["nodeRev"] = int(old.get("nodeRev") or 1) + 1
            # 公式：服务端重算 canonical/astHash（AI 提供的忽略）
            if new["type"] == "equation":
                from .formula import ast_hash, canonical, parse_latex

                ast = parse_latex(new["latex"])
                new["canonical"] = canonical(ast)
                new["astHash"] = ast_hash(ast)
            # server-owned 快照：优先用本次 enrich；claimRef 未变且无新快照则保留旧值
            for key in ("claimSnapshot", "citationSnapshot", "lineage"):
                if key in enrich:
                    new[key] = enrich[key]
                elif key in old and new.get("refs") == old.get("refs") and new.get("claimRef") == old.get("claimRef"):
                    new[key] = old[key]
            by_id[target] = new

        elif kind == "delete":
            target = op["target"]
            by_id.pop(target, None)
            order = [i for i in order if i != target]

        elif kind == "move":
            target = op["target"]
            node = by_id.get(target)
            if node is None:
                raise PaperSchemaError("PAPER_OP_TARGET_MISSING", f"move target 不存在: {target}")
            order = [i for i in order if i != target]
            after = op.get("after")
            if after and after in order:
                order.insert(order.index(after) + 1, target)
            else:
                order.append(target)

    return [by_id[i] for i in order]


def _enrich_node(node: dict[str, Any], enrich: dict[str, Any]) -> dict[str, Any]:
    if node["type"] == "equation":
        from .formula import ast_hash, canonical, parse_latex

        ast = parse_latex(node["latex"])
        node["canonical"] = canonical(ast)
        node["astHash"] = ast_hash(ast)
    for key in ("claimSnapshot", "citationSnapshot", "lineage"):
        if key in enrich:
            node[key] = enrich[key]
    return node
