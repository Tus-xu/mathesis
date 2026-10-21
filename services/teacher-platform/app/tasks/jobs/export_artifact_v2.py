"""T24 产物导出作业（8100 worker 侧）。

链路（不换引擎）：
  deck:  8100 spec → 8200 ingest → 8200 ppt.deck.generate → 轮询 → DECK_PPTX 字节
         → sha256 复算 → QA 卡门（p0/p1 拦截：落库跨页重复消除提案 → 新 revision →
         重新 ingest/生成，≤2 轮；页图不成套或渲染不可用 → 显式 skipped 放行）
         → MinIO + teacher_artifacts(pptx) → [pdf] 8201 COM 导出 → teacher_artifacts(pdf)
         → qa_status 写 tasks.result（V2 链不写 export_jobs 表）
  教案:  LessonPlanSpec → markdown / reportlab PDF → MinIO + teacher_artifacts

失败语义：任一环失败即抛错 → run_task 置 failed + error，**不产出**任何占位产物；
QA 未通过且不可修复（spec 无变化 → 重渲染必得同产物）同样如实失败，缺陷件不出厂。
取消语义：每个阶段前后读一次 tasks.status，见 cancelled 抛 TaskCancelled（终态 cancelled，不是 done）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EventPublisher
from app.db.models import Tasks
from app.db.session import session_factory
from app.ppt_v2.adapters.deck_engine import DeckEngineClient, EngineError, EngineUnavailable
from app.ppt_v2.service import enrich as enrich_service
from app.ppt_v2.service import qa as qa_service
from app.ppt_v2.service import spec_edit
from app.storage.artifacts import register_artifact, safe_download_name
from app.storage.minio import get_storage

_PDF_MEDIA = "application/pdf"
_PPTX_MEDIA = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_MD_MEDIA = "text/markdown"
_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

logger = logging.getLogger("teacher.tasks.export_v2")


class TaskCancelled(RuntimeError):
    """作业被取消（终态 cancelled，不能冒充 done）。"""


async def _emit(publisher: EventPublisher, task_id: str, seq_holder: dict[str, int], event: str, payload: dict[str, Any]) -> None:
    seq_holder["n"] += 1
    await publisher.publish_durable(
        scope_kind="task", scope_id=uuid.UUID(task_id), event_type=event, payload=payload
    )


async def _ensure_not_cancelled(session: AsyncSession, task_id: str) -> None:
    row = (await session.execute(select(Tasks).where(Tasks.id == uuid.UUID(task_id)))).scalar_one_or_none()
    if row is not None and row.status == "cancelled":
        raise TaskCancelled("作业已取消")


async def _payload_of(task_id: str) -> tuple[dict[str, Any], str]:
    async with session_factory()() as session:
        row = (await session.execute(select(Tasks).where(Tasks.id == uuid.UUID(task_id)))).scalar_one_or_none()
        if row is None:
            raise RuntimeError(f"task {task_id} 不存在")
        return dict(row.payload or {}), str(row.user_id)


def _lineage(
    *,
    spec_kind: str,
    spec_id: str,
    spec_revision: int,
    projection: dict[str, Any] | None,
    refs: dict[str, Any] | None,
    derived_from: list[str],
    engine: dict[str, Any],
) -> dict[str, Any]:
    projection = projection or {}
    refs = refs or {}
    return {
        "spec_kind": spec_kind,
        "spec_id": spec_id,
        "spec_revision": spec_revision,
        "approved_blueprint_id": projection.get("approved_blueprint_id") or refs.get("approved_blueprint_id"),
        "blueprint_revision": projection.get("approved_blueprint_revision") or refs.get("blueprint_revision"),
        "context_snapshot_hash": projection.get("context_snapshot_hash") or refs.get("context_manifest_id"),
        "prompt_ref": refs.get("prompt_ref") or (
            {"prompt_id": projection.get("prompt_release_id")} if projection.get("prompt_release_id") else None
        ),
        "evidence_refs": list(refs.get("evidence_refs") or []),
        "derived_from": derived_from,
        "engine": engine,
    }


# 8200 页级事件 → 8100 任务事件流的 stage 映射（宪法§15：长任务必须给阶段性真实结果）。
# 8200 事件源：ppt-studio-v2/app/reliability/steps.py 页循环 emit；页码 1-based
# （enumerate start=1）。page_total 由 8100 侧已 ingest 的 spec 页数补齐
# （8200 事件 payload 只带页码不带总数）。
_PAGE_EVENT_STAGES = {
    "PAGE_COMPLETED": "deck_page_completed",
    "CHECKPOINT_REUSED": "deck_checkpoint_reused",
}


async def _forward_engine_page_events(
    engine: DeckEngineClient,
    job_id: str,
    *,
    publisher: EventPublisher,
    task_id: str,
    seq: dict[str, int],
    page_total: int,
    last_seq: int,
) -> int:
    """增量拉取 8200 作业事件并映射页级进度；返回推进后的已见 seq。

    事件是进度信息而非产物：拉取失败只告警跳过本轮，绝不让导出因此失败。
    """
    try:
        events = await engine.list_job_events(job_id)
    except (EngineError, EngineUnavailable) as exc:
        logger.warning("8200 作业事件拉取失败（本轮跳过，不影响导出）: job=%s err=%s", job_id, exc)
        return last_seq
    seen = last_seq
    for ev in events:
        cur = int(ev.get("seq") or 0)
        if cur <= seen:
            continue  # 增量去重：seq 单调推进，重复轮询不重发
        seen = cur
        stage = _PAGE_EVENT_STAGES.get(str(ev.get("event_type") or ""))
        if stage is None:
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        page_no = int(payload.get("page_no") or 0)
        if page_no < 1:
            continue  # 缺页码的异常事件不映射（不发明页号）
        detail: dict[str, Any] = {
            "stage": stage,
            "page_index": page_no,
            "page_total": page_total,
            "slide_id": payload.get("slide_id"),
        }
        if stage == "deck_page_completed":
            detail["elapsed_ms"] = payload.get("elapsed_ms")
        await _emit(publisher, task_id, seq, "stage", detail)
    return seen


async def _poll_engine_job(
    engine: DeckEngineClient,
    job_id: str,
    *,
    publisher: EventPublisher,
    task_id: str,
    seq: dict[str, int],
    page_total: int,
    timeout_s: float = 900.0,
) -> dict[str, Any]:
    """轮询 8200 生成作业到终态；期间增量转发页级事件（「首批课件页可见」的承载点）。

    轮询上限 900s 与 8200 作业 deadline 900s 对齐：引擎先到点 → 这里读到引擎侧
    真实终态（DLQ/FAILED 原因）而不是客户端盲超时。事件转发在终态判断**之前**
    执行，最后一轮（COMPLETED）的收尾页事件同样不丢。
    """
    deadline = asyncio.get_running_loop().time() + timeout_s
    last: dict[str, Any] = {}
    last_event_seq = 0
    while asyncio.get_running_loop().time() < deadline:
        last = await engine.get_job(job_id)
        last_event_seq = await _forward_engine_page_events(
            engine, job_id,
            publisher=publisher, task_id=task_id, seq=seq,
            page_total=page_total, last_seq=last_event_seq,
        )
        state = str(last.get("status") or "").upper()
        if state == "COMPLETED":
            return last
        if state in {"FAILED", "CANCELLED", "DLQ"}:
            raise EngineError(f"8200 生成作业终态 {state}: {last.get('last_error') or last.get('error') or ''}")
        await asyncio.sleep(1.5)
    raise EngineUnavailable(f"8200 生成作业 {job_id} 超时未完成（>{timeout_s}s）")


def _inline_assets(spec_dict: dict[str, Any]) -> dict[str, str]:
    """把 spec 引用的资产字节随 ingest 一并下发（8200 要求字节在场并校验 sha256）。

    字节从平台对象存储读取；读不到或哈希不符的一律**不下发**（宁可让引擎显式失败，
    也不把不一致的字节塞过去）。
    """
    import base64

    out: dict[str, str] = {}
    store = get_storage()
    for asset in spec_dict.get("assets") or []:
        key = asset.get("object_key")
        if not key:
            continue
        try:
            data = store.get_bytes("teacher-assets", key)
        except Exception:  # noqa: BLE001 - 缺失资产由引擎侧闸门裁决
            continue
        if hashlib.sha256(data).hexdigest() != str(asset.get("sha256", "")).lower():
            continue
        out[str(asset.get("id"))] = base64.b64encode(data).decode("ascii")
    return out


def _latest_deck_pptx(artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从 artifact 列表里**确定性**取「最新」的 DECK_PPTX。

    依据（已核实 8200 代码）：`GET /decks/{deck_id}/artifacts` 的 SQL 排序为
    ``ORDER BY created_at ASC, kind ASC``（见 services/ppt-studio-v2/app/api/routes.py
    ``deck_artifacts_route``），响应按此顺序返回，且每条含 ``created_at``（ISO8601）。

    因此「最新」= ``created_at`` 最大者；``created_at`` 缺失/相等时以列表位置为准
    （列表为升序 → 越靠后越新）。

    R8-F P0：旧实现用 ``next(...)`` 取**首个** = 最旧，导致引擎升级（数学图形 PNG 注入）
    后老师导出拿到的仍是旧的无图产物。这里改为确定性取最新。
    """
    best: dict[str, Any] | None = None
    best_key: tuple[str, int] | None = None
    for idx, art in enumerate(artifacts):
        if art.get("kind") != "DECK_PPTX":
            continue
        key = (str(art.get("created_at") or ""), idx)
        if best_key is None or key > best_key:
            best, best_key = art, key
    return best


