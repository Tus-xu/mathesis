# coding: utf-8
"""contents 域业务服务（M2-B · Gate 2 支撑，L4 数据面）。

- decks CRUD：slides/boards 存 JSONB；slide PATCH 走 JSON Patch（RFC 6902 子集）语义，
  元素级 diff 写 slide_patch_logs（撤销审计；modifier 恒 'teacher'——AI 无写路径，R5）。
- plans/confirm：置 confirmed=true 后对 AI 只读（生成域改动必须草稿副本）。
- push-to-deck：教案环节 → 页型槽位映射（B4 规范 §3.2），产出真实草稿 deck 行。
- export：登记 export_jobs（渲染本体与 worker 归 B3，M2-B 集成）。
"""
from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.contents.models import Deck, ExportJob, Plan, SlidePatchLog

# 教案十板块 → V3SlideLayout 页型映射（环节→页型槽位，§3.2）
BOARD_LAYOUT_MAP: dict[str, str] = {
    '课标与学情': 'blank',
    '教学目标': 'keypoints',
    '复习引入': 'review',
    '新知探究': 'definition',
    '例题精讲': 'example',
    '变式训练': 'variation',
    '易错辨析': 'review',
    '课堂小结与检测': 'summary',
    '分层作业': 'keypoints',
    '教学反思与板书': 'blank',
}


class ContentsError(Exception):
    def __init__(self, message: str, code: int):
        self.code = code
        super().__init__(message)


def _not_found(kind: str) -> ContentsError:
    return ContentsError(f'{kind}不存在', 40402)


# ── decks CRUD ──────────────────────────────────────────────────────────────

async def list_decks(session: AsyncSession, *, owner_id: UUID | None = None) -> list[Deck]:
    # 越权隔离（独立审查 #8）：按创建者过滤；owner_id 缺省时不含任何文档（不允许匿名看到全校课件）
    q = select(Deck).order_by(Deck.updated_at.desc())
    if owner_id is not None:
        q = q.where(Deck.created_by == owner_id)
    else:
        q = q.where(Deck.created_by.is_(None))  # 兜底：无身份时绝不返回全部
    return list((await session.execute(q)).scalars())


async def get_deck(session: AsyncSession, deck_id: UUID) -> Deck:
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise _not_found('课件')
    return deck


async def hydrate_deck_backgrounds(session: AsyncSession, deck: Deck) -> None:
    """读取时为每页补齐模板背景 image 层（z=0，1280×720 dataURI PNG）。

    旧课件/无主题课件生成时可能缺背景层（编辑器/导出都是白底）；此处按
    deck.template_id → 模板 slots._theme.bg_datauri 在内存补充：编辑器渲染与
    PPTX 导出同时获得模板视觉。不落库、幂等——已有背景层（image & z=0）跳过。
    未绑定模板的课件使用默认模板（首个模板）主题，杜绝纯白裸页。
    """
    if deck is None or not isinstance(deck.slides, list):
        return
    from sqlalchemy import select

    from .models import DeckTemplate

    stmt = select(DeckTemplate)
    if deck.template_id is not None:
        stmt = stmt.where(DeckTemplate.id == deck.template_id)
    else:
        # 未绑定模板的课件：优先"学术蓝"（颜色明显，视觉不裸白）
        stmt = select(DeckTemplate).order_by((DeckTemplate.name == "学术蓝").desc(), DeckTemplate.id).limit(1)
    row = (await session.execute(stmt)).scalar_one_or_none()
    theme = {}
    if row is not None and isinstance(row.slots, dict):
        theme = row.slots.get("_theme") or {}
    bg = theme.get("bg_datauri") if isinstance(theme, dict) else None
    if not isinstance(bg, str) or not bg.startswith("data:image"):
        # DB 缺 _theme（老模板 / 未补挂）：按模板名现算主题，保证读取即有模板视觉
        from .templates_seed import _STYLE_THEMES, _bg_datauri

        name = str(getattr(row, "name", "") or "") if row is not None else "学术蓝"
        base = _STYLE_THEMES.get(name) or _STYLE_THEMES["学术蓝"]
        theme = {**base, "bg_datauri": _bg_datauri(name)}
        bg = theme.get("bg_datauri") or ""
    if not isinstance(bg, str) or not bg.startswith("data:image"):
        return
    for slide in deck.slides:
        if not isinstance(slide, dict):
            continue
        els = slide.get("elements")
        if not isinstance(els, list):
            continue
        if any(isinstance(e, dict) and e.get("type") == "image" and e.get("z") == 0 for e in els):
            continue
        sid = str(slide.get("id") or "s")
        els.insert(0, {
            "id": f"bg-{sid[:8]}", "type": "image",
            "left": 0, "top": 0, "width": 1280, "height": 720, "z": 0,
            "src": bg, "alt": "模板背景",
        })


