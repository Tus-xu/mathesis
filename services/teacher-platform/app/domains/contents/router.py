# coding: utf-8
"""contents 域路由（课件 decks / 教案 plans / 导出登记）。

契约事实源：D:/frontend/src/api/teacherV3.ts `v3Api.decks` / `v3Api.plans`。
业务在 domains/contents/service.py（decks CRUD JSON Patch 语义、plan 确认门 R5、
推课件环节→页型映射、导出登记）。PPTX 导出引擎见 tasks/jobs/export_pptx.py，
当前部署为 API 单进程内异步执行（无需独立 worker 进程）。
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user, get_session
from ...core.errors import ApiError, ErrorCode
from ...core.logging import get_logger
from ...core.response import ok
from ...core.security import UserToken
from . import service as ct
from .models import Deck, DeckTemplate, LessonTemplate, Plan
from .service import ContentsError

router = APIRouter()

logger = get_logger(__name__)

# 进程内导出任务的强引用持有集：asyncio.create_task 的返回值若不持有，任务可能被 GC 提前回收
# （官方文档明确要求保存引用）。done 回调即时丢弃，集合大小 == 在跑的导出行数。
_EXPORT_TASKS: set[asyncio.Task] = set()


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        raise ApiError(ErrorCode.VALIDATION, f"非法 UUID: {value}")


def _uuid_or_none(value) -> uuid.UUID | None:
    """模板等可选 UUID 的宽容解析：非法/空返回 None（调用方降级处理）。"""
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _biz(e: ContentsError) -> ApiError:
    mapping = {40400: ErrorCode.NOT_FOUND, 40402: ErrorCode.NOT_FOUND_ITEM, 40900: ErrorCode.CONFLICT, 42200: ErrorCode.VALIDATION}
    return ApiError(mapping.get(int(e.code), ErrorCode.VALIDATION), str(e))


def _deck_summary(d: Deck) -> dict:
    slides = d.slides if isinstance(d.slides, list) else []
    return {
        "id": str(d.id), "title": d.title, "template_id": str(d.template_id) if d.template_id else "",
        "source": d.source, "slide_count": len(slides),
        "class_name": "", "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _resolve_media(slides: list[dict]) -> list[dict]:
    """image/anchorPhoto 元素 src 为 MinIO key（如例题配图 originals/…/x.png）
    时，读取时解析为短时效 presign GET URL（DB 只存 key 不存 URL，02-ARCHITECTURE §5）。

    已带协议（http/data/blob）的原样返回；解析失败保原值（不静默空白也不崩）。
    """
    import copy

    from ...storage.minio import get_storage

    try:
        storage = get_storage()
    except Exception:
        return slides
    out = copy.deepcopy(slides)
    for slide in out:
        for el in (slide.get("elements") or []):
            if el.get("type") not in ("image", "anchorPhoto"):
                continue
            src = str(el.get("src") or "")
            if not src or src.startswith(("http://", "https://", "data:", "blob:")):
                continue
            try:
                el["src"] = storage.presign_get("teacher-photos", src, expires_seconds=3600)
            except Exception:
                pass
    return out


def _deck_full(d: Deck) -> dict:
    return {
        "id": str(d.id), "title": d.title, "template_id": str(d.template_id) if d.template_id else "",
        "source": d.source, "slides": _resolve_media(d.slides) if isinstance(d.slides, list) else [],
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


async def _deck_full_hydrated(session: AsyncSession, d: Deck) -> dict:
    """返回课件前按模板主题补齐背景层（旧课件也获得模板视觉；编辑器/导出一致）。"""
    from .service import hydrate_deck_backgrounds

    await hydrate_deck_backgrounds(session, d)
    return _deck_full(d)


def _plan_summary(p: Plan) -> dict:
    boards = p.boards if isinstance(p.boards, list) else []
    return {
        "id": str(p.id), "topic": p.topic, "class_id": str(p.class_id) if p.class_id else "",
        "lesson_type": p.lesson_type, "section_count": len(boards),
        "confirmed": bool(p.confirmed),
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _flat_strs(value) -> list[str]:
    """outline 内嵌结构 → 字符串数组（契约 objectives/key_points 口径）。"""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        out: list[str] = []
        for v in value.values():
            out.extend(_flat_strs(v))
        return out
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_flat_strs(v))
        return out
    return []


def _plan_full(p: Plan) -> dict:
    """V3LessonPlan 契约形状（teacherV3.ts:326——sections 为权威键名，审计 #6 键位漂移修复）。"""
    outline = p.outline if isinstance(p.outline, dict) else {}
    raw_boards = p.boards if isinstance(p.boards, list) else []
    sections: list[dict] = []
    board_notes: list[str] = []
    for i, raw in enumerate(raw_boards):
        content = raw.get("content") if isinstance(raw.get("content"), dict) else {}

        def _join(key: str) -> str:
            v = content.get(key)
            if isinstance(v, list):
                return "\n".join(str(x) for x in v)
            if isinstance(v, str):
                return v
            return str(raw.get("goal") or "")

        sid = raw.get("id") or str(uuid.uuid5(uuid.NAMESPACE_URL, f"plan:{p.id}:{i}"))
        # 独立审查 #4：透出检索要点/公式提示/溯源，让教师看到"资料依据"而非只有套话壳
        retrieval: dict[str, object] = {}
        if content.get("key_points_backend"):
            retrieval["key_points"] = [str(x) for x in content["key_points_backend"]]
        if content.get("formula_hint"):
            retrieval["formula_hint"] = str(content["formula_hint"])
        if content.get("source_note"):
            retrieval["source_note"] = str(content["source_note"])
        sections.append({
            "id": sid,
            "name": str(raw.get("name") or content.get("board") or f"板块 {i + 1}"),
            "minutes": int(raw.get("minutes") or 0),
            "teacher_activity": _join("teacher_activity"),
            "student_activity": _join("student_activity"),
            "design_intent": _join("design_intent"),
            "confirmed": bool(raw.get("confirmed", False)),
            **({"retrieval": retrieval} if retrieval else {}),
        })
        meta = content.get("boards_metadata")
        if isinstance(meta, dict):
            for line in meta.get("板书要点") or []:
                board_notes.append(str(line))
    objectives = _flat_strs(outline.get("objectives"))
    key_points = _flat_strs(outline.get("key_points"))
    homework_raw = outline.get("homework")
    homework_tiers = [
        {"tier": str(h.get("tier")), "items": [str(x) for x in (h.get("items") or [])]}
        for h in homework_raw if isinstance(h, dict)
    ] if isinstance(homework_raw, list) else []
    return {
        "id": str(p.id),
        "topic": p.topic,
        "class_id": str(p.class_id) if p.class_id else "",
        "lesson_type": p.lesson_type,
        "template_id": "",
        "objectives": objectives,
        "key_points": key_points,
        "sections": sections,
        "board_design_note": "\n".join(board_notes),
        "homework_tiers": homework_tiers,
        "refs": _flat_strs(outline.get("refs")) or _flat_strs(outline.get("missing_evidence")),
        "confirmed": bool(p.confirmed),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


# ---------- decks ----------

@router.get("/decks")
async def list_decks(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await ct.list_decks(session, owner_id=user.id))
    return ok({"items": [_deck_summary(d) for d in rows]}, request_id=request.state.request_id)


@router.get("/decks/{deck_id}")
async def get_deck(request: Request, deck_id: str, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        d = await ct.get_deck(session, _uuid(deck_id))
    except ContentsError as e:
        raise _biz(e)
    return ok(await _deck_full_hydrated(session, d), request_id=request.state.request_id)


@router.patch("/decks/{deck_id}")
async def patch_deck(request: Request, deck_id: str, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _assert_deck_owner(session, deck_id, user)
    try:
        d = await ct.patch_deck(session, _uuid(deck_id), title=body.get("title"), template_id=_uuid(body["template_id"]) if body.get("template_id") else None)
        await session.commit()
        await session.refresh(d)
    except ContentsError as e:
        raise _biz(e)
    return ok(await _deck_full_hydrated(session, d), request_id=request.state.request_id)


@router.post("/decks/{deck_id}/slides")
async def add_slide(request: Request, deck_id: str, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _assert_deck_owner(session, deck_id, user)
    slide = body.get("slide")
    if not isinstance(slide, dict):
        raise ApiError(ErrorCode.VALIDATION, "slide 必填（对象）")
    try:
        d = await ct.add_slide(session, _uuid(deck_id), slide, after_id=body.get("after_id"))
        await session.commit()
        await session.refresh(d)
    except ContentsError as e:
        raise _biz(e)
    return ok({"deck": await _deck_full_hydrated(session, d)}, request_id=request.state.request_id)


@router.delete("/decks/{deck_id}/slides/{slide_id}")
async def delete_slide(request: Request, deck_id: str, slide_id: str, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _assert_deck_owner(session, deck_id, user)
    try:
        d = await ct.delete_slide(session, _uuid(deck_id), slide_id)
        await session.commit()
        await session.refresh(d)
    except ContentsError as e:
        raise _biz(e)
    return ok({"deck": _deck_full(d)}, request_id=request.state.request_id)


@router.patch("/decks/{deck_id}/slides/{slide_id}")
async def patch_slide(request: Request, deck_id: str, slide_id: str, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """元素级编辑（双语义，前端契约优先）：
    - body 含 `ops` 数组 → JSON Patch（RFC6902 子集）
    - 否则按前端契约 Partial<V3Slide> 部分合并（{elements/notes/anchor_bar/layout}）
    撤销审计均落 slide_patch_logs。"""
    await _assert_deck_owner(session, deck_id, user)
    try:
        if isinstance(body.get("ops"), list):
            d = await ct.patch_slide(session, _uuid(deck_id), slide_id, body["ops"], modifier="teacher")
        else:
            d = await ct.patch_slide_fields(session, _uuid(deck_id), slide_id, body, modifier="teacher")
        await session.commit()
        await session.refresh(d)
    except ContentsError as e:
        raise _biz(e)
    return ok({"deck": await _deck_full_hydrated(session, d)}, request_id=request.state.request_id)


@router.post("/decks/{deck_id}/export")
async def export_deck(request: Request, deck_id: str, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """导出：登记 export_jobs + tasks 行，并在同一进程内异步执行 PPTX 生成引擎。

    部署形态：API 单进程 Uvicorn 常驻（未随服务启动独立 worker 进程），故任务不依赖
    TaskIQ worker 消费，直接 create_task 在本进程事件循环执行；worker 模式启用后
    可移除该 create_task，交由 broker 队列消费（file_key 带时间戳，重复执行不覆盖）。
    前端通过 GET /export-jobs/{job_id} 轮询 download_url。
    """
    await _assert_deck_owner(session, deck_id, user)
    fmt = body.get("format") or "pptx"
    if fmt not in ("pptx", "pdf"):
        raise ApiError(ErrorCode.VALIDATION, "format 仅支持 pptx/pdf")
    try:
        job = await ct.register_export(session, _uuid(deck_id), fmt=fmt)
        await session.commit()
    except ContentsError as e:
        raise _biz(e)
    from ...tasks.service import TaskService

    try:
        task = await TaskService.submit(
            "export_pptx" if fmt == "pptx" else "export_pdf",
            {"export_job_id": str(job.id), "deck_id": str(job.deck_id), "format": fmt},
            user.id,
        )
    except Exception as e:  # worker 队列不可用：诚实报错
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, f"导出任务提交失败：{e}")
    # TODO(reliability-job-engine): 已知架构偏差 / KNOWN ARCHITECTURAL DEVIATION——
    # app/reliability/__init__.py:17 明确「PPT 长任务禁止进 FastAPI BackgroundTasks/进程内，统一走
    # outbox→stream→worker 路径」。此处仍以本进程 create_task 执行 PPTX 导出（worker 未常驻的过渡形态）。
    # 收敛工作包：改走 TransactionalOutbox 派发 + run_task 消费者，并删掉下面的进程内调度分支。
    try:
        from ...core.events import get_publisher
        from ...tasks.jobs.export_pptx import export_pptx as _run_export_pptx

        _task = asyncio.create_task(_run_export_pptx(str(task["id"]), publisher=get_publisher(), user_id=str(user.id)))
        _EXPORT_TASKS.add(_task)
        _task.add_done_callback(_EXPORT_TASKS.discard)
    except Exception as e:  # noqa: BLE001 —— 调度层失败：响亮失败，不静默吞
        # 此前为 `except: pass`：调度失败（引擎 import 异常/参数求值异常）会让任务永远停在 pending、
        # 无日志、无错误返回，教师侧只看到一直「导出中」。改为记录 + 任务终态置 failed（任务中心可见）。
        logger.exception("导出任务调度失败（进程内 create_task）", task_id=str(task["id"]))
        try:
            await TaskService.finish(str(task["id"]), "failed", error=f"导出调度失败：{e}"[:500])
        except Exception:  # noqa: BLE001 —— 终态回写本身失败不能再吞掉上一条错误
            logger.exception("导出任务失败态回写失败", task_id=str(task["id"]))
    return ok({"task_id": task["id"], "job_id": str(job.id)}, request_id=request.state.request_id)


# ---------- plans ----------

@router.get("/plans")
async def list_plans(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    from sqlalchemy import select

    # 越权隔离（独立审查 #8）：仅返回当前教师自己的教案，NULL owner 旧数据不定向暴露
    rows = (await session.execute(
        select(Plan).where(Plan.created_by == user.id).order_by(Plan.updated_at.desc()).limit(100)
    )).scalars().all()
    return ok({"items": [_plan_summary(p) for p in rows]}, request_id=request.state.request_id)


@router.get("/plans/{plan_id}")
async def get_plan(request: Request, plan_id: str, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    p = (await _get_plan(session, plan_id))
    return ok(_plan_full(p), request_id=request.state.request_id)


@router.patch("/plans/{plan_id}")
async def patch_plan(request: Request, plan_id: str, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    p = await _assert_plan_owner(session, plan_id, user)
    await ct.assert_plan_mutable(p)  # confirmed 后对 AI/编辑只读（R5 确认门）
    if "topic" in body:
        p.topic = str(body["topic"])
    if "lesson_type" in body:
        p.lesson_type = str(body["lesson_type"])
    sections = body.get("sections") if "sections" in body else body.get("boards")
    if isinstance(sections, list):
        # 合并保存（独立审查 #2a/#4）：前端编辑只带本环节改动的板名/时长/目标/师生活动，
        # 后端按 id 合并既有 content，保留 key_points_backend/source_note/formula_hint/boards_metadata
        existing = {str(b.get("id")): b for b in (p.boards if isinstance(p.boards, list) else [])}
        merged = []
        for i, b in enumerate(sections):
            prev = existing.get(str(b.get("id")))
            old_content = prev.get("content") if isinstance(prev, dict) and isinstance(prev.get("content"), dict) else {}
            new_content = b.get("content") if isinstance(b.get("content"), dict) else {}
            content = {**old_content, **new_content}
            item = {**b, "content": content}
            if not item.get("id"):
                item["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"plan:{p.id}:{i}"))
            merged.append(item)
        p.boards = merged
    if "outline" in body:
        p.outline = body["outline"]
    await session.commit()
    await session.refresh(p)
    return ok(_plan_full(p), request_id=request.state.request_id)


@router.get("/export-jobs/{job_id}")
async def export_job_status(request: Request, job_id: str, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """导出任务状态轮询（M2-B）：done 时返回 presign GET 下载地址（teacher-exports）。"""
    from ...db.models import ExportJobs
    from ...storage.minio import get_storage

    job = await session.get(ExportJobs, _uuid(job_id))
    if job is None:
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "导出任务不存在")
    download_url = ""
    if job.status == "done" and job.file_key:
        try:
            download_url = get_storage().presign_get("teacher-exports", job.file_key, expires_seconds=1800)
        except Exception:
            download_url = ""
    return ok({
        "job_id": str(job.id), "status": job.status, "format": job.format,
        "file_key": job.file_key, "download_url": download_url, "error": job.error,
    }, request_id=request.state.request_id)


@router.post("/decks/import-pptx")
async def import_pptx(request: Request, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """老师上传自制 pptx → 逐页拆元素 → 可二次编辑的 deck 草稿（用户点名能力）。

    前置：文件先经 POST /upload/presign 直传（key 形如 uploads/{uid}/xxx.pptx）。
    全部产物 teacher_confirmed=False（R5 草稿态）；表格/图表等 v1 不拆项如实列入 report.skipped。
    """
    file_key = str(body.get("file_key") or "")
    if not file_key:
        raise ApiError(ErrorCode.VALIDATION, "file_key 必填（先经 /upload/presign 直传 pptx）")
    from ...storage.minio import get_storage

    storage = get_storage()
    try:
        data = storage.get_bytes("teacher-assets", file_key)
    except Exception as e:
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, f"读取上传文件失败：{e}")
    if not data:
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "上传文件为空")

    from .pptx_import import parse_pptx

    try:
        parsed = parse_pptx(data, storage=storage, owner_key=str(user.id)[:8])
    except Exception as e:
        raise ApiError(ErrorCode.VALIDATION, f"pptx 解析失败（文件可能损坏或非 pptx）：{e}")

    title = str(body.get("title") or "").strip() or "导入课件"
    try:
        deck = await ct.create_imported_deck(session, title=title, slides=parsed["slides"],
                                             created_by=_uuid(user.id))
        await session.commit()
    except ContentsError as e:
        raise _biz(e)
    return ok({
        "deck_id": str(deck.id), "title": deck.title,
        "report": parsed["report"],
    }, request_id=request.state.request_id)


@router.post("/plans/{plan_id}/confirm")
async def confirm_plan(request: Request, plan_id: str, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    await _assert_plan_owner(session, plan_id, user)
    try:
        p = await ct.confirm_plan(session, _uuid(plan_id))
        await session.commit()
    except ContentsError as e:
        raise _biz(e)
    return ok(_plan_full(p), request_id=request.state.request_id)


@router.post("/plans/{plan_id}/push-to-deck")
async def push_to_deck(request: Request, plan_id: str, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """教案直通课件（已确认教案；环节→页型映射；模板支持 slug/name/UUID 三种形式）。"""
    await _assert_plan_owner(session, plan_id, user)
    template_id = body.get("template_id")
    if not template_id:
        raise ApiError(ErrorCode.VALIDATION, "template_id 必填")
    # 独立审查 #2b：前端常传 slug（如 tpl-academic-blue）而非 UUID——解析到真实模板或降级为空槽
    template_uuid = _uuid_or_none(template_id)
    if template_uuid is None:
        from sqlalchemy import select as _sel
        slug = str(template_id).strip().lower().replace("tpl-", "").replace("-", "")
        mapped = {
            "academicblue": "学术蓝", "blackboardgreen": "黑板绿", "geometrygray": "几何灰",
            "classicnavy": "经典藏青", "minimalwhite": "极简白", "warmorange": "暖橙活力",
        }
        row = (
            await session.execute(
                _sel(DeckTemplate).where(
                    (DeckTemplate.name == str(template_id))
                    | (DeckTemplate.name == mapped.get(slug, "__none__"))
                )
            )
        ).scalars().first()
        if row is not None:
            template_uuid = row.id
    try:
        deck = await ct.push_to_deck(session, _uuid(plan_id), template_id=template_uuid, created_by=_uuid(user.id))
        await session.commit()
    except ContentsError as e:
        raise _biz(e)
    return ok({"deck_id": str(deck.id)}, request_id=request.state.request_id)


def _assert_owner(created_by, user) -> None:
    """资源级授权（审计 #9）：created_by 非空的资源仅属主可变更；NULL=公共资产（单租户共享）。"""
    if created_by is not None and str(created_by) != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "该资源由另一位教师创建，不可变更")


async def _assert_plan_owner(session: AsyncSession, plan_id: str, user) -> Plan:
    p = await _get_plan(session, plan_id)
    _assert_owner(p.created_by, user)
    return p


async def _assert_deck_owner(session: AsyncSession, deck_id: str, user) -> None:
    d = await ct.get_deck(session, _uuid(deck_id))
    _assert_owner(d.created_by, user)


async def _get_plan(session: AsyncSession, plan_id: str) -> Plan:
    p = await session.get(Plan, _uuid(plan_id))
    if p is None:
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "教案不存在")
    return p