async def _find_deck_pptx(engine: DeckEngineClient, engine_deck_id: str) -> dict[str, Any] | None:
    artifacts = await engine.list_artifacts(engine_deck_id)
    return _latest_deck_pptx(artifacts)


async def _submit_or_reuse(
    engine: DeckEngineClient, engine_deck_id: str, *, attempts: int = 200
) -> tuple[str | None, dict[str, Any] | None]:
    """提交 generate；8200 以 dedupe 409 拒绝时复用既有产物。

    8200 对同一 deck 的重复 generate 返回 409 且不回传既有 job_id，因此这里无法跟踪
    既有作业状态，只能以"产物是否已出现"为准；等不到即真实失败，不伪造成功。
    """
    try:
        submitted = await engine.submit_generate(engine_deck_id)
    except EngineError as exc:
        if "duplicate_job" not in str(exc) and "重复作业" not in str(exc):
            raise
        for _ in range(attempts):
            existing = await _find_deck_pptx(engine, engine_deck_id)
            if existing is not None:
                return None, existing
            await asyncio.sleep(3.0)
        raise EngineError(
            "8200 以 dedupe 拒绝重复生成且未回传既有 job_id，同时找不到已产出的 DECK_PPTX"
            "（引擎侧既有作业可能仍在进行）"
        ) from exc
    job_id = str(submitted.get("id") or submitted.get("job_id"))
    if not job_id or job_id == "None":
        raise EngineError(f"8200 未返回作业 id: {submitted}")
    return job_id, None