async def patch_deck(session: AsyncSession, deck_id: UUID, *, title: str | None = None, template_id: UUID | None = None) -> Deck:
    deck = await get_deck(session, deck_id)
    if title is not None:
        deck.title = title
    if template_id is not None:
        deck.template_id = template_id
    return deck


async def add_slide(session: AsyncSession, deck_id: UUID, slide: dict, *, after_id: str | None = None) -> Deck:
    deck = await get_deck(session, deck_id)
    slides = list(deck.slides or [])
    idx = next((i + 1 for i, s in enumerate(slides) if s.get('id') == after_id), len(slides))
    slides.insert(idx, slide)
    deck.slides = slides
    await _log(session, deck_id, slide.get('id', ''), [{'op': 'add', 'path': '/slides', 'value': slide.get('id')}])
    return deck


async def delete_slide(session: AsyncSession, deck_id: UUID, slide_id: str) -> Deck:
    deck = await get_deck(session, deck_id)
    before = len(deck.slides or [])
    deck.slides = [s for s in (deck.slides or []) if s.get('id') != slide_id]
    if len(deck.slides) == before:
        raise ContentsError('页不存在', 40402)
    await _log(session, deck_id, slide_id, [{'op': 'remove', 'path': f'/slides/{slide_id}'}])
    return deck


async def patch_slide(session: AsyncSession, deck_id: UUID, slide_id: str, ops: list[dict], *, modifier: str = 'teacher') -> Deck:
    """JSON Patch（RFC 6902 子集：add/replace/remove，指针 /elements/i/field）。

    teacher_confirmed 元素：教师可改（记 modifier='teacher'）；AI 无任何写路径——
    本函数不接受 modifier != 'teacher' 的调用（R5 红线，生成域改稿走草稿副本）。
    """
    if modifier != 'teacher':
        raise ContentsError('AI 无直写旁路：改稿请走草稿副本（R5）', 40302)
    deck = await get_deck(session, deck_id)
    slides = copy.deepcopy(list(deck.slides or []))
    target = next((s for s in slides if s.get('id') == slide_id), None)
    if target is None:
        raise ContentsError('页不存在', 40402)
    for op in ops:
        _apply_op(target, op)
    deck.slides = slides
    await _log(session, deck_id, slide_id, ops)
    return deck


async def patch_slide_fields(session: AsyncSession, deck_id: UUID, slide_id: str, fields: dict, *, modifier: str = 'teacher') -> Deck:
    """部分合并语义（前端契约：Partial<V3Slide>，teacherV3.ts:177）——
    {elements/notes/anchor_bar/layout} 直接替换对应字段。JSON Patch 路线见 patch_slide(ops)。
    审计 #6：此前后端只认 ops，前端 {elements} 必 422 半提交（标题落库元素丢失）。"""
    if modifier != 'teacher':
        raise ContentsError('AI 无直写旁路：改稿请走草稿副本（R5）', 40302)
    allowed = ('elements', 'notes', 'anchor_bar', 'layout', 'fill_rate')
    patch = {k: fields[k] for k in allowed if k in fields}
    if not patch:
        raise ContentsError('无可应用的字段（允许：elements/notes/anchor_bar/layout/fill_rate）', 42200)
    deck = await get_deck(session, deck_id)
    slides = copy.deepcopy(list(deck.slides or []))
    target = next((s for s in slides if s.get('id') == slide_id), None)
    if target is None:
        raise ContentsError('页不存在', 40402)
    target.update(patch)
    deck.slides = slides
    ops = [{'op': 'replace', 'path': f'/{k}', 'value': patch[k]} for k in patch]
    await _log(session, deck_id, slide_id, ops)
    return deck


