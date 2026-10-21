"""RV13 · PaperSpec typed node schema（canonical structured document）。

设计立场
--------
- PaperSpec 是 **canonical structured document**：`content = {"blocks": [...]}`，
  每个块是一个**版本化 typed node**（`nodeRev` 随内容变更递增）。
- 存储不认 editor DOM、raw HTML、LaTeX 全文 —— 它们只是**投影/导入导出格式**。
- `claimSnapshot` / `citationSnapshot` / `lineage` 是 **server-owned** 字段：
  由服务端在确认/编辑时用真源回填，AI 与客户端不得自带。
"""

from __future__ import annotations

from typing import Any

from .errors import PaperSchemaError
from .sanitize import sanitize_text, sanitize_url

#: 封闭节点类型集（任务书 8 类 + RV12 论文引用门依赖的 3 个引用位）
PAPER_NODE_TYPES: frozenset[str] = frozenset(
    {
        "section", "heading", "paragraph", "equation",
        "citation", "figure", "table", "callout",
        "claim_ref", "evidence_ref", "review_mark",
    }
)

#: 带 refs 的节点类型（引用位必须可解析）
_REF_TYPES: frozenset[str] = frozenset({"citation", "figure", "table", "claim_ref", "evidence_ref"})

#: callout 封闭词表
CALLOUT_KINDS: frozenset[str] = frozenset({"INFO", "WARNING", "GAP"})

#: server-owned 节点字段（AI/客户端提供即拒绝）
SERVER_OWNED_NODE_FIELDS: frozenset[str] = frozenset(
    {"claimSnapshot", "citationSnapshot", "lineage"}
)

#: 各类节点允许的字段（id/type/nodeRev 之外）
_NODE_FIELDS: dict[str, frozenset[str]] = {
    "section": frozenset({"text", "number"}),
    "heading": frozenset({"text", "level"}),
    "paragraph": frozenset({"text", "claimRef"}),
    "equation": frozenset({"text", "latex", "display", "canonical", "astHash"}),
    "citation": frozenset({"text", "refs", "versions"}),
    "figure": frozenset({"text", "refs", "src", "locator", "caption"}),
    "table": frozenset({"text", "refs", "locator", "caption", "columns"}),
    "callout": frozenset({"text", "kind"}),
    "claim_ref": frozenset({"text", "refs"}),
    "evidence_ref": frozenset({"text", "refs"}),
    "review_mark": frozenset({"text"}),
}

_MAX_TEXT = 20000
_MAX_LATEX = 10000
_MAX_CAPTION = 2000
_MAX_ID = 64


