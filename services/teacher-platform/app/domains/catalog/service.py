# coding: utf-8
"""catalog 域查询服务 —— 题库查询路径（M1）与班级/今日聚合骨架。

quiz 查询语义（B4 规范 §3.1 / 前端 api/teacherV3.ts catalog.quizQuestions）：
  kp        多码逗号分隔 → kp_code 相等 OR code ∈ kp_path（text[] GIN，子树前缀）
  q         题干关键词 → stem_plain ILIKE（IFC-002）∪ stem_latex ILIKE（LaTeX 命中噪声大，
            仅作兜底通道；语义检索通道 B2 Retriever 于 M3 接入，混合排序）
  difficulty/q_type/source/folder  精确过滤（folder 走 folder_question_refs 引用表）
  total     独立 COUNT（精确计数，红线 4：不许假数）
列表接口禁全表扫：分页（limit/offset，游标 P5 优化）+ ix_quiz_kp_code/ix_quiz_kp_path_gin。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.domains.catalog.models import Clazz, FolderQuestionRef, QuizQuestion


@dataclass
class QuizQueryFilters:
    kp: list[str] = field(default_factory=list)   # 已拆分的 kp_code 列表
    q: str = ''
    difficulty: str = ''
    q_type: str = ''
    source: str = ''
    folder: UUID | None = None
    year: str = ''
    limit: int = 20
    offset: int = 0

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 100:
            raise ValueError('limit 取值 1-100')


def _conditions(f: QuizQueryFilters) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    if f.kp:
        conds.append(or_(*[
            or_(QuizQuestion.kp_code == code, func.array_position(QuizQuestion.kp_path, code).is_not(None))
            for code in f.kp
        ]))
    if f.q:
        kw = f'%{f.q.strip()}%'
        conds.append(or_(QuizQuestion.stem_plain.ilike(kw), QuizQuestion.stem_latex.ilike(kw)))
    if f.difficulty:
        conds.append(QuizQuestion.difficulty == f.difficulty)
    if f.q_type:
        conds.append(QuizQuestion.q_type == f.q_type)
    if f.source:
        conds.append(QuizQuestion.source == f.source)
    if f.year:
        conds.append(QuizQuestion.year == f.year)
    if f.folder is not None:
        conds.append(QuizQuestion.id.in_(
            select(FolderQuestionRef.question_id).where(FolderQuestionRef.folder_id == f.folder)
        ))
    return conds


def build_quiz_query(f: QuizQueryFilters) -> tuple[Select, Select]:
    """返回（数据查询, total 计数查询）——total 精确 COUNT，不许假数。"""
    stmt = select(QuizQuestion)
    count = select(func.count()).select_from(QuizQuestion)
    conds = _conditions(f)
    if conds:
        stmt = stmt.where(*conds)
        count = count.where(*conds)
    stmt = stmt.order_by(QuizQuestion.created_at.desc(), QuizQuestion.id.desc()).limit(f.limit).offset(f.offset)
    return stmt, count


def build_classes_query() -> Select:
    """班级列表（今日工作台/课堂开课共用）。"""
    return select(Clazz).order_by(Clazz.name)


def build_kp_tree(nodes: list, own: dict[str, int]) -> list[dict]:
    """知识点树构建（纯函数，2026-10-15 题库分类树治理）。

    题量口径：**归属集合去重计数** —— 每道题对它归属的每个 code（kp_code ∪ kp_path）
    只计 1 次（own 由调用方按此口径算好传入），节点题量 = own(自身) + Σ own(子孙)。
    此前 exact+subtree 相加会把 kp_code 与 kp_path 同时命中的题计两次（三角章 111→222）。

    树的视野：只返回**子树内有题**或**教师自建**（source='teacher'）的根及其子树。
    kp_nodes 语料是全学段 K12-KGraph 导入（小一~高三共 2252 节点），与当前题库
    无关的零题章节不再占视野 —— 树只呈现"有用的知识点"。

    nodes: KpNode 行（需 id/code/name/parent_id/source）；own: code→去重归属题量。
    """
    children_map: dict[object, list] = {}
    for n in nodes:
        children_map.setdefault(getattr(n, 'parent_id', None), []).append(n)

    agg: dict[object, int] = {}

    def agg_count(n) -> int:
        if n.id in agg:
            return agg[n.id]
        total = own.get(n.code, 0)
        for k in children_map.get(n.id, []):
            total += agg_count(k)
        agg[n.id] = total
        return total

    def build(node, depth: int = 0) -> dict:
        kids = children_map.get(node.id, [])
        item: dict = {"id": str(node.id), "name": node.name, "code": node.code, "count": agg_count(node)}
        if kids and depth < 4:
            item["children"] = [build(k, depth + 1) for k in kids]
        return item

    roots_raw = children_map.get(None, [])
    roots = [r for r in roots_raw if agg_count(r) > 0 or getattr(r, 'source', '') == 'teacher']
    return [build(r) for r in roots]


def normalize_authorized_class_items(payload: dict) -> list[dict[str, object]]:
    """归一化 8000 `/api/classes/mine`，只暴露已确认的教学成员班级。

    8100 与 8000 使用不同数据库，8100 本地 ``classes`` 表不能作为教师
    授权目录；没有稳定的 8000 id 时宁可返回空，也不把一个必然在创建会话
    时失败的孤立班级展示给教师。
    """
    data = payload.get("data")
    source = data if isinstance(data, dict) else payload
    raw_items = source.get("items") if isinstance(source, dict) else None
    if not isinstance(raw_items, list):
        return []

    items: list[dict[str, object]] = []
    for row in raw_items:
        if not isinstance(row, dict):
            continue
        role = str(
            row.get("myRole")
            or row.get("my_role")
            or row.get("memberRole")
            or row.get("member_role")
            or ""
        )
        if role not in {"teacher", "owner", "assistant"}:
            continue
        if not bool(row.get("confirmed", True)):
            continue
        class_id = row.get("class_id") or row.get("classId") or row.get("id")
        name = row.get("name") or row.get("class_name") or row.get("className")
        if not class_id or not name:
            continue
        students = row.get("students") or row.get("student_count") or row.get("studentCount")
        if isinstance(students, bool) or not isinstance(students, int) or students < 0:
            students = 0
        items.append({"class_id": str(class_id), "name": str(name), "students": students})
    return items
