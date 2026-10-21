"""RV13 · 节点级 diff —— expected_revision 冲突必须"让人看懂"。

与 RV09 的 `revision_diff`（路径级，给任意 JSON）互补：
论文块树是**以节点 id 为身份**的有序列表，索引比较毫无意义 ——
"在中间插一段"会把后面所有块都报成 changed。本模块按 **id 配对**，
产出 added / removed / changed（字段级 before/after）/ moved 四类，
供 409 冲突响应与修订审计共用。

确定性：同样的输入必得同样的输出（字段按名排序、节点按出现顺序）。
"""

from __future__ import annotations

from typing import Any

MAX_DIFF_NODES = 200
_MAX_PREVIEW = 200


def _preview(value: Any, limit: int = _MAX_PREVIEW) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else _stable(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"…(+{len(text) - limit} 字符)"


def _stable(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _content_fingerprint(node: dict[str, Any]) -> str:
    """节点**内容**指纹（nodeRev 与 server-owned 快照除外）。

    nodeRev 变化本身不是内容变化；快照变化属于事实源变化，由 bindings 通道报告，
    不算"作者改了论文"。
    """
    import hashlib
    import json

    body = {k: v for k, v in node.items() if k not in {"nodeRev", "claimSnapshot", "citationSnapshot", "lineage"}}
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def diff_nodes(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> dict[str, Any]:
    """按节点 id 配对的结构化 diff。

    返回
    ----
    `{added, removed, changed, moved, count, empty, truncated}`；
    - `changed[i].fields`：字段级 `{field, before, after}`（nodeRev 单独报告）；
    - `moved[i]`：`{id, from, to}`，纯位置变化**不**算 changed；
    - 全部顺序确定（before 序 / after 序 / 字段名字序）。
    """
    before = list(before or [])
    after = list(after or [])
    before_ids = [str(n.get("id")) for n in before]
    after_ids = [str(n.get("id")) for n in after]
    before_map = {str(n.get("id")): n for n in before}
    after_map = {str(n.get("id")): n for n in after}

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    truncated = False

    def _truncate() -> bool:
        nonlocal truncated
        total = len(added) + len(removed) + len(changed) + len(moved)
        if total >= MAX_DIFF_NODES:
            truncated = True
            return True
        return False

    # added（after 序）
    for idx, nid in enumerate(after_ids):
        if nid not in before_map:
            if _truncate():
                break
            node = after_map[nid]
            added.append({"id": nid, "type": node.get("type", ""), "afterIndex": idx})

    # removed（before 序）
    if not truncated:
        for idx, nid in enumerate(before_ids):
            if nid not in after_map:
                if _truncate():
                    break
                node = before_map[nid]
                removed.append({"id": nid, "type": node.get("type", ""), "beforeIndex": idx})

    # changed / moved（before 序，确定性）
    if not truncated:
        for nid in before_ids:
            if nid not in after_map:
                continue
            b, a = before_map[nid], after_map[nid]
            if _content_fingerprint(b) != _content_fingerprint(a):
                if _truncate():
                    break
                fields: list[dict[str, Any]] = []
                for key in sorted(set(b) | set(a)):
                    if key in {"nodeRev", "claimSnapshot", "citationSnapshot", "lineage"}:
                        continue
                    bv, av = b.get(key), a.get(key)
                    if bv != av:
                        fields.append({"field": key, "before": _preview(bv), "after": _preview(av)})
                changed.append(
                    {
                        "id": nid,
                        "type": a.get("type", b.get("type", "")),
                        "fields": fields,
                        "nodeRev": {
                            "before": b.get("nodeRev"),
                            "after": a.get("nodeRev"),
                        },
                    }
                )
            bi, ai = before_ids.index(nid), after_ids.index(nid)
            if bi != ai:
                if _truncate():
                    break
                moved.append({"id": nid, "from": bi, "to": ai})

    count = len(added) + len(removed) + len(changed) + len(moved)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "moved": moved,
        "count": count,
        "empty": count == 0,
        "truncated": truncated,
    }
