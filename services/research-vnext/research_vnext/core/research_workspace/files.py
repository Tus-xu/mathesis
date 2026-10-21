"""RV17 · 草稿文件树服务（P0-3）。

- 树 / 读 / 写 / 建 / 删 / 重命名
- `PUT` 用 `expected_revision` 做**乐观并发**（冲突 → 409，宪法 §51 自动保存语义）
- `outline` 由 `main.tex` 解析出带行号的结构树（宪法 §18）
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import AccessContext
from ..domain.errors import DomainError, NotFound, RevisionConflict
from .models import FILE_KINDS, ResearchProjectFileRow, utcnow
from .outline import parse_outline

_EXT_KIND = {
    "tex": "tex",
    "bib": "bib",
    "sty": "sty",
    "cls": "cls",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "svg": "image",
    "pdf": "pdf",
    "csv": "data",
    "json": "data",
    "dat": "data",
    "txt": "data",
}


class InvalidFilePath(DomainError):
    code = "INVALID_FILE_PATH"
    http_status = 422


class FileConflict(DomainError):
    code = "FILE_ALREADY_EXISTS"
    http_status = 409


def normalize_path(raw: str) -> str:
    """归一化并**校验**文件路径（拒绝对路径 / 上跳 / 反斜杠逃逸）。"""
    p = str(raw or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    if not p:
        raise InvalidFilePath("文件路径不能为空")
    if ".." in p.split("/"):
        raise InvalidFilePath("文件路径不允许包含 '..'")
    if len(p) > 512:
        raise InvalidFilePath("文件路径过长")
    if any(seg == "" for seg in p.split("/")):
        raise InvalidFilePath("文件路径不允许包含空路径段（重复 '/'）")
    return p


def infer_kind(path: str, kind: str | None = None) -> str:
    if kind:
        k = str(kind).strip().lower()
        if k not in FILE_KINDS:
            raise InvalidFilePath(f"kind 非法: {k!r}（允许 {sorted(FILE_KINDS)}）")
        return k
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _EXT_KIND.get(ext, "data")


def file_public_dict(row: ResearchProjectFileRow, *, include_content: bool = False) -> dict:
    out = {
        "id": str(row.id),
        "path": row.path,
        "kind": row.kind,
        "revision": int(row.revision),
        "asset_id": str(row.asset_id) if row.asset_id else None,
        "size": len(row.content or ""),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "saved_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    if include_content:
        out["content"] = row.content
    return out


def list_files(db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID) -> list[ResearchProjectFileRow]:
    rows = db.execute(
        select(ResearchProjectFileRow)
        .where(
            ResearchProjectFileRow.tenant_id == tenant_id,
            ResearchProjectFileRow.project_id == project_id,
        )
        .order_by(ResearchProjectFileRow.path.asc())
    ).scalars().all()
    return list(rows)


def build_tree(rows: list[ResearchProjectFileRow]) -> list[dict]:
    """由扁平文件行构建嵌套目录树。"""
    root: dict[str, dict] = {}

    def _ensure_dir(parts: list[str], node: dict) -> dict:
        cur = node
        acc: list[str] = []
        for seg in parts:
            acc.append(seg)
            nxt = cur.get(seg)
            if nxt is None:
                nxt = {
                    "name": seg,
                    "path": "/".join(acc),
                    "type": "dir",
                    "kind": "dir",
                    "children": {},
                }
                cur[seg] = nxt
            cur = nxt["children"]
        return cur

    for row in rows:
        parts = row.path.split("/")
        parent = _ensure_dir(parts[:-1], root)
        parent[parts[-1]] = {
            "name": parts[-1],
            "path": row.path,
            "type": "file",
            "kind": row.kind,
            "revision": int(row.revision),
            "size": len(row.content or ""),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "children": {},
        }

    def _upgrade(node_map: dict) -> list[dict]:
        out: list[dict] = []
        for node in node_map.values():
            node["children"] = _upgrade(node["children"])
            out.append(node)
        out.sort(key=lambda c: (c["type"] != "dir", c["name"]))
        return out

    return _upgrade(root)


def get_file(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, path: str
) -> ResearchProjectFileRow:
    p = normalize_path(path)
    row = db.execute(
        select(ResearchProjectFileRow).where(
            ResearchProjectFileRow.tenant_id == tenant_id,
            ResearchProjectFileRow.project_id == project_id,
            ResearchProjectFileRow.path == p,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound(f"文件不存在: {p}")
    return row


def create_file(
    db: Session,
    ctx: AccessContext,
    *,
    project_id: uuid.UUID,
    path: str,
    kind: str | None = None,
    content: str | None = None,
    asset_id: uuid.UUID | None = None,
) -> ResearchProjectFileRow:
    p = normalize_path(path)
    existing = db.execute(
        select(ResearchProjectFileRow).where(
            ResearchProjectFileRow.project_id == project_id,
            ResearchProjectFileRow.path == p,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise FileConflict(f"文件已存在: {p}")

    now = utcnow()
    row = ResearchProjectFileRow(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        project_id=project_id,
        path=p,
        kind=infer_kind(p, kind),
        content=content,
        asset_id=asset_id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def write_file(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    path: str,
    content: str,
    expected_revision: int | None = None,
) -> ResearchProjectFileRow:
    """写文件（自动保存语义）。`expected_revision` 不匹配 → 409（乐观并发）。"""
    row = get_file(db, tenant_id=tenant_id, project_id=project_id, path=path)
    if expected_revision is not None and int(expected_revision) != int(row.revision):
        raise RevisionConflict(expected=int(expected_revision), current=int(row.revision))
    row.content = content
    row.revision = int(row.revision) + 1
    row.updated_at = utcnow()
    db.flush()
    return row


def delete_file(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, path: str
) -> None:
    row = get_file(db, tenant_id=tenant_id, project_id=project_id, path=path)
    db.delete(row)
    db.flush()


def rename_file(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, src: str, dst: str
) -> ResearchProjectFileRow:
    row = get_file(db, tenant_id=tenant_id, project_id=project_id, path=src)
    new_path = normalize_path(dst)
    clash = db.execute(
        select(ResearchProjectFileRow).where(
            ResearchProjectFileRow.project_id == project_id,
            ResearchProjectFileRow.path == new_path,
            ResearchProjectFileRow.id != row.id,
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise FileConflict(f"目标路径已存在: {new_path}")
    old_ext = row.path.rsplit(".", 1)[-1].lower() if "." in row.path else ""
    new_ext = new_path.rsplit(".", 1)[-1].lower() if "." in new_path else ""
    row.path = new_path
    if old_ext != new_ext:
        row.kind = infer_kind(new_path, None)
    row.updated_at = utcnow()
    db.flush()
    return row


def outline(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, entry: str = "main.tex"
) -> dict:
    """项目大纲：解析 entry 并**展开 `\input`/`\include` 引用的子文件**。

    r7-deploy 修复（eng-deploy）：多文件项目（main.tex 只 `\input{sections/*}`）此前
    只扫 entry，结构树只剩「摘要」一项——前端 OutlinePanel 的契约是
    `items[{title,level,line,kind,file}]`（OutlinePanel.vue navigate(file, line)），
    缺 `file` 字段即结构树残缺、点击无法跨文件定位。

    语义：`file` = item 来源文件路径，`line` = 该文件内的行号；排序按
    `\input` 出现位置展开后的文档顺序。递归深度 ≤5、`visiting` 集合防环；
    缺失的 `\input` 目标静默跳过（与「词法级解析、不编译不展开宏」的确定性原则一致）。
    """
    from .outline import build_tree

    input_re = re.compile(r"\\(?:input|include)\s*\{([^{}]+)\}")

    def parse_one(path: str) -> tuple[list[dict], list[tuple[int, str]]]:
        try:
            row = get_file(db, tenant_id=tenant_id, project_id=project_id, path=path)
        except Exception:  # noqa: BLE001 - 目标文件缺失按空处理
            return [], []
        content = row.content or ""
        parsed = parse_outline(content, source_file=path)
        items = [{**it, "file": path} for it in parsed["items"]]
        refs: list[tuple[int, str]] = []
        for lineno, line in enumerate(content.splitlines(), start=1):
            m = input_re.search(line)
            if m:
                target = m.group(1).strip()
                if not target.endswith(".tex"):
                    target += ".tex"
                refs.append((lineno, target))
        return items, refs

    def flatten(path: str, depth: int, visiting: set[str]) -> list[dict]:
        if depth > 5 or path in visiting:
            return []
        visiting.add(path)
        own, refs = parse_one(path)
        # 同一行内：先本文件 item，后展开该行 \input 引入的子树。
        events: list[tuple[int, int, object]] = [(it["line"], 1, it) for it in own]
        events += [(ln, 2, target) for ln, target in refs]
        events.sort(key=lambda e: (e[0], e[1]))
        out: list[dict] = []
        for _, _, payload in events:
            if isinstance(payload, dict):
                out.append(payload)  # type: ignore[arg-type]
            else:
                out.extend(flatten(str(payload), depth + 1, visiting))
        visiting.discard(path)
        return out

    root = get_file(db, tenant_id=tenant_id, project_id=project_id, path=entry)
    items = flatten(entry, 0, set())
    return {
        "source_file": root.path,
        "items": items,
        "tree": build_tree(items),
        "revision": int(root.revision),
    }


__all__ = [
    "FileConflict",
    "InvalidFilePath",
    "build_tree",
    "create_file",
    "delete_file",
    "file_public_dict",
    "get_file",
    "infer_kind",
    "list_files",
    "normalize_path",
    "outline",
    "rename_file",
    "write_file",
]
