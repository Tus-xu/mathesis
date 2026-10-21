"""RV09 · 修订 diff —— 让"改了什么"可显示，而不是只给一个冲突码。

两个使用场景
------------
1. **409 冲突**：用户基于 revision 3 提了编辑，服务端已经到 revision 5。
   只回 `REVISION_CONFLICT` 等于让用户重新想一遍"我改的和别人改的差在哪"。
   把两侧内容一并返回，前端才能并排显示。
2. **修订审计**：`object_revision_audit.diff` 记录 revision N-1 → N 的结构变化，
   使"这条约束是什么时候加的、谁加的"可回溯。

设计约束
--------
- **确定性**：同样的输入必得同样的输出（排序固定），否则审计行无法比对；
- **纯函数**：不碰 DB / 不依赖 ORM；
- **有界**：只报告**结构变化**（按路径），不复制整份内容。
  内容本体已经在修订行里，diff 复制一份只会让审计表变成第二份真相。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_DIFF_ITEMS = 200


@dataclass
class Diff:
    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    @property
    def count(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "count": self.count,
            "truncated": self.truncated,
        }

    def summary(self) -> str:
        if self.empty:
            return "无结构变化"
        return f"+{len(self.added)} / -{len(self.removed)} / ~{len(self.changed)}"


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def diff_content(before: Any, after: Any) -> Diff:
    """按路径比较两份内容，产出确定性 diff。

    列表**按索引**比较（领域对象的元素顺序是有意义的：`constraints#1` 就是第二个约束），
    因此"删除中间一条约束"会表现为后续若干条 changed + 末尾一条 removed ——
    这是刻意的：它忠实反映"哪些元素位置变了"，比按内容配对更可审计。
    """
    out = Diff()
    _walk(before, after, path="", out=out)
    return out


def _push(target: list[dict[str, Any]], *, path: str, value: Any, out: Diff) -> None:
    if out.count >= MAX_DIFF_ITEMS:
        out.truncated = True
        return
    target.append({"path": path, "value": _preview(value)})


def _walk(before: Any, after: Any, *, path: str, out: Diff) -> None:
    if out.truncated:
        return

    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else str(key)
            if key not in before:
                _push(out.added, path=child, value=after[key], out=out)
            elif key not in after:
                _push(out.removed, path=child, value=before[key], out=out)
            else:
                _walk(before[key], after[key], path=child, out=out)
        return

    if isinstance(before, list) and isinstance(after, list):
        for i in range(max(len(before), len(after))):
            child = f"{path}[{i}]"
            if i >= len(before):
                _push(out.added, path=child, value=after[i], out=out)
            elif i >= len(after):
                _push(out.removed, path=child, value=before[i], out=out)
            else:
                _walk(before[i], after[i], path=child, out=out)
        return

    if before != after:
        if _is_scalar(before) and _is_scalar(after):
            if out.count >= MAX_DIFF_ITEMS:
                out.truncated = True
                return
            out.changed.append({"path": path, "before": _preview(before), "after": _preview(after)})
        else:
            # 类型变化（dict ↔ list ↔ scalar）：记为一次替换，递归进去只会产出噪音。
            if out.count >= MAX_DIFF_ITEMS:
                out.truncated = True
                return
            out.changed.append(
                {"path": path, "before": _preview(before), "after": _preview(after), "typeChanged": True}
            )


def _preview(value: Any, limit: int = 200) -> Any:
    """只保留可阅读的短预览。长文本截断并**明确标注**，不做"看起来完整"的假象。"""
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"…(+{len(value) - limit} 字符)"
    if _is_scalar(value):
        return value
    if isinstance(value, list):
        return {"__list_len__": len(value), "preview": [_preview(v, 60) for v in value[:3]]}
    if isinstance(value, dict):
        keys = sorted(value)[:8]
        return {"__dict_keys__": keys, "preview": {k: _preview(value[k], 60) for k in keys[:3]}}
    return str(value)[:limit]


__all__ = ["MAX_DIFF_ITEMS", "Diff", "diff_content"]