def _apply_op(slide: dict, op: dict) -> None:
    kind, path = op.get('op'), str(op.get('path', ''))
    if kind not in ('add', 'replace', 'remove'):
        raise ContentsError(f'不支持的 JSON Patch op：{kind!r}', 42206)
    parts = [p for p in path.split('/') if p != '']
    if not parts:
        raise ContentsError('空指针', 42206)
    node: Any = slide
    for key in parts[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    last = parts[-1]
    if kind == 'remove':
        if isinstance(node, list):
            del node[int(last)]
        else:
            node.pop(last, None)
    elif kind == 'add':
        if isinstance(node, list):
            node.append(op['value'])
        else:
            node[last] = op['value']
    else:  # replace
        if isinstance(node, list):
            node[int(last)] = op['value']
        else:
            if last not in node:
                raise ContentsError(f'replace 目标不存在：{path}', 42206)
            node[last] = op['value']


async def _log(session: AsyncSession, deck_id: UUID, slide_id: str, ops: list[dict]) -> None:
    session.add(SlidePatchLog(deck_id=deck_id, slide_id=slide_id, patch=ops, modifier='teacher'))


# ── plans：确认（AI 只读门）与推课件 ────────────────────────────────────────

async def create_imported_deck(session: AsyncSession, *, title: str, slides: list[dict], created_by: UUID) -> Deck:
    """pptx 导入产物落库（source='导入'，草稿态——元素自带 teacher_confirmed=False）。"""
    row = Deck(title=title, template_id=None, source='导入', class_id=None,
               slides=slides, created_by=created_by)
    session.add(row)
    await session.flush()
    return row


async def confirm_plan(session: AsyncSession, plan_id: UUID) -> Plan:
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise _not_found('教案')
    plan.confirmed = True
    await session.commit()
    # 防 MissingGreenlet：commit 后属性可能过期，_plan_full 同步读触发异步重载时报错——先刷新
    await session.refresh(plan)
    return plan


async def assert_plan_mutable(plan: Plan) -> None:
    """confirmed 后对 AI 只读（R5）：生成域想改必须出草稿副本。"""
    if plan.confirmed:
        raise ContentsError('教案已确认定稿：AI 只读，请生成草稿副本后再改（R5）', 40303)


async def push_to_deck(session: AsyncSession, plan_id: UUID, *, template_id: UUID, created_by: UUID) -> Deck:
    """教案直通课件：环节→页型槽位映射，产出真实草稿 deck 行（替换 mock 克隆 fixture）。"""
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise _not_found('教案')
    if not plan.confirmed:
        raise ContentsError('教案未确认：请先 confirm 再推送为课件', 42207)
    slides: list[dict] = []
    for i, board in enumerate(plan.boards or []):
        layout = BOARD_LAYOUT_MAP.get(str(board.get('name', '')), 'blank')
        html = str(board.get('name', f'环节 {i + 1}'))
        slides.append({
            'id': f'bd-{i}', 'layout': layout,
            'elements': [{
                'id': f'bd-{i}-e0', 'type': 'text', 'left': 72, 'top': 52,
                'width': 1136, 'height': 64, 'z': 1, 'teacher_confirmed': False,
                'html': html, 'font_size': 32,
            }],
        })
    deck = Deck(title=f'{plan.topic} · 教案直通', template_id=template_id,
                source='lesson-push', class_id=plan.class_id, slides=slides, created_by=created_by)
    session.add(deck)
    return deck


# ── 导出登记（渲染本体/worker 归 B3）────────────────────────────────────────

async def register_export(session: AsyncSession, deck_id: UUID, *, fmt: str) -> ExportJob:
    if fmt not in ('pptx', 'pdf'):
        raise ContentsError(f'不支持的导出格式：{fmt!r}', 42208)
    await get_deck(session, deck_id)  # 存在性校验
    job = ExportJob(deck_id=deck_id, format=fmt, status='queued')
    session.add(job)
    return job