def _sanitize_deck_dict(spec_dict: dict[str, Any]) -> int:
    """导出边界反泄漏：剥除 deck 可见文本里的内部编辑标记（「（教师修订）」等）。

    存量 spec 可能由「净化前」的投影写入；教师端编辑还会再写入标记。导出是最后一道
    边界，统一剥除以保证 PPTX 产物零内部标记（LEAD-BAR §1.3）。返回清洗的字段数。
    """
    from app.teacher_prep.text_safety import strip_edit_markers

    cleaned = 0
    for slide in spec_dict.get("slides") or []:
        for field in ("title", "content", "speaker_note"):
            val = slide.get(field)
            if isinstance(val, str):
                new = strip_edit_markers(val).strip()
                if new != val:
                    slide[field] = new
                    cleaned += 1
    return cleaned


# 语义元素 → 正文展开顺序（与 ppt_v2/enrich._ROLE_ORDER、前端 SlideElements 版式顺序同族）
_ELEMENT_BODY_ORDER = ("definition", "figure", "knowledge_card", "formula", "bullets", "steps", "table", "note")
_ELEMENT_BODY_RANK = {r: i for i, r in enumerate(_ELEMENT_BODY_ORDER)}


def _element_body_lines(element: dict[str, Any], *, drop_example_card: bool = False) -> list[str]:
    """单个语义元素 → 导出正文行（与画布 SlideElements 可见文本同口径；figure 不进正文）。

    drop_example_card：原 content 已有完整「例/问题」题干时，跳过重复的例题知识卡，
    避免同一题面既进 problem 又进 solution-steps。
    """
    import re

    from app.teacher_prep.text_safety import is_teacher_annotation, sanitize_deck_text

    role = str(element.get("role") or "")
    if role == "figure":
        return []  # 图形由 slide.math_object 确定性内嵌（R8-U），不占正文行
    lines: list[str] = []
    if role == "formula":
        latex = str(element.get("latex") or "").strip()
        if latex:
            lines.append(latex if latex.startswith("$") else f"${latex}$")
    elif role in ("bullets", "steps", "table"):
        for i, item in enumerate(element.get("items") or [], start=1):
            if role == "table":
                # 表格行以 `|` 分隔单元格（FROZEN-CONTRACT §A.3）：逐格净化后重组，
                # 防止单元格分隔符被通用净化剥掉（实测 sanitize_deck_text 会吃 `|`）。
                cells = [sanitize_deck_text(c) for c in str(item).split("|")]
                text = " | ".join(c for c in cells if c)
            else:
                text = sanitize_deck_text(str(item))
            if not text:
                continue
            if role == "steps":
                # enrich 产出的步骤常自带「1. / 2.」；版式会再画序号，先剥掉避免双编号
                text = re.sub(r"^\s*\d+\s*[.、]\s*", "", text)
                if not re.match(r"^\d+[.、]", text):
                    text = f"{i}. {text}"
            lines.append(text)
    elif role == "knowledge_card":
        title = sanitize_deck_text(str(element.get("title") or ""))
        items = [
            sanitize_deck_text(str(item))
            for item in (element.get("items") or [])
            if sanitize_deck_text(str(item))
        ]
        # 例题/练习卡：标题只是「例1 题目」，真正题干在 items。worked/practice
        # 版式把首行当作 problem/prompt；必须合成「例1 题目 + 已知条件 + 求…」，
        # 否则会渲染成空题框。非例题卡仍按标题 + 条目分行。
        if title and title.startswith(("例", "问题", "练习")):
            if drop_example_card:
                return [ln for ln in lines if ln and not is_teacher_annotation(ln)]
            lines.append(" ".join([title, *items]).strip())
            items = []
        elif title:
            lines.append(title)
        lines.extend(items)
        latex = str(element.get("latex") or "").strip()
        if latex:
            lines.append(latex if latex.startswith("$") else f"${latex}$")
    elif role in ("definition", "note"):
        text = sanitize_deck_text(str(element.get("text") or ""))
        if text:
            lines.append(text)
    return [ln for ln in lines if ln and not is_teacher_annotation(ln)]