def normalize_node(raw: dict[str, Any], *, stored: bool = False) -> dict[str, Any]:
    """把一个原始节点规范化为 canonical 节点；违规抛 `PaperSchemaError`。

    规范化 = 类型校验 + 字段收敛 + 文本清洗 + 默认值回填。

    `stored=True`：校验**已入库**内容（允许携带 server-owned 快照字段）；
    默认（`stored=False`）用于校验**外部输入**（AI/客户端），server-owned 一律拒绝。
    """
    if not isinstance(raw, dict):
        raise PaperSchemaError("PAPER_NODE_TYPE_UNKNOWN", f"节点必须是对象，得到 {type(raw).__name__}")
    ntype = str(raw.get("type") or "").strip()
    if ntype not in PAPER_NODE_TYPES:
        raise PaperSchemaError("PAPER_NODE_TYPE_UNKNOWN", f"未知 block 类型: {ntype!r}")

    node_id = str(raw.get("id") or "").strip()
    if not node_id or len(node_id) > _MAX_ID:
        raise PaperSchemaError("PAPER_NODE_ID_INVALID", f"节点 id 非法: {node_id!r}")

    for owned in SERVER_OWNED_NODE_FIELDS:
        if owned in raw and not stored:
            code = (
                "PAPER_CLAIM_SNAPSHOT_SERVER_OWNED"
                if owned == "claimSnapshot"
                else "PAPER_SERVER_OWNED_FIELD"
            )
            raise PaperSchemaError(
                code,
                f"字段 {owned} 由服务端回填，客户端/AI 不得自带",
            )

    node_rev = raw.get("nodeRev", 1)
    if not isinstance(node_rev, int) or isinstance(node_rev, bool) or node_rev < 1:
        raise PaperSchemaError("PAPER_NODE_REV_INVALID", f"nodeRev 必须是正整数: {node_rev!r}")

    allowed = _NODE_FIELDS[ntype]
    for key in raw:
        if key in {"id", "type", "nodeRev"}:
            continue
        if stored and key in SERVER_OWNED_NODE_FIELDS:
            continue  # 已入库内容：快照字段在末尾原样透传
        if key not in allowed:
            raise PaperSchemaError(
                "PAPER_NODE_FIELD_NOT_ALLOWED", f"类型 {ntype} 不允许字段 {key!r}"
            )

    node: dict[str, Any] = {"id": node_id, "type": ntype, "nodeRev": node_rev}

    # ---- 文本类字段（统一清洗） ----
    if "text" in allowed:
        node["text"] = sanitize_text(str(raw.get("text") or ""), limit=_MAX_TEXT)
    if "caption" in allowed and raw.get("caption"):
        node["caption"] = sanitize_text(str(raw["caption"]), limit=_MAX_CAPTION)

    # ---- heading.level ----
    if ntype == "heading":
        level = raw.get("level")
        if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 6:
            raise PaperSchemaError("PAPER_HEADING_LEVEL_INVALID", f"heading level 非法: {level!r}")
        node["level"] = level

    # ---- equation：latex（或 text 回退），canonical/astHash 由服务端 formula 模块回填 ----
    if ntype == "equation":
        latex = str(raw.get("latex") or raw.get("text") or "").strip()
        if not latex:
            raise PaperSchemaError("PAPER_EQUATION_EMPTY", "equation 节点必须携带公式")
        if len(latex) > _MAX_LATEX:
            raise PaperSchemaError("PAPER_EQUATION_TOO_LONG", "公式超长")
        node["latex"] = latex
        node.pop("text", None)
        node["display"] = bool(raw.get("display", False))

    # ---- refs（引用位） ----
    if ntype in _REF_TYPES:
        refs = raw.get("refs")
        if not isinstance(refs, list) or not refs or not all(
            isinstance(r, str) and r.strip() for r in refs
        ):
            raise PaperSchemaError("PAPER_REFS_REQUIRED", f"{ntype} 节点必须携带非空 refs")
        node["refs"] = [str(r).strip()[:128] for r in refs]

    # ---- citation.versions（引用版本绑定） ----
    if ntype == "citation" and isinstance(raw.get("versions"), dict):
        node["versions"] = {str(k)[:128]: str(v)[:64] for k, v in raw["versions"].items()}

    # ---- figure.src（URL scheme allowlist） ----
    if ntype == "figure" and raw.get("src"):
        node["src"] = sanitize_url(str(raw["src"]))

    # ---- figure/table.locator（immutable locator 快照） ----
    if ntype in {"figure", "table"} and isinstance(raw.get("locator"), dict):
        locator = raw["locator"]
        clean_loc: dict[str, Any] = {}
        for k in ("page", "bbox", "sectionPath", "anchorId", "kind"):
            if k in locator:
                clean_loc[k] = locator[k]
        node["locator"] = clean_loc

    # ---- table.columns ----
    if ntype == "table" and isinstance(raw.get("columns"), list):
        node["columns"] = [sanitize_text(str(c), limit=200) for c in raw["columns"][:32]]

    # ---- callout.kind ----
    if ntype == "callout":
        kind = str(raw.get("kind") or "").strip().upper()
        if kind not in CALLOUT_KINDS:
            raise PaperSchemaError(
                "PAPER_CALLOUT_KIND_UNKNOWN", f"callout kind 非法: {kind!r}"
            )
        node["kind"] = kind

    # ---- paragraph.claimRef（强结论必须链接 RV12 Claim） ----
    if ntype == "paragraph" and raw.get("claimRef"):
        node["claimRef"] = str(raw["claimRef"]).strip()[:128]

    if "number" in allowed and raw.get("number"):
        node["number"] = sanitize_text(str(raw["number"]), limit=64)

    if stored:
        # 已入库内容：canonical/astHash 与 server-owned 快照**原样透传**
        # （canonical 的真实性由 formula 往返在写入点保证，这里不重算）
        if ntype == "equation":
            node["canonical"] = str(raw.get("canonical") or "")
            node["astHash"] = str(raw.get("astHash") or "")
        for owned in SERVER_OWNED_NODE_FIELDS:
            if owned in raw:
                node[owned] = raw[owned]

    return node


def validate_blocks(
    blocks: list[dict[str, Any]], *, stored: bool = False
) -> list[dict[str, Any]]:
    """整树校验：非空 + 唯一 id + 逐节点规范化。返回 canonical blocks。

    `stored=True` 用于读回/冲突投影等**已入库**内容（含 server-owned 快照）。
    """
    if not blocks:
        raise PaperSchemaError("PAPER_NO_BLOCKS", "论文必须包含至少一个节点")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in blocks:
        node = normalize_node(raw, stored=stored)
        if node["id"] in seen:
            raise PaperSchemaError("PAPER_NODE_ID_DUPLICATE", f"节点 id 重复: {node['id']}")
        seen.add(node["id"])
        out.append(node)
    return out


def bump_node_rev(node: dict[str, Any]) -> dict[str, Any]:
    out = dict(node)
    out["nodeRev"] = int(node.get("nodeRev") or 1) + 1
    return out


def strip_server_owned(raw: dict[str, Any]) -> dict[str, Any]:
    """AI/客户端节点不得自带 server-owned 字段（存在即拒绝，不做静默剥离）。"""
    for owned in SERVER_OWNED_NODE_FIELDS:
        if owned in raw:
            code = (
                "PAPER_CLAIM_SNAPSHOT_SERVER_OWNED"
                if owned == "claimSnapshot"
                else "PAPER_SERVER_OWNED_FIELD"
            )
            raise PaperSchemaError(
                code,
                f"字段 {owned} 由服务端回填，客户端/AI 不得自带",
            )
    return raw
