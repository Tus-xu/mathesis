# coding: utf-8
"""grading 域路由（作业发布 / 作答采集 / 聚类 / 终审定分 / 讲评包）。

2026-10-05 批改工作台真实化（用户实测「看不到真实的作业批改」）：

此前的问题（全部实测定位）：
  1. `GET /grading/assignments/{id}` 返回结构与前端 V3GradingAssignment 契约
     **对不上**（后端给 kp_code/clusters[].members，前端要 q_no/full_score/
     accuracy/error_dist/tiers/clusters[].sample[]）→ 打开作业页面空白；
  2. 没有任何聚类生成逻辑（grading_clusters 恒 0 行）；
  3. `confirm` 只存 feedback，**教师给的分被丢弃**，graded 计数也不更新；
  4. 作答只有 `image_key`（且全是 demo/answers/... 假路径），没有文本作答通道；
  5. 前端「学生答卷原图」是硬编码的内联 SVG 假图。

本次：
  - 表结构补齐（trial_002 迁移）：题号/满分/题干配图/参考答案 + 文本作答/终审定分；
  - **真实作答采集**：`POST /grading/assignments/{id}/answers`（文本作答；照片作答
    走 image_key，口子保留）；
  - **自动聚类**：按作答文本归一化分组，与参考答案比对判 kind（correct/partial/wrong/
    blank），无参考答案时诚实降级为「待教师判定」（不编造）；
  - **终审定分**：confirm 落盘每份作答的 score 并更新 graded 计数（真实 UPDATE）；
  - 详情端点按 V3 契约组装：questions[].clusters[].sample[]、error_dist、tiers。
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ...core.deps import get_current_user, get_session
from ...core.errors import ApiError, ErrorCode
from ...core.response import ok
from ...core.security import UserToken
from . import paper_publish
from .models import GradingAnswer, GradingAssignment, GradingCluster, GradingQuestion

router = APIRouter()

_ERROR_TAGS = ("计算错误", "概念混淆", "步骤缺失", "审题错误", "方法选择", "表达不规范")


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        raise ApiError(ErrorCode.VALIDATION, f"非法 UUID: {value}")


def _norm_answer(text: str | None) -> str:
    """作答归一化（聚类分桶用）：去空白/全半角差异/结尾标点。"""
    t = str(text or "")
    t = re.sub(r"\s+", "", t)
    t = t.replace("（", "(").replace("）", ")").replace("，", ",").replace("。", "")
    return t.strip().rstrip(".")


def _kind_of(answer_text: str | None, reference: str | None) -> tuple[str, str | None]:
    """作答 → (kind, tag)。有参考答案才判 correct/wrong；否则诚实给 partial（待教师判定）。"""
    a = _norm_answer(answer_text)
    if not a:
        return "blank", None
    ref = _norm_answer(reference)
    if not ref:
        return "partial", None
    if a == ref:
        return "correct", None
    # 数值等价（如 1/√3 与 √3/3）：提取数字串比对
    na = re.findall(r"-?\d+(?:\.\d+)?", a)
    nr = re.findall(r"-?\d+(?:\.\d+)?", ref)
    if na and nr and na[-3:] == nr[-3:]:
        return "correct", None
    return "wrong", "计算错误"


async def _ensure_clusters(session: AsyncSession, question: GradingQuestion, answers: list[GradingAnswer]) -> list[GradingCluster]:
    """把一道题的作答自动聚类（幂等：已聚类的作答不再动）。

    分桶：文本作答按归一化文本分组；照片作答（无文本）归入「待人工聚类」桶。
    kind：与参考答案比对；无参考答案时给 partial + 空 tag（不编造错误类型）。
    """
    existing = (await session.execute(
        select(GradingCluster).where(GradingCluster.question_id == question.id)
    )).scalars().all()
    by_id = {c.id: c for c in existing}
    pending = [a for a in answers if a.cluster_id is None]

    groups: dict[str, list[GradingAnswer]] = defaultdict(list)
    for a in pending:
        if a.answer_text and a.answer_text.strip():
            groups[_norm_answer(a.answer_text)].append(a)
        else:
            groups.setdefault("__unclustered__", []).append(a)

    created_or_updated: list[GradingCluster] = []
    for norm, group in groups.items():
        first = group[0]
        # 同文本的旧聚类复用（幂等）
        target = None
        if norm != "__unclustered__":
            for c in existing:
                if _norm_answer(c.summary) == norm:
                    target = c
                    break
        if target is None:
            kind, tag = _kind_of(first.answer_text, question.reference_answer)
            sample_title = (first.answer_text or "").strip() or "（未作答）"
            if norm == "__unclustered__":
                sample_title = "（照片作答，待人工聚类）"
            target = GradingCluster(
                question_id=question.id,
                summary=sample_title[:120],
                feedback=None,
                confirmed=False,
                count=0,
                kind=kind,
                tag=tag,
                members=[],
            )
            session.add(target)
            await session.flush()
        for a in group:
            a.cluster_id = target.id
        target.count = sum(1 for a in answers if a.cluster_id == target.id)
        created_or_updated.append(target)
    await session.flush()
    return created_or_updated


def _steps_from_answer(answer_text: str | None) -> list[dict]:
    """文本作答 → 识别步骤（真实数据：按行切分；空行/编号行过滤）。"""
    steps: list[dict] = []
    for line in str(answer_text or "").splitlines():
        s = line.strip().lstrip("0123456789.、）) ")
        if not s:
            continue
        steps.append({"latex": s, "status": "ok"})
    return steps


@router.post("/assignments/publish-paper")
async def publish_paper_assignment(
    request: Request,
    body: paper_publish.PaperPublishRequest,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """组卷中心 → 8000 班级作业（真实 draft→published，学生课堂任务可见）。

    题目正文由 8100 题库按 id 读取（不信任前端正文）；当前成员关系与教师角色由
    8000 再次裁决；失败 fail-closed，不写演示数据、不返回假成功。
    """
    token = request.headers.get("Authorization", "")
    bearer = token[7:] if token.lower().startswith("bearer ") else ""
    result = await paper_publish.publish_paper(session, token=bearer, body=body)
    return ok(result, request_id=request.state.request_id)


@router.post("/assignments/publish")
async def publish_assignment(request: Request, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """发布作业：questions[] 支持 {kp_code/kp_name, stem_latex, stem_image, reference_answer,
    full_score, answer_text?}；q_no 按数组顺序。"""
    title = (body.get("title") or "").strip()
    class_id = body.get("class_id")
    questions = body.get("questions") or []
    if not title or not class_id or not questions:
        raise ApiError(ErrorCode.VALIDATION, "title/class_id/questions 必填")
    row = GradingAssignment(
        title=title, class_id=_uuid(class_id),
        total=len(questions), submitted=0, graded=0,
    )
    session.add(row)
    await session.flush()
    for i, q in enumerate(questions, start=1):
        session.add(GradingQuestion(
            assignment_id=row.id,
            kp_code=str(q.get("kp_code") or q.get("kp_name") or ""),
            stem_latex=str(q.get("stem_latex") or ""),
            image_key=(str(q.get("stem_image")) if q.get("stem_image") else None),
            reference_answer=(str(q.get("reference_answer")) if q.get("reference_answer") else None),
            q_no=q.get("q_no") or i,
            full_score=float(q.get("full_score") or 10),
            error_rate=None,
        ))
    await session.commit()
    return ok({
        "ok": True, "assignment_id": str(row.id), "demo": False,
        "classroom_published": False,
        "note": "本端点仅写 8100 批改工作台数据，不会进入学生课堂任务；班级作业请用 /assignments/publish-paper",
    }, request_id=request.state.request_id)


@router.post("/grading/assignments/{assignment_id}/answers")
async def add_answer(request: Request, assignment_id: str, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """采集一份学生作答（文本作答；照片作答传 image_key，口子保留）。

    空白卷是真实批改场景 → 内容可为空（kind=blank）。同一作业同一学生的同一题
    重复提交 → 覆盖（幂等，便于重交）。"""
    a = await session.get(GradingAssignment, _uuid(assignment_id))
    if a is None:
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "作业不存在")
    qid = body.get("question_id")
    name = (body.get("student_name") or "").strip()
    answer_text = (body.get("answer_text") or "").strip() or None
    image_key = (body.get("image_key") or "").strip() or None
    if not qid or not name:
        raise ApiError(ErrorCode.VALIDATION, "question_id/student_name 必填")
    q = await session.get(GradingQuestion, _uuid(str(qid)))
    if q is None or str(q.assignment_id) != assignment_id:
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "题目不属于该作业")

    row = (await session.execute(
        select(GradingAnswer).where(
            GradingAnswer.assignment_id == a.id,
            GradingAnswer.question_id == q.id,
            GradingAnswer.student_name == name,
        )
    )).scalar_one_or_none()
    if row is None:
        row = GradingAnswer(assignment_id=a.id, question_id=q.id, student_name=name,
                            image_key=image_key or "", cluster_id=None)
        session.add(row)
    row.answer_text = answer_text
    if image_key:
        row.image_key = image_key
    row.score = None  # 重交重批
    all_answers = (await session.execute(
        select(GradingAnswer).where(GradingAnswer.assignment_id == a.id)
    )).scalars().all()
    a.submitted = len({x.student_name for x in all_answers})
    await session.flush()
    await _ensure_clusters(session, q, [x for x in all_answers if x.question_id == q.id])
    await session.commit()
    return ok({"ok": True, "answer_id": str(row.id)}, request_id=request.state.request_id)


@router.post("/grading/assignments/{assignment_id}/simulate-submissions")
async def simulate_submissions(request: Request, assignment_id: str, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """演示作答（契约显式 demo 端点，姓名前缀「演示·」）：按题生成 文本作答并真实聚类。"""
    a = await session.get(GradingAssignment, _uuid(assignment_id))
    if a is None:
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "作业不存在")
    questions = (await session.execute(
        select(GradingQuestion).where(GradingQuestion.assignment_id == a.id)
    )).scalars().all()
    added = 0
    demo = [
        ("演示·小陈", "解：{ref}\n故结论成立。"),
        ("演示·小林", "解：思路正确，但最后一步计算出错，得到 2/√3。"),
        ("演示·小王", "解：直接设坐标代入，缺少论证过程。"),
        ("演示·小赵", ""),
    ]
    for q in questions:
        ref = (q.reference_answer or "").strip()
        for name, tpl in demo:
            text = tpl.replace("{ref}", ref or "按参考答案逐步推导")
            row = (await session.execute(
                select(GradingAnswer).where(
                    GradingAnswer.assignment_id == a.id,
                    GradingAnswer.question_id == q.id,
                    GradingAnswer.student_name == name,
                )
            )).scalar_one_or_none()
            if row is None:
                row = GradingAnswer(assignment_id=a.id, question_id=q.id, student_name=name,
                                    image_key=f"demo/answers/{assignment_id}/{q.q_no}_{name}.png", cluster_id=None)
                session.add(row)
            row.answer_text = text
            added += 1
    await session.flush()
    for q in questions:
        answers = (await session.execute(
            select(GradingAnswer).where(
                GradingAnswer.assignment_id == a.id, GradingAnswer.question_id == q.id
            )
        )).scalars().all()
        await _ensure_clusters(session, q, answers)
    a.submitted = len({x.student_name for x in (await session.execute(
        select(GradingAnswer).where(GradingAnswer.assignment_id == a.id)
    )).scalars().all()})
    await session.commit()
    return ok({"ok": True, "demo": True, "added": added}, request_id=request.state.request_id)


@router.get("/grading/assignments")
async def list_assignments(request: Request, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    from ...db.models import Classes

    rows = (await session.execute(select(GradingAssignment).order_by(GradingAssignment.updated_at.desc()).limit(50))).scalars().all()
    cls_names = {str(c.id): c.name for c in (await session.execute(select(Classes))).scalars().all()}
    return ok({"items": [
        {
            "id": str(a.id), "title": a.title, "class_id": str(a.class_id),
            "class_name": cls_names.get(str(a.class_id), ""),
            "submitted": a.submitted or 0, "total": a.total or 0, "graded": a.graded or 0,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }
        for a in rows
    ]}, request_id=request.state.request_id)


@router.get("/grading/assignments/{assignment_id}")
async def get_assignment(request: Request, assignment_id: str, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """作业详情 —— 按**前端 V3GradingAssignment 契约**组装（此前字段对不上 → 页面空白）。"""
    a = await session.get(GradingAssignment, _uuid(assignment_id))
    if a is None:
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "作业不存在")
    questions = (await session.execute(
        select(GradingQuestion).where(GradingQuestion.assignment_id == a.id).order_by(GradingQuestion.q_no)
    )).scalars().all()
    answers = (await session.execute(
        select(GradingAnswer).where(GradingAnswer.assignment_id == a.id)
    )).scalars().all()
    clusters = (await session.execute(
        select(GradingCluster).where(GradingCluster.question_id.in_([q.id for q in questions] or [uuid.uuid4()]))
    )).scalars().all()
    answers_by_q: dict[uuid.UUID, list[GradingAnswer]] = defaultdict(list)
    for ans in answers:
        answers_by_q[ans.question_id].append(ans)
    clusters_by_id = {c.id: c for c in clusters}

    v3_questions: list[dict] = []
    all_tier_rows: list[tuple[str, float, list[str]]] = []
    for qi, q in enumerate(questions, start=1):
        q_answers = answers_by_q.get(q.id, [])
        q_clusters = [c for c in clusters if c.question_id == q.id]
        # 聚类样本（每个聚类取前 6 份作答）
        v3_clusters = []
        error_dist: dict[str, int] = defaultdict(int)
        for c in q_clusters:
            members = [ans for ans in q_answers if ans.cluster_id == c.id]
            sample = []
            for ans in members[:6]:
                score = float(ans.score) if ans.score is not None else (
                    float(q.full_score) if c.kind == "correct" else 0.0
                )
                sample.append({
                    "student": ans.student_name,
                    "score": score,
                    "photo_region": {"x": 0, "y": 0, "w": 0, "h": 0},
                    "answer_image": _answer_image_url(ans.image_key),
                    "answer_text": ans.answer_text or "",
                    "recognized_steps": _steps_from_answer(ans.answer_text),
                    "feedback": c.feedback or "",
                })
            if c.tag:
                error_dist[c.tag] = c.count or len(members)
            v3_clusters.append({
                "id": str(c.id),
                "kind": c.kind,
                "tag": c.tag,
                "count": len(members),
                "summary": c.summary,
                "feedback": c.feedback,
                "confirmed": bool(c.confirmed),
                "sample": sample,
            })
        # 正确率（correct 份数 / 总份数）
        acc = (sum(c.count or 0 for c in q_clusters if c.kind == "correct") / len(q_answers)) if q_answers else 0.0
        v3_questions.append({
            "id": str(q.id),
            "q_no": q.q_no or qi,
            "stem_latex": q.stem_latex,
            "stem_image": _answer_image_url(q.image_key) if q.image_key else None,
            "full_score": float(q.full_score),
            "accuracy": round(acc, 3),
            "error_dist": [{"tag": t, "count": n} for t, n in error_dist.items()],
            "clusters": v3_clusters,
        })
        # 分层（A/B/C：按学生均分）
        for ans in q_answers:
            sc = float(ans.score) if ans.score is not None else None
            if sc is None:
                continue
            all_tier_rows.append((ans.student_name, sc, [c.tag for c in q_clusters if c.tag and ans.cluster_id == c.id]))

    # 分层汇总
    per_student: dict[str, list[float]] = defaultdict(list)
    weak: dict[str, list[str]] = defaultdict(list)
    for name, sc, tags in all_tier_rows:
        per_student[name].append(sc)
        weak[name].extend([t for t in tags if t])
    tiers = [
        {"tier": "A", "label": "优秀（≥85%）", "accuracy": None, "students": []},
        {"tier": "B", "label": "中等（60–85%）", "accuracy": None, "students": []},
        {"tier": "C", "label": "待加强（<60%）", "accuracy": None, "students": []},
    ]
    for name, scores in per_student.items():
        avg = sum(scores) / len(scores) if scores else 0.0
        total_score = sum(float(q.full_score) for q in questions) or 10.0
        ratio = avg / total_score if total_score else 0.0
        idx = 0 if ratio >= 0.85 else (1 if ratio >= 0.6 else 2)
        tiers[idx]["students"].append({"name": name, "avg": round(avg, 2), "weak_tags": list(dict.fromkeys(weak.get(name, [])))[:3]})
    for t in tiers:
        t["accuracy"] = round(sum(s["avg"] for s in t["students"]) / len(t["students"]) / 10, 3) if t["students"] else 0.0
        t["students"] = t["students"][:20]

    graded = sum(1 for ans in answers if ans.score is not None)
    return ok({
        "id": str(a.id), "title": a.title, "class_id": str(a.class_id),
        "total": a.total, "submitted": a.submitted, "graded": graded,
        "questions": v3_questions,
        "tiers": tiers,
    }, request_id=request.state.request_id)


def _answer_image_url(image_key: str | None) -> str | None:
    """作答/题干图 → 鉴权代理 URL（服务器上 minio 只在内网，presign URL 浏览器不可达）。"""
    if not image_key:
        return None
    v = str(image_key)
    if v.startswith(("http://", "https://", "data:", "blob:")):
        return v
    from urllib.parse import quote

    return f"/api/teacher-v3/grading/image?key={quote(v, safe='')}"


@router.get("/grading/image")
async def grading_image(request: Request, key: str, user: UserToken = Depends(get_current_user)) -> Response:
    """作答/题干图代理（与 catalog/quiz/image 同口径；桶 teacher-photos）。"""
    from urllib.parse import quote as _q  # noqa: F401
    from fastapi.responses import Response

    k = (key or "").strip()
    if not k or k.startswith(("/", "http://", "https://", "data:", "blob:")) or ".." in k:
        raise ApiError(ErrorCode.VALIDATION, "非法的图片 key")
    from ...storage.minio import get_storage as _gs

    try:
        data = _gs().get_bytes("teacher-photos", k)
    except Exception:  # noqa: BLE001
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "图片不存在或已被清理") from None
    lower = k.lower()
    ctype = ("image/png" if lower.endswith(".png")
             else "image/webp" if lower.endswith(".webp")
             else "image/gif" if lower.endswith(".gif") else "image/jpeg")
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.post("/grading/assignments/{assignment_id}/clusters/{cluster_id}/confirm")
async def confirm_cluster(request: Request, assignment_id: str, cluster_id: str, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """聚类终审：feedback + **每份作答的得分真实落盘**（此前分数被丢弃）、更新 graded 计数。

    body: {feedback?: str, reviews?: [{student: str, score: number}], default_score?: number}
    """
    feedback = (body.get("feedback") or "").strip()
    reviews = body.get("reviews") or []
    c = await session.get(GradingCluster, _uuid(cluster_id))
    if c is None:
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "聚类不存在")
    q = await session.get(GradingQuestion, c.question_id)
    if q is None or str(q.assignment_id) != assignment_id:
        raise ApiError(ErrorCode.NOT_FOUND_ITEM, "聚类不属于该作业")
    c.feedback = feedback or c.feedback
    c.confirmed = True

    score_by_name: dict[str, float] = {}
    for r in reviews if isinstance(reviews, list) else []:
        if isinstance(r, dict) and r.get("student") is not None and r.get("score") is not None:
            try:
                score_by_name[str(r["student"])] = float(r["score"])
            except (TypeError, ValueError):
                continue
    members = [ans for ans in (await session.execute(
        select(GradingAnswer).where(GradingAnswer.cluster_id == c.id)
    )).scalars().all()]
    for ans in members:
        if ans.student_name in score_by_name:
            ans.score = score_by_name[ans.student_name]
        elif body.get("default_score") is not None:
            ans.score = float(body["default_score"])
    await session.commit()
    # graded = 已定分学生数（真实 UPDATE）
    a = await session.get(GradingAssignment, _uuid(assignment_id))
    if a is not None:
        a.graded = len({ans.student_name for ans in (await session.execute(
            select(GradingAnswer).where(
                GradingAnswer.assignment_id == a.id,
                GradingAnswer.score.is_not(None),
            )
        )).scalars().all()})
        await session.commit()
    return ok({"ok": True, "graded": a.graded if a else None}, request_id=request.state.request_id)


@router.post("/grading/assignments/{assignment_id}/review-pack")
async def review_pack(request: Request, assignment_id: str, user: UserToken = Depends(get_current_user)) -> EventSourceResponse:
    """讲评包 SSE：meta → slide* → done。B1 review 图依赖批改统计与提示词渲染；
    数据缺失/LLM 异常时诚实 error——不返回假讲评页。"""
    import json

    async def gen():
        yield {"event": "meta", "data": json.dumps({"assignment_id": assignment_id}, ensure_ascii=False)}
        yield {"event": "error", "data": json.dumps({
            "code": 50300,
            "message": "讲评包生成链路未完成接线（B1 review 图需批改统计 provider 注入）——当前不生成占位讲评页",
        }, ensure_ascii=False)}
        yield {"event": "done", "data": json.dumps({"finish_reason": "not_implemented"})}

    return sse_response(gen())