def _materialize_elements_body(spec_dict: dict[str, Any]) -> int:
    """导出边界一致性（G14）：`slide.elements` 非空时，正文 = 语义元素展开。

    背景（R3c 实测缺陷）：画布按 `elements` 渲染（知识卡/公式/步骤），导出链只吃
    `content` → 同一页两套事实（画布≠导出）。导出必须与教师看到的画布同一份正文：
    有 elements 的页，`content` 在下发引擎的副本上由元素确定性展开（不改源库），
    保证 画布=导出=落库 三者一致。返回改写的页数。
    """
    rewritten = 0
    for slide in spec_dict.get("slides") or []:
        elements = [e for e in (slide.get("elements") or []) if isinstance(e, dict)]
        if not elements:
            continue
        ordered = sorted(elements, key=lambda e: _ELEMENT_BODY_RANK.get(str(e.get("role") or ""), 99))
        lines: list[str] = []
        original_problem = ""
        if str(slide.get("type") or "") in ("worked-example", "practice"):
            for raw_line in str(slide.get("content") or "").splitlines():
                probe = raw_line.strip()
                if probe.startswith(("例", "问题")):
                    original_problem = probe
                    break
        for element in ordered:
            lines.extend(_element_body_lines(element, drop_example_card=bool(original_problem)))
        if original_problem and original_problem not in lines:
            lines.insert(0, original_problem)
        body = "\n".join(lines).strip()
        if body:
            slide["content"] = body
            rewritten += 1
    return rewritten


def _prepare_export_spec(spec: Any) -> dict[str, Any]:
    """下发引擎的 spec 副本（G14 materialize + 反泄漏 sanitize；只改副本不改源库）。

    Golden B Gate：导出是存量 deck 的最后一道确定性整形边界。新投影已在落库前
    polish；历史 deck 没有，若直接下发会出现「同图挂相邻两页 → 8200 序列无解」。
    这里对副本再跑一次 polish（纯函数、幂等），保证导出/课堂快照都拿到可排版版本，
    不修改源库、不生成新事实。
    """
    from app.teacher_prep.specs.deck_polish import polish_deck_spec

    polished, _report = polish_deck_spec(spec)
    spec_dict = polished.model_dump(mode="json")
    _materialize_elements_body(spec_dict)
    _sanitize_deck_dict(spec_dict)
    return spec_dict


# ── QA 卡门（R22）────────────────────────────────────────────────────────────
# QA p0/p1 → 拦截 artifact 输出，走确定性收敛后重出产物。修复轮上限 2：
# 每轮 = 落库跨页重复消除提案（revision+1 → 8200 内容寻址产生新 deck → 重渲染）；
# spec 无变化时重渲染必得同产物（8200 内容寻址幂等），不烧无效轮次，如实失败。
_QA_GATE_MAX_ROUNDS = 2


def _qa_gate_blocked(summary: dict[str, Any]) -> bool:
    """QA p0/p1 任一非零即拦截（get_qa 的 status 只反映 p0，这里按完整 summary 判）。"""
    return bool(int(summary.get("p0_count") or 0) or int(summary.get("p1_count") or 0))


def _qa_fail_detail(summary: dict[str, Any]) -> str:
    return (
        f"p0={summary.get('p0_count')} p1={summary.get('p1_count')} "
        f"failed_pages={summary.get('failed_pages')} by_kind={summary.get('by_kind')}"
    )


