# coding: utf-8
"""catalog 域路由（今日工作台 / 班级 / 题库 / 知识点树 / 专题夹 / 模板 / 资源 / 洞察）。

契约事实源：frontend/src/api/teacherV3.ts `v3Api.catalog`。
响应壳 ok()/鉴权 get_current_user 由 B3 core 提供；本文件只做「解析请求 → 调服务/DB → 包壳」。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...core.deps import get_current_user, get_session
from ...core.errors import ApiError, ErrorCode
from ...core.response import ok
from ...core.security import UserToken
from ...db.models import Classes, Tasks, Users
from ...storage.minio import get_storage
from ...teacher_prep_v2.adapters.api8000 import HttpxApiTransport
from .models import Folder, FolderQuestionRef, KpNode, QuizQuestion, Textbook
from .service import (
    QuizQueryFilters,
    build_classes_query,
    build_kp_tree,
    build_quiz_query,
    normalize_authorized_class_items,
)

router = APIRouter()


# ---------- 序列化 ----------

def _presign_quiz_image(value: str | None) -> str | None:
    """题图取 URL：DB 里 `stem_image`/`solution_image` 存的是 **MinIO key**（R8 约定，
    `originals/{user_id}/{uuid}.png`，桶 teacher-photos）。

    ⚠️ 不能用 presigned GET：服务器上 `teacher-minio` 只在 compose 内网，
    presign 出来的 URL 主机是 `teacher-minio:9000`，**浏览器加载不到**。
    改走 **鉴权代理路由** `/quiz/image?key=...`（8100 内部取 MinIO 再流式回给浏览器），
    前端用「fetch 带 Bearer → blob → objectURL」渲染（与 SliceFigure 既有模式一致）。
    data:/http(s)/blob: 开头的值原样返回（兼容历史脏数据）。"""
    if not value:
        return None
    v = str(value)
    if v.startswith(("http://", "https://", "data:", "blob:")):
        return v
    return f"/api/teacher-v3/quiz/image?key={quote(v, safe='')}"


def _question_to_contract(row: QuizQuestion, kp_name: str | None = None, folder_refs: list[str] | None = None) -> dict:
    return {
        "id": str(row.id),
        "kp_name": kp_name or "",
        "kp_code": row.kp_code,
        "kp_path": list(row.kp_path or []),
        "q_type": row.q_type,
        "difficulty": row.difficulty,
        "stem_latex": row.stem_latex,
        "stem_image": _presign_quiz_image(row.stem_image),
        "options": list(row.options) if row.options is not None else None,
        "answer": row.answer,
        "solution_image": _presign_quiz_image(row.solution_image),
        "source": row.source,
        "year": row.year,
        "analysis": row.analysis,
        "folder_refs": folder_refs or [],
        "usage_count": row.usage_count,
        "teacher_confirmed": row.teacher_confirmed,
    }


async def _kp_names(session: AsyncSession, codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    rows = (await session.execute(select(KpNode.code, KpNode.name).where(KpNode.code.in_(codes)))).all()
    return {c: n for c, n in rows}


# ---------- 今日工作台（L1） ----------

@router.get("/today")
async def today(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """今日工作台：teacher/classes 来自 DB 真实行；todos 来自任务中心真实行。

    schedule（课表）当前无真实数据源（课程表功能未建），诚实返回空数组——
    不用 mock 假课表冒充（红线 6 / L1 判定：数据必须 SQL 对得上）。
    """
    try:
        user_uuid = uuid.UUID(user.id)
    except (ValueError, AttributeError, TypeError):
        user_uuid = None

    teacher_name = user.name
    if user_uuid is not None:
        row = (await session.execute(select(Users).where(Users.id == user_uuid))).scalar_one_or_none()
        if row is not None:
            teacher_name = row.name

    classes = (await session.execute(build_classes_query())).scalars().all()

    todos: list[dict] = []
    running = 0
    if user_uuid is not None:
        task_rows = (
            await session.execute(
                select(Tasks).where(Tasks.user_id == user_uuid).order_by(Tasks.created_at.desc()).limit(20)
            )
        ).scalars().all()
        for t in task_rows:
            if t.status == "running":
                running += 1
            if t.status in ("pending", "running"):
                todos.append({
                    "id": str(t.id),
                    "time": (t.created_at or datetime.now()).strftime("%H:%M"),
                    "text": f"任务进行中：{t.type}（{t.progress}%）",
                    "kind": "review",
                })

    return ok({
        "teacher": {"name": teacher_name, "subject": "数学", "grade_group": "高中"},
        "schedule": [],
        "todos": todos,
        "class_brief": [
            {
                "class_id": str(c.id),
                "name": c.name,
                "students": c.student_count or 0,
            }
            for c in classes
        ],
    }, request_id=request.state.request_id)


@router.get("/classes")
async def list_classes(request: Request, user: UserToken = Depends(get_current_user)) -> dict:
    """教师备课班级目录：唯一读取 8000 的当前确认成员关系。"""
    token = request.headers.get("Authorization", "")
    bearer = token[7:] if token.startswith("Bearer ") else ""
    transport = HttpxApiTransport(get_settings().api8000_base_url)
    try:
        status, body = await transport.request("GET", "/api/classes/mine", token=bearer)
    except Exception as exc:  # noqa: BLE001 - transport 将网络错误归一为权威不可达
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, f"8000 身份与班级服务暂时不可用：{exc}") from exc

    if status == 401:
        raise ApiError(ErrorCode.UNAUTHORIZED, "统一登录已失效，请重新登录")
    if status == 403:
        raise ApiError(ErrorCode.FORBIDDEN, "当前教师身份未获班级目录授权")
    if status >= 500:
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, f"8000 身份与班级服务暂时不可用（HTTP {status}）")
    if status != 200 or body is None:
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, f"8000 班级目录响应异常（HTTP {status}）")

    code = body.get("code") if isinstance(body, dict) else None
    if isinstance(code, int) and code != 0:
        if 40100 <= code < 40200:
            raise ApiError(ErrorCode.UNAUTHORIZED, str(body.get("message") or "统一登录已失效，请重新登录"))
        if 40300 <= code < 40400:
            raise ApiError(ErrorCode.FORBIDDEN, str(body.get("message") or "当前教师身份未获班级目录授权"))
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, str(body.get("message") or "8000 班级目录暂不可用"))
    return ok({"items": normalize_authorized_class_items(body)}, request_id=request.state.request_id)


# ---------- 题库 ----------

@router.get("/quiz/questions")
async def list_quiz_questions(
    request: Request,
    kp: str | None = None,
    q: str | None = None,
    difficulty: str | None = None,
    q_type: str | None = None,
    source: str | None = None,
    folder: str | None = None,
    year: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    filters = QuizQueryFilters(
        kp=[s.strip() for s in kp.split(",") if s.strip()] if kp else [],
        q=q or "",
        difficulty=difficulty or "",
        q_type=q_type or "",
        source=source or "",
        folder=uuid.UUID(folder) if folder else None,
        year=year or "",
        limit=limit,
        offset=offset,
    )
    stmt, count_stmt = build_quiz_query(filters)
    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar_one()
    # kp_code 在 B2 真实语料中允许 NULL（785/788 行未挂知识点——RESCUE_STATE 数据层缺陷），
    # 统计与序列化必须 None 防御（审计 #7：sorted(None) 500）
    codes = sorted({r.kp_code for r in rows if r.kp_code})
    names = await _kp_names(session, codes)
    # 专题夹引用：此前恒回 []，导致前端按专题夹过滤必然空列表（用户实测「点夹子没题」）
    folder_map: dict[uuid.UUID, list[str]] = {}
    if rows:
        ref_rows = (
            await session.execute(
                select(FolderQuestionRef.folder_id, FolderQuestionRef.question_id).where(
                    FolderQuestionRef.question_id.in_([r.id for r in rows])
                )
            )
        ).all()
        for fid, qid in ref_rows:
            folder_map.setdefault(qid, []).append(str(fid))
    return ok({
        "items": [
            _question_to_contract(r, names.get(r.kp_code) if r.kp_code else "", folder_map.get(r.id) or [])
            for r in rows
        ],
        "total": total,
    }, request_id=request.state.request_id)


@router.post("/quiz/questions")
async def create_quiz_question(
    request: Request,
    body: dict,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """自编题录入（V3.1）：结构化手输，source='自编'，teacher_confirmed=True（教师亲手录入即确认）。

    支持选填 `stem_image`（MinIO key，经 /upload/file 上传后返回）——
    教师常需要「题干文字 + 几何图形」同时呈现（用户实测：四棱锥题只有文字没图没法用）。
    """
    required = ("kp_code", "kp_name", "q_type", "difficulty", "stem_latex", "answer")
    missing = [k for k in required if not body.get(k)]
    if missing:
        raise ApiError(ErrorCode.VALIDATION, f"缺少必填字段: {', '.join(missing)}")
    if body["q_type"] not in ("choice", "fill", "solve", "image"):
        raise ApiError(ErrorCode.VALIDATION, "自编题 q_type 仅支持 choice/fill/solve/image")
    stem_image = (body.get("stem_image") or "").strip() or None
    if stem_image and stem_image.startswith(("data:", "blob:")):
        # 历史事故：上传失败时前端把整个 base64 dataURL 塞进来，库里留下一批裂图垃圾行
        raise ApiError(ErrorCode.VALIDATION, "题图未成功上传到对象存储，请重新上传图片后再录入")
    row = QuizQuestion(
        kp_code=body["kp_code"],
        kp_path=list(body.get("kp_path") or [body["kp_code"]]),
        q_type=body["q_type"],
        difficulty=body["difficulty"],
        stem_latex=body["stem_latex"],
        stem_plain=_strip_latex(body["stem_latex"]),
        stem_image=stem_image,
        options=body.get("options"),
        answer=body["answer"],
        analysis=body.get("analysis"),
        source="自编",
        teacher_confirmed=True,
        created_by=_uuid_or_none(user.id),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ok(_question_to_contract(row, body.get("kp_name")), request_id=request.state.request_id)


@router.delete("/quiz/questions/{question_id}")
async def delete_quiz_question(
    request: Request,
    question_id: str,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = (await session.execute(select(QuizQuestion).where(QuizQuestion.id == _uuid(question_id)))).scalar_one_or_none()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "题目不存在")
    # 归属校验（审计 #9）：他人录入的题不可删（created_by NULL=区库公共资产，单租户可管理）
    if row.created_by is not None and str(row.created_by) != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "该题由另一位教师录入，不可删除")
    await session.delete(row)
    await session.commit()
    return ok({"ok": True}, request_id=request.state.request_id)


@router.post("/quiz/scan-import")
async def quiz_scan_import(
    request: Request,
    body: dict,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """扫描入库（P4）：拍照/扫描题 → 入库（图片题型挂原图 key，原图锚定 R8）。"""
    for key in ("src", "kp_code", "kp_name"):
        if not body.get(key):
            raise ApiError(ErrorCode.VALIDATION, f"缺少必填字段: {key}")
    as_image = bool(body.get("as_image"))
    # 2026-10-05 修复「拍照入库产出一批裂图垃圾行」：src 必须是对象存储 key。
    # 此前前端直传 MinIO 失败（线上 minio 只在内网 + CORS）后会回落成 base64 dataURL
    # 塞进 stem_image，浏览器无法渲染 → 首屏全是裂图 + 空题干，教师感知「题库坏了」。
    src = str(body["src"])
    if src.startswith(("data:", "blob:", "http://", "https://")):
        raise ApiError(
            ErrorCode.VALIDATION,
            "题图没有成功上传到对象存储，请重新选择/拍摄图片后再入库",
        )
    solution_src = body.get("solution_src")
    if solution_src and str(solution_src).startswith(("data:", "blob:")):
        solution_src = None
    row = QuizQuestion(
        kp_code=body["kp_code"],
        kp_path=list(body.get("kp_path") or [body["kp_code"]]),
        q_type="image" if as_image else "solve",
        difficulty=body.get("difficulty", "medium"),
        stem_latex=body.get("stem_latex", ""),
        stem_image=src,
        solution_image=solution_src,
        answer="",
        source="拍照入库",
        teacher_confirmed=False,
        created_by=_uuid_or_none(user.id),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ok(_question_to_contract(row, body.get("kp_name")), request_id=request.state.request_id)


# ---------- 知识点树 ----------

@router.get("/quiz/image")
async def quiz_image(
    request: Request,
    key: str,
    user: UserToken = Depends(get_current_user),
) -> Response:
    """题图代理（2026-10-05）：`stem_image`/`solution_image` 存的是 MinIO key，
    而服务器上 `teacher-minio` 只在 compose **内网**（无宿主端口映射），
    presigned GET URL 浏览器不可达 → 由 8100 内部取 MinIO 再流式回给浏览器。

    鉴权沿用 get_current_user（Bearer）；前端用「fetch 带 Bearer → blob → objectURL」
    渲染（与 SliceFigure 既有模式一致，`<img>` 标签带不了 Authorization 头）。

    key 校验：仅接受对象存储 key 形态（禁绝对路径 / 禁 `..` 目录穿越 / 禁外部 URL），
    防止借本端点任意读对象。"""
    k = (key or "").strip()
    if not k or k.startswith(("/", "http://", "https://", "data:", "blob:")) or ".." in k:
        raise ApiError(ErrorCode.VALIDATION, "非法的图片 key")
    storage = get_storage()
    try:
        data = storage.get_bytes("teacher-photos", k)
    except Exception:  # noqa: BLE001 — 桶/key 不存在统一 404，不泄漏内部错误
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "图片不存在或已被清理") from None
    lower = k.lower()
    ctype = (
        "image/png" if lower.endswith(".png")
        else "image/webp" if lower.endswith(".webp")
        else "image/gif" if lower.endswith(".gif")
        else "image/jpeg"
    )
    return Response(
        content=data,
        media_type=ctype,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/quiz/kp-tree")
async def kp_tree(
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """知识点分类树（P4）：kp_nodes 真实行 + 题量统计（精确 COUNT，红线 4）。

    题量口径（2026-10-15 修正）：自底向上聚合 —— 节点自身精确命中（kp_code）+
    kp_path 命中 + **全部子孙节点题量之和**。此前只看 kp_path 数组，题目只挂章级
    code 时节/概念层恒为 0，树看起来"对不上"。

    树的视野（2026-10-15 收缩）：只返回**子树内有题**或**教师自建**（source='teacher'）
    的根节点及其子树。kp_nodes 语料是全学段（小一~高三）K12-KGraph 导入，与当前
    高中题库无关的零题章节不再占视野——树只呈现"有用的知识点"。
    """
    nodes = (await session.execute(select(KpNode).order_by(KpNode.sort, KpNode.code))).scalars().all()
    # 归属集合去重口径（2026-10-15）：每道题对 kp_code ∪ kp_path 里的每个 code 只计 1 次。
    # 此前 exact+subtree 相加会把两者同时命中的题计两次（111 题的章显示 222）。
    own: dict[str, int] = {}
    member_rows = await session.execute(select(QuizQuestion.kp_code, QuizQuestion.kp_path))
    for kp_code, kp_path in member_rows:
        codes = set(kp_path or [])
        if kp_code:
            codes.add(kp_code)
        for c in codes:
            if c:
                own[c] = own.get(c, 0) + 1

    tree = build_kp_tree(nodes, own)
    return ok({"tree": tree}, request_id=request.state.request_id)


@router.post("/quiz/kp-tree")
async def kp_tree_add(
    request: Request,
    body: dict,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """知识点树个人定制：加子级（V3.2）。parent_id=null 时加在顶层。"""
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError(ErrorCode.VALIDATION, "name 必填")
    parent_id = _uuid_or_none(body.get("parent_id"))
    if body.get("parent_id") and parent_id is None:
        raise ApiError(ErrorCode.VALIDATION, "parent_id 格式错误")
    if parent_id is not None:
        parent = (await session.execute(select(KpNode).where(KpNode.id == parent_id))).scalar_one_or_none()
        if parent is None:
            raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "父知识点不存在")
        parent_path = list(parent.path or [parent.code])
        code = f"{parent.code}.{uuid.uuid4().hex[:6]}"
        path = parent_path + [code]
    else:
        code = f"custom.{uuid.uuid4().hex[:6]}"
        path = [code]
    row = KpNode(code=code, name=name, parent_id=parent_id, path=path, source="teacher", sort=0)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ok({"id": str(row.id), "name": row.name, "code": row.code, "count": 0}, request_id=request.state.request_id)


async def _get_kp(session: AsyncSession, kp_id: str) -> KpNode:
    row = (await session.execute(select(KpNode).where(KpNode.id == _uuid(kp_id)))).scalar_one_or_none()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "知识点不存在")
    return row


@router.patch("/quiz/kp-tree/{kp_id}")
async def kp_tree_rename(
    request: Request,
    kp_id: str,
    body: dict,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await _get_kp(session, kp_id)
    new_name = (body.get("name") or "").strip()
    if not new_name:
        raise ApiError(ErrorCode.VALIDATION, "name 必填")
    if row.source != "teacher":
        raise ApiError(ErrorCode.FORBIDDEN, "内置知识点不可改名，可新建个人子级")
    row.name = new_name
    await session.commit()
    return ok({"ok": True}, request_id=request.state.request_id)


@router.delete("/quiz/kp-tree/{kp_id}")
async def kp_tree_remove(
    request: Request,
    kp_id: str,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await _get_kp(session, kp_id)
    if row.source != "teacher":
        raise ApiError(ErrorCode.FORBIDDEN, "内置知识点不可删除，可新建个人子级")
    await session.delete(row)
    await session.commit()
    return ok({"ok": True}, request_id=request.state.request_id)


@router.post("/quiz/kp-suggest")
async def kp_suggest(
    request: Request,
    body: dict,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """AI 识别知识点建议。

    诚实实现边界：SiliconFlow embedding key 失效（BLOCKED_ENV），语义检索通道不可用。
    当前通道 = kp_nodes 词面匹配（真实确定性规则，非编造）；note 明确告知教师当前通道。
    """
    text = (body.get("stem") or "").strip()
    if not text and body.get("src"):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "图片识别通道（pix2text sidecar）未就绪，请粘贴题干文本")
    if not text:
        raise ApiError(ErrorCode.VALIDATION, "stem 或 src 至少一项必填")

    nodes = (await session.execute(select(KpNode).order_by(func.length(KpNode.name).desc()))).scalars().all()
    scored: list[tuple[int, KpNode]] = []
    for n in nodes:
        if len(n.name) >= 2 and n.name in text:
            scored.append((len(n.name), n))
    scored.sort(key=lambda t: t[0], reverse=True)
    top = [n for _, n in scored[:4]]
    if not top:
        return ok({
            "suggestion": None,
            "alternates": [],
            "note": "未从题干中匹配到知识点词面，请手动选择（语义匹配通道待 embedding 配置恢复）",
        }, request_id=request.state.request_id)

    def payload(n: KpNode) -> dict:
        return {"code": n.code, "path": list(n.path or [n.code]), "name": n.name}

    return ok({
        "suggestion": {**payload(top[0]), "confidence": 0.6},
        "alternates": [payload(n) for n in top[1:]],
        "note": "当前为知识点词面匹配（embedding 通道未配置），建议教师复核归属",
    }, request_id=request.state.request_id)


# ---------- 专题夹 ----------

@router.get("/folders")
async def list_folders(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(select(Folder).order_by(Folder.updated_at.desc()))).scalars().all()
    counts = dict((await session.execute(
        select(FolderQuestionRef.folder_id, func.count()).group_by(FolderQuestionRef.folder_id)
    )).all())
    return ok({
        "items": [
            {
                "id": str(f.id),
                "name": f.name,
                "desc": f.desc,
                "count": counts.get(f.id, 0),
                "updated_at": f.updated_at.isoformat() if f.updated_at else None,
            }
            for f in rows
        ]
    }, request_id=request.state.request_id)


@router.post("/folders")
async def create_folder(request: Request, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError(ErrorCode.VALIDATION, "name 必填")
    row = Folder(name=name, desc=body.get("desc"), owner_id=_uuid_or_none(user.id) or uuid.uuid4())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return ok({
        "id": str(row.id), "name": row.name, "desc": row.desc, "count": 0,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }, request_id=request.state.request_id)


@router.delete("/folders/{folder_id}")
async def delete_folder(request: Request, folder_id: str, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    row = (await session.execute(select(Folder).where(Folder.id == _uuid(folder_id)))).scalar_one_or_none()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "专题夹不存在")
    if str(row.owner_id) != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "该专题夹属于另一位教师")
    await session.delete(row)
    await session.commit()
    return ok({"ok": True}, request_id=request.state.request_id)


@router.post("/folders/{folder_id}/questions")
async def folder_add_questions(request: Request, folder_id: str, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    folder = (await session.execute(select(Folder).where(Folder.id == _uuid(folder_id)))).scalar_one_or_none()
    if folder is None:
        raise ApiError(ErrorCode.NOT_FOUND_RESOURCE, "专题夹不存在")
    if str(folder.owner_id) != user.id:
        raise ApiError(ErrorCode.FORBIDDEN, "该专题夹属于另一位教师")
    qids = body.get("question_ids") or []
    if not qids:
        raise ApiError(ErrorCode.VALIDATION, "question_ids 必填")
    added = 0
    for qid in qids:
        exists = (await session.execute(
            select(FolderQuestionRef).where(
                FolderQuestionRef.folder_id == folder.id,
                FolderQuestionRef.question_id == _uuid(str(qid)),
            )
        )).scalar_one_or_none()
        if exists is None:
            session.add(FolderQuestionRef(folder_id=folder.id, question_id=_uuid(str(qid))))
            added += 1
    await session.commit()
    return ok({"ok": True, "count": added}, request_id=request.state.request_id)


@router.delete("/folders/{folder_id}/questions/{question_id}")
async def folder_remove_question(request: Request, folder_id: str, question_id: str, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    ref = (await session.execute(
        select(FolderQuestionRef).where(
            FolderQuestionRef.folder_id == _uuid(folder_id),
            FolderQuestionRef.question_id == _uuid(question_id),
        )
    )).scalar_one_or_none()
    if ref is None:
        return ok({"ok": True, "count": 0}, request_id=request.state.request_id)
    await session.delete(ref)
    await session.commit()
    return ok({"ok": True, "count": 0}, request_id=request.state.request_id)


# ---------- 教材 / 模板 / 配方 / 资源 / 洞察 ----------

@router.get("/textbook-chapters")
async def textbook_chapters(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """五件套级联数据源。契约允许 provenance='preset'（待上传教材替换）——DB 无教材时诚实标注 preset。"""
    rows = (await session.execute(select(Textbook))).scalars().all()
    if rows:
        return ok({
            "provenance": "knowledge_base",
            "textbooks": [
                {"name": t.name, "editable": True,
                 "chapters": [
                     {"id": str(i), "path": p}
                     for i, p in enumerate((t.chapters or {}).get("chapters", []) if isinstance(t.chapters, dict) else (t.chapters or []))
                 ]}
                for t in rows
            ],
        }, request_id=request.state.request_id)
    return ok({
        "provenance": "preset",
        "textbooks": [
            {"name": "人教A版（示例目录，上传教材后替换）", "editable": True, "chapters": []},
        ],
    }, request_id=request.state.request_id)


@router.post("/textbooks/rebuild-index")
async def textbook_rebuild_index(request: Request, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """上传教材 → 重建知识库目录。依赖文档解析/嵌入管线（B2），当前外部依赖未就绪——诚实降级，不伪造目录。"""
    raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "教材目录重建依赖文档解析/嵌入管线（当前 embedding 通道未配置），暂不可用")


@router.get("/deck-templates")
async def list_deck_templates(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """模板图卡（L4 判定①）：真实服务端渲染缩略图（MinIO presign GET），非前端色块占位。"""
    from ...storage.minio import get_storage
    from app.domains.contents.templates_seed import ensure_deck_templates

    await ensure_deck_templates(session)
    from app.domains.contents.models import DeckTemplate
    from app.domains.contents.templates_seed import _STYLE_THEMES
    rows = (await session.execute(select(DeckTemplate))).scalars().all()
    try:
        storage = get_storage()
        def _thumb(key: str) -> str:
            return storage.presign_get("teacher-thumbs", key, expires_seconds=3600) if key else ""
    except Exception:
        _thumb = lambda key: ""  # noqa: E731  MinIO 不可用：thumb 置空（前端骨架兜底）

    def _swatch(name: str) -> dict:
        """模板色板（前端 V3DeckTemplate.swatch 契约）；种子无主题时给默认亮色板。"""
        th = _STYLE_THEMES.get(name) or {}
        return {
            "bg": th.get("bg1", "#ffffff"),
            "primary": th.get("accent", "#4f46e5"),
            "accent": th.get("title", "#1e3a8a"),
            "light": True,
        }

    return ok({
        "items": [
            {
                "id": str(t.id), "name": t.name, "style": t.style,
                "slide_count": t.slide_count, "thumb": _thumb(t.thumb) if t.thumb else "", "source": t.source,
                "slots": t.slots,
                "swatch": _swatch(t.name),
                "page_kinds": sorted({k for k in (t.slots or {}) if not k.startswith("_")}),
                "recommended_for": "通用课件模板",
            }
            for t in rows
        ]
    }, request_id=request.state.request_id)


@router.get("/lesson-templates")
async def list_lesson_templates(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    from app.domains.contents.models import LessonTemplate
    rows = (await session.execute(select(LessonTemplate))).scalars().all()
    return ok({
        "items": [
            {"id": str(t.id), "name": t.name, "source": t.source, "boards": t.boards}
            for t in rows
        ]
    }, request_id=request.state.request_id)


@router.get("/recipes")
async def list_recipes(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    from app.domains.figures.models import Recipe
    # 归属过滤（审计 #9/L7 判据②）：school_shared 公开，私有仅属主可见
    from sqlalchemy import or_
    rows = (await session.execute(
        select(Recipe).where(or_(
            Recipe.school_shared.is_(True),
            Recipe.owner_id == (_uuid_or_none(user.id) or uuid.uuid4()),
        ))
        .order_by(Recipe.created_at.desc())
    )).scalars().all()
    return ok({
        "items": [
            {
                "id": str(r.id), "name": r.name, "preset_id": r.preset_id,
                "params": r.params, "school_shared": r.school_shared, "note": r.note,
            }
            for r in rows
        ]
    }, request_id=request.state.request_id)


@router.get("/insights/overview")
async def insights_overview(
    request: Request,
    class_id: str = Query(default=""),
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """学情洞察。诚实边界：批改/课堂作答数据为空时返回空结构（真实空态），不编造掌握度。"""
    cls = None
    if class_id:
        cls = (await session.execute(select(Classes).where(Classes.id == _uuid(class_id)))).scalar_one_or_none()
    return ok({
        "class_id": str(cls.id) if cls else class_id,
        "class_name": cls.name if cls else "",
        "avg": 0,
        "trend": [],
        "kp_heat": [],
        "error_tags": [],
        "watchlist": [],
    }, request_id=request.state.request_id)


@router.get("/resources")
async def list_resources(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """资源中心：聚合 decks/plans/图形配方/题库专题夹真实行。"""
    from app.domains.contents.models import Deck, Plan
    from app.domains.figures.models import Recipe

    items: list[dict] = []
    for d in (await session.execute(select(Deck).order_by(Deck.updated_at.desc()).limit(50))).scalars().all():
        items.append({
            "id": str(d.id), "name": d.title, "kind": "deck", "subject": "数学",
            "updated_at": (d.updated_at or d.created_at).isoformat() if (d.updated_at or d.created_at) else None,
            "owner": "张老师", "shared": False,
        })
    for p in (await session.execute(select(Plan).order_by(Plan.updated_at.desc()).limit(50))).scalars().all():
        items.append({
            "id": str(p.id), "name": p.topic, "kind": "plan", "subject": "数学",
            "updated_at": (p.updated_at or p.created_at).isoformat() if (p.updated_at or p.created_at) else None,
            "owner": "张老师", "shared": False,
        })
    for r in (await session.execute(select(Recipe).order_by(Recipe.created_at.desc()).limit(50))).scalars().all():
        items.append({
            "id": str(r.id), "name": r.name, "kind": "figure-recipe", "subject": "数学",
            "updated_at": r.created_at.isoformat() if r.created_at else None,
            "owner": "张老师", "shared": bool(r.school_shared),
        })
    return ok({"items": items}, request_id=request.state.request_id)


@router.post("/plan-templates/import")
async def plan_templates_import(request: Request, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "模板质量体检依赖反套话/结构解析管线（B2 prompts），当前未接入——返回诚实失败")


@router.post("/plan-templates/extract")
async def plan_templates_extract(request: Request, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "模板提炼依赖教案结构解析管线（B2 prompts），当前未接入——返回诚实失败")


# ---------- 工具 ----------

def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        raise ApiError(ErrorCode.VALIDATION, f"非法 UUID: {value}")


def _uuid_or_none(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _strip_latex(latex: str) -> str:
    """LaTeX → 纯文本近似（IFC-002 stem_plain 口径：检索词面匹配用）。"""
    import re

    text = re.sub(r"\$+|\\[a-zA-Z]+|[{}]", "", latex or "")
    return re.sub(r"\s+", " ", text).strip()