async def _run_deck_export(task_id: str, publisher: EventPublisher, payload: dict[str, Any]) -> None:
    deck_id = str(payload.get("deck_id") or "")
    fmt = str(payload.get("format") or "pptx")
    owner_id = uuid.UUID(str(payload["owner_id"])) if payload.get("owner_id") else None
    seq: dict[str, int] = {"n": 0}
    engine = DeckEngineClient()

    async with session_factory()() as session:
        row = await spec_edit.load_deck_row(session, deck_id)
        spec = spec_edit.load_spec(row)
        await spec_edit.ensure_snapshot(session, row)
        await session.commit()
        spec_revision = spec.revision
        # 教师可读下载名：用真实课题名（禁止所有 deck 都叫「教案课件」而无法区分）
        deck_title = str(getattr(spec.metadata, "title", "") or "")
        deck_stem = safe_download_name(deck_title, fallback="教学课件")

    spec_dict = _prepare_export_spec(spec)

    # ── 生成 + QA 卡门（R22）─────────────────────────────────────────────
    # 每轮按当前 spec 内容寻址 ingest（同内容幂等返回同一 deck）→ 生成 → QA；
    # QA p0/p1 → 拦截产物，落库跨页重复消除提案（enrich 汇总层同规则）→ 重读最新
    # revision 进下一轮。页级 checkpoint（8200 steps._checkpoint_reuse）保证同 deck
    # 复发/重试场景只重渲染缺失页，已完成页零重渲染。
    gate_round = 0
    qa_view: dict[str, Any] | None = None
    qa_skipped_reason: str | None = None
    engine_deck_id = ""
    engine_job_id = ""
    job: dict[str, Any] = {}
    page_pngs: dict[int, bytes] = {}
    plan: dict[str, Any] | None = None

    while True:
        gate_round += 1
        await _emit(
            publisher, task_id, seq, "stage",
            {"stage": "ingest", "round": gate_round, "page_count": len(spec_dict.get("slides") or [])},
        )
        assets_inline = _inline_assets(spec_dict)
        ingested = await engine.ingest_deck(spec_dict, assets_inline)
        await _emit(
            publisher, task_id, seq, "stage",
            {"stage": "ingested", "round": gate_round, "assets_inline": len(assets_inline),
             "total_assets": len(spec_dict.get("assets") or [])},
        )
        engine_deck_id = str(ingested.get("deck_id"))
        await _emit(publisher, task_id, seq, "stage",
                    {"stage": "generate", "round": gate_round, "engine_deck_id": engine_deck_id})

        submitted, reused = await _submit_or_reuse(engine, engine_deck_id)
        if reused is not None:
            # 8200 的内容寻址语义：同一 spec 的 deck+作业已存在，产物本身就是权威结果
            engine_job_id = str(reused.get("job_id") or "deduped")
            pptx_artifact = reused
            await _emit(publisher, task_id, seq, "stage",
                        {"stage": "reused_engine_result", "round": gate_round, "engine_job_id": engine_job_id})
            job = {"status": "REUSED"}
        else:
            engine_job_id = str(submitted)
            job = await _poll_engine_job(
                engine, engine_job_id,
                publisher=publisher, task_id=task_id, seq=seq,
                page_total=len(spec_dict.get("slides") or []),
            )
            await _emit(publisher, task_id, seq, "stage",
                        {"stage": "rendered", "round": gate_round, "engine_job_id": engine_job_id})
            artifacts = await engine.list_artifacts(engine_deck_id)
            pptx_artifact = _latest_deck_pptx(artifacts)
        if pptx_artifact is None:
            raise EngineError("8200 未产出 DECK_PPTX（拒绝把预览图当 PPTX）")
        pptx_bytes = await engine.artifact_bytes(str(pptx_artifact["id"]))
        actual = hashlib.sha256(pptx_bytes).hexdigest()
        if actual != pptx_artifact.get("sha256"):
            raise EngineError(
                f"DECK_PPTX 字节哈希与引擎登记不一致（{actual[:12]}… != {str(pptx_artifact.get('sha256'))[:12]}…）"
            )

        # ── 逐页 QA 输入：页图必须成套（缺页按 QA 不可用处理，不得静默部分通过）──
        total_pages = len(spec_dict.get("slides") or [])
        artifacts = await engine.list_artifacts(engine_deck_id)
        page_pngs = {}
        for art in artifacts:
            if art.get("kind") == "PAGE_PNG" and art.get("page_no"):
                try:
                    page_pngs[int(art["page_no"])] = await engine.artifact_bytes(str(art["id"]))
                except (EngineError, EngineUnavailable):
                    break  # 引擎页图不可取：整体转本地独立渲染，避免半套页图
        if page_pngs and len(page_pngs) != total_pages:
            page_pngs = {}
        if not page_pngs:
            from app.ppt_v2.qa.render import render_pages

            try:
                page_pngs = await render_pages(pptx_bytes)
                await _emit(publisher, task_id, seq, "stage",
                            {"stage": "qa_local_render", "round": gate_round, "pages": len(page_pngs)})
            except Exception as exc:  # noqa: BLE001 - 放行策略显式化：渲染不可用 → skipped，不静默
                qa_skipped_reason = f"QA 渲染不可用，放行并标注 skipped：{type(exc).__name__}: {str(exc)[:200]}"
                await _emit(publisher, task_id, seq, "stage",
                            {"stage": "qa_unavailable", "round": gate_round, "reason": str(exc)[:200]})
                break
        if page_pngs and len(page_pngs) != total_pages:
            # 本地渲染也只出半套页图 → 同样按 QA 不可用处理（缺页不得静默部分通过）
            qa_skipped_reason = (
                f"QA 页图不成套（{len(page_pngs)}/{total_pages}），放行并标注 skipped"
            )
            await _emit(publisher, task_id, seq, "stage",
                        {"stage": "qa_unavailable", "round": gate_round,
                         "reason": f"页图不成套 {len(page_pngs)}/{total_pages}"})
            break

        plan = None
        plan_art = next((a for a in artifacts if a.get("kind") == "PLAN_JSON"), None)
        if plan_art is not None:
            try:
                plan = json.loads((await engine.artifact_bytes(str(plan_art["id"]))).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                plan = None

        async with session_factory()() as session:
            await _ensure_not_cancelled(session, task_id)
            qa_view = await qa_service.run_full_qa(
                session,
                deck_id=uuid.UUID(deck_id),
                spec_revision=spec_revision,
                pptx_bytes=pptx_bytes,
                pages_png=page_pngs,
                plan=plan,
            )
            await session.commit()
        summary = dict(qa_view.get("summary") or {})
        await _emit(publisher, task_id, seq, "qa", {"round": gate_round, **summary})
        if not _qa_gate_blocked(summary):
            break  # QA 通过 → 出产物

        # ── QA 未通过：确定性收敛（修复轮 ≤2；同内容重渲染必得同产物，不烧假轮次）──
        if gate_round > _QA_GATE_MAX_ROUNDS:
            raise RuntimeError(
                f"课件 QA 未通过（{_QA_GATE_MAX_ROUNDS} 轮修复后仍不合格，拦截产物输出）: {_qa_fail_detail(summary)}"
            )
        async with session_factory()() as session:
            await _ensure_not_cancelled(session, task_id)
            dedup = await enrich_service.dedupe_deck_elements(
                session, deck_id,
                actor_id=str(owner_id) if owner_id else None,
                refs=payload.get("refs") if isinstance(payload.get("refs"), dict) else None,
            )
            await session.commit()
        if not dedup.get("changed"):
            raise RuntimeError(
                "课件 QA 未通过且无确定性修复可用（spec 未变 → 重渲染必得同产物，不烧无效轮次）"
                f": {_qa_fail_detail(summary)}"
            )
        await _emit(publisher, task_id, seq, "stage",
                    {"stage": "qa_remediation", "round": gate_round,
                     "removed_lines": dedup.get("removed_lines"), "revision": dedup.get("revision")})
        # spec 已落库变更 → 重读最新 revision，下一轮按新内容重新 ingest/生成
        async with session_factory()() as session:
            row = await spec_edit.load_deck_row(session, deck_id)
            spec = spec_edit.load_spec(row)
            await spec_edit.ensure_snapshot(session, row)
            await session.commit()
            spec_revision = spec.revision
            deck_stem = safe_download_name(str(getattr(spec.metadata, "title", "") or ""), fallback="教学课件")
        spec_dict = _prepare_export_spec(spec)

    if qa_view is None:
        # 显式放行（QA 渲染不可用）：不拦截产物，但 qa_status=skipped 如实在案
        await _emit(publisher, task_id, seq, "stage", {"stage": "qa_skipped", "reason": qa_skipped_reason})

    async with session_factory()() as session:
        await _ensure_not_cancelled(session, task_id)

    store = get_storage()
    projection = dict(spec_dict.get("projection") or {})
    lineage = _lineage(
        spec_kind="teaching_deck",
        spec_id=deck_id,
        spec_revision=spec_revision,
        projection=projection,
        refs=payload.get("refs") if isinstance(payload.get("refs"), dict) else None,
        derived_from=[f"8200:{engine_artifact_ref(pptx_artifact)}"],
        engine={"service": "8200", "job_id": engine_job_id, "deck_id": engine_deck_id, "vpl_version": "r21.1-vpl-1.0.0"},
    )

    async with session_factory()() as session:
        pptx_row = await register_artifact(
            session,
            store=store,
            spec_kind="teaching_deck",
            spec_id=deck_id,
            spec_revision=spec_revision,
            kind="pptx",
            media_type=_PPTX_MEDIA,
            data=pptx_bytes,
            filename=f"{deck_stem}-课件-r{spec_revision}.pptx",
            owner_id=owner_id,
            created_by_run_id=f"task:{task_id}",
            source_lineage=lineage,
        )
        await session.commit()
        pptx_artifact_id = str(pptx_row.id)

    await _emit(publisher, task_id, seq, "artifact", {"kind": "pptx", "artifact_id": pptx_artifact_id, "bytes": len(pptx_bytes)})

    if qa_view is not None:
        # QA 行绑定最终产物：卡门通过轮已判定同一字节，这里仅补 artifact_id 归档
        # （检测器确定性纯函数，同字节必得同结论；结果以本轮判定为准）。
        async with session_factory()() as session:
            qa_view = await qa_service.run_full_qa(
                session,
                deck_id=uuid.UUID(deck_id),
                spec_revision=spec_revision,
                pptx_bytes=pptx_bytes,
                pages_png=page_pngs,
                plan=plan,
                artifact_id=uuid.UUID(pptx_artifact_id),
            )
            await session.commit()

    pdf_artifact_id: str | None = None
    if fmt == "pdf":
        async with session_factory()() as session:
            await _ensure_not_cancelled(session, task_id)
        pdf = None
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                pdf = await engine.export_pdf(pptx_bytes, file_stem=f"deck-r{spec_revision}")
                break
            except (EngineError, EngineUnavailable) as exc:
                last_exc = exc
                # PowerPoint COM 是单实例桌面应用：上一次未干净退出时 Open 会偶发失败，
                # 重试一次是工程处理；两次都失败就是真实失败（不产假 PDF）。
                await _emit(publisher, task_id, seq, "stage", {"stage": "pdf_retry", "attempt": attempt + 1, "reason": str(exc)[:180]})
                await asyncio.sleep(8.0)
        if pdf is None:
            raise EngineError(f"8201 PDF 导出连续 2 次失败: {last_exc}")
        pdf_bytes = pdf["pdf_bytes"]
        if not pdf_bytes.startswith(b"%PDF-"):
            raise EngineError("8201 返回的不是 PDF 字节（拒绝改后缀冒充）")
        pdf_lineage = {**lineage, "derived_from": [*lineage["derived_from"], f"pptx:{pptx_artifact_id}"],
                       "engine": {**lineage["engine"], "pdf_engine": pdf.get("engine"), "page_count": pdf.get("page_count")}}
        async with session_factory()() as session:
            pdf_row = await register_artifact(
                session,
                store=store,
                spec_kind="teaching_deck",
                spec_id=deck_id,
                spec_revision=spec_revision,
                kind="pdf",
                media_type=_PDF_MEDIA,
                data=pdf_bytes,
                filename=f"{deck_stem}-课件-r{spec_revision}.pdf",
                owner_id=owner_id,
                created_by_run_id=f"task:{task_id}",
                source_lineage=pdf_lineage,
            )
            await session.commit()
            pdf_artifact_id = str(pdf_row.id)
        await _emit(publisher, task_id, seq, "artifact", {"kind": "pdf", "artifact_id": pdf_artifact_id, "bytes": len(pdf_bytes)})

    # qa_status 显式三态（写 tasks.result，不落 export_jobs —— V2 链无该表）：
    # pass = QA 通过；skipped = QA 渲染不可用的显式放行（不得静默冒充通过）。
    if qa_view is not None:
        qa_status: dict[str, Any] = {
            "status": "pass", "rounds": gate_round, "summary": qa_view.get("summary"),
        }
    else:
        qa_status = {"status": "skipped", "rounds": gate_round, "reason": qa_skipped_reason}

    result = {
        "spec_kind": "teaching_deck",
        "deck_id": deck_id,
        "spec_revision": spec_revision,
        "pptx_artifact_id": pptx_artifact_id,
        "pdf_artifact_id": pdf_artifact_id,
        "qa_status": qa_status,
        "engine_job_id": engine_job_id,
        "engine_job_status": job.get("status"),
    }
    async with session_factory()() as session:
        row = (await session.execute(select(Tasks).where(Tasks.id == uuid.UUID(task_id)))).scalar_one_or_none()
        if row is not None:
            row.result = result
            await session.commit()


def engine_artifact_ref(artifact: dict[str, Any]) -> str:
    """8200 artifact 的稳定引用串（血缘里不写本地路径，只写内容寻址引用）。"""
    return f"{artifact.get('object_key')}#{str(artifact.get('sha256'))[:12]}"


def _locator_label(view: Any) -> str:
    """SourceView → 教师可读溯源标签：教材 · 章节 · chunk 主题（R6-planfix）。

    页码不渲染：切片源下 textbook_sections.page_start/end 语义为 chunk 行号而非页码
    （app/textbook_v2/models.py 明示）——印出来即假页码，踩教材零编造红线。
    """
    book = "·".join(dict.fromkeys(p for p in str(view.textbook or "").split("·") if p))
    out = [book] if book else []
    section_title = str(getattr(view, "section_title", "") or "").strip()
    if section_title:
        out.append(section_title)
    theme = _chunk_theme(str(getattr(view, "text_preview", "") or ""))
    if theme and theme != section_title:
        out.append(theme)
    return " · ".join(out)


def _chunk_theme(preview: str) -> str:
    """chunk 文本主题 = 首个 markdown 标题行（剥 # + 数学 Unicode 净化，零裸源码）。"""
    from app.teacher_prep.specs.math_text import latex_to_unicode
    from app.teacher_prep.text_safety import strip_edit_markers

    for ln in preview.splitlines():
        t = re.sub(r"^#+\s*", "", ln.strip()).strip()
        if t:
            return latex_to_unicode(strip_edit_markers(t)).strip()[:24]
    return ""


async def _resolve_locator_labels(spec: Any) -> dict[str, str]:
    """把 spec 全部 SourceLocator 解析为教师可读标签（md 追溯附录用；失败不产标签）。

    权限语义复用 textbook_v2.locator.resolve_locator（rights visible + 租户隔离）；
    解析不到的 locator 不进教师产物（只计未解析数，绝不回退打印机器串）。
    """
    from app.textbook_v2.locator import SourceLocator, resolve_locator
    from app.teacher_prep.specs.doc_render import collect_source_refs

    labels: dict[str, str] = {}
    async with session_factory()() as session:
        for ref in collect_source_refs(spec):
            loc = SourceLocator.from_ref(ref)
            if loc is None:
                continue
            try:
                view = await resolve_locator(session, loc)
            except Exception:  # noqa: BLE001 - 单条解析失败不影响其余溯源
                continue
            if view is None:
                continue
            label = _locator_label(view)
            if label:
                labels[ref] = label
    return labels


async def _run_lesson_plan_export(task_id: str, publisher: EventPublisher, payload: dict[str, Any]) -> None:
    from app.teacher_prep.specs.doc_render import (
        render_lesson_plan_docx,
        render_lesson_plan_markdown,
        render_lesson_plan_pdf,
    )
    from app.teacher_prep.specs.lesson_plan_schema import LessonPlanSpec
    from app.teacher_prep.specs.models import LessonPlanSpecRow

    spec_id = str(payload.get("spec_id") or "")
    fmt = str(payload.get("format") or "markdown")
    owner_id = uuid.UUID(str(payload["owner_id"])) if payload.get("owner_id") else None
    seq: dict[str, int] = {"n": 0}

    async with session_factory()() as session:
        row = (
            await session.execute(
                select(LessonPlanSpecRow).where(LessonPlanSpecRow.id == uuid.UUID(spec_id))
            )
        ).scalar_one_or_none()
        if row is None:
            raise RuntimeError(f"LessonPlanSpec {spec_id} 不存在")
        spec = LessonPlanSpec.model_validate(row.body)
        lineage_base = {
            "spec_id": spec_id,
            "spec_revision": row.revision,
            "approved_blueprint_id": str(row.blueprint_id),
            "blueprint_revision": row.approved_blueprint_revision,
            "context_snapshot_hash": row.context_snapshot_hash,
            "prompt_ref": (row.lineage or {}).get("prompt_ref"),
            "evidence_refs": list((row.lineage or {}).get("evidence_refs") or []),
        }

    await _emit(publisher, task_id, seq, "stage", {"stage": "render", "format": fmt, "spec_kind": "lesson_plan"})
    if fmt == "pdf":
        data, filename = render_lesson_plan_pdf(spec)
        kind, media = "pdf", _PDF_MEDIA
        if not data.startswith(b"%PDF-"):
            raise RuntimeError("教案 PDF 渲染结果不是 PDF 字节")
    elif fmt == "markdown":
        # R6-planfix：md 追溯附录 = 人类可读教材溯源（解析 locator → 标签；机器串零进产物）
        locator_labels = await _resolve_locator_labels(spec)
        text, filename = render_lesson_plan_markdown(spec, meta={"locator_labels": locator_labels})
        data, kind, media = text.encode("utf-8"), "markdown", _MD_MEDIA
    elif fmt == "docx":
        data, filename = render_lesson_plan_docx(spec)
        kind, media = "docx", _DOCX_MEDIA
        # docx 是 OOXML 包：必须以 PK zip 魔数开头（拒绝改后缀冒充）
        if not data.startswith(b"PK\x03\x04"):
            raise RuntimeError("教案 DOCX 渲染结果不是 OOXML 包")
    else:
        raise RuntimeError(f"教案格式 {fmt} 不受支持（仅 markdown/pdf/docx）")

    async with session_factory()() as session:
        await _ensure_not_cancelled(session, task_id)

    async with session_factory()() as session:
        art = await register_artifact(
            session,
            store=get_storage(),
            spec_kind="lesson_plan",
            spec_id=spec_id,
            spec_revision=spec.revision,
            kind=kind,
            media_type=media,
            data=data,
            filename=filename,
            owner_id=owner_id,
            created_by_run_id=f"task:{task_id}",
            source_lineage={
                **lineage_base,
                "derived_from": [],
                "engine": {
                    "service": "8100",
                    "renderer": {"pdf": "reportlab", "docx": "python-docx", "markdown": "markdown"}[kind],
                },
            },
        )
        await session.commit()
        artifact_id = str(art.id)

    await _emit(publisher, task_id, seq, "artifact", {"kind": kind, "artifact_id": artifact_id, "bytes": len(data)})
    async with session_factory()() as session:
        row = (await session.execute(select(Tasks).where(Tasks.id == uuid.UUID(task_id)))).scalar_one_or_none()
        if row is not None:
            row.result = {
                "spec_kind": "lesson_plan",
                "spec_id": spec_id,
                "spec_revision": spec.revision,
                "artifact_id": artifact_id,
                "kind": kind,
                "bytes": len(data),
            }
            await session.commit()


async def export_deck_v2(task_id: str, *, publisher: EventPublisher, user_id: str) -> None:
    payload, _ = await _payload_of(task_id)
    await _run_deck_export(task_id, publisher, payload)


async def export_lesson_plan_v2(task_id: str, *, publisher: EventPublisher, user_id: str) -> None:
    payload, _ = await _payload_of(task_id)
    await _run_lesson_plan_export(task_id, publisher, payload)
