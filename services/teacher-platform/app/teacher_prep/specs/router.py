"""T24 教案（LessonPlanSpec）HTTP 面：块级编辑 / 修订 / 撤销 / 局部重生成 / 导出。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.core.errors import ApiError, ErrorCode
from app.core.response import ok
from app.core.security import UserToken
from app.ppt_v2.service import export as export_service
from app.storage import artifacts as artifact_service
from app.teacher_prep.specs import edit_service
from app.teacher_prep.specs import patch as patch_domain


async def _guard_spec_path(
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """router 级教案归属闸门（T25 P0-5 读越权 / P0-6 回滚写越权）。

    与 deck 同源：归属由 projection/artifact 反查，无证据即拒。
    一处覆盖全部 `{spec_id}` 路由（当前 8 条），后续新增不会漏。
    """
    spec_id = request.path_params.get("spec_id")
    if not spec_id:
        return
    allowed, reason = await artifact_service.spec_access(
        session,
        spec_id=str(spec_id),
        actor_id=user.id,
        roles=[user.role] if user.role else None,
    )
    if not allowed:
        raise ApiError(ErrorCode.FORBIDDEN, f"非本人教案或无权访问（{reason}）")


router = APIRouter(
    prefix="/artifacts/lesson-plans",
    tags=["t24-lesson-plan"],
    dependencies=[Depends(_guard_spec_path)],
)

_REGEN_SYSTEM = (
    "你是中学数学教研助手。只重写教师指定的那一个教案块，其余块保持不变；"
    "输出单个 JSON 对象，键为 text（块正文，不超过 300 字）。不要输出解释。"
)


class PatchBody(BaseModel):
    expected_revision: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=300)
    source: str = Field(default="human", pattern="^(human|ai|regen)$")
    operations: list[dict[str, Any]] = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, max_length=120)


class ApplyBody(BaseModel):
    expected_revision: int = Field(ge=0)


class RejectBody(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class RevertBody(BaseModel):
    expected_revision: int = Field(ge=0)
    summary: str | None = Field(default=None, max_length=300)


class RegenBody(BaseModel):
    expected_revision: int = Field(ge=0)
    target: dict[str, Any]
    instruction: str = Field(min_length=1, max_length=2000)
    refs: dict[str, Any]


class ExportBody(BaseModel):
    format: str = Field(min_length=1, max_length=16)
    expected_revision: int = Field(ge=0)


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", "")


@router.get("/{spec_id}")
async def get_lesson_plan(
    spec_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await edit_service.load_spec_row(session, spec_id)
    await edit_service.ensure_snapshot(session, row)
    await session.commit()
    return ok(edit_service.spec_view(row), request_id=_rid(request))


@router.get("/{spec_id}/revisions")
async def list_revisions(
    spec_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await edit_service.list_revisions(session, spec_id)
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/{spec_id}/revisions/{revision}/revert")
async def revert_revision(
    spec_id: str,
    revision: int,
    body: RevertBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await edit_service.revert_to_revision(
        session, spec_id, revision, expected_revision=body.expected_revision,
        actor_id=user.id, summary=body.summary,
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/{spec_id}/patches")
async def create_patch(
    spec_id: str,
    body: PatchBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await edit_service.create_proposal(
        session,
        spec_id,
        expected_revision=body.expected_revision,
        summary=body.summary,
        source=body.source,
        operations=body.operations,
        actor_id=user.id,
        idempotency_key=body.idempotency_key,
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/{spec_id}/patches/{patch_id}/apply")
async def apply_patch(
    spec_id: str,
    patch_id: str,
    body: ApplyBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await edit_service.apply_proposal_by_id(
        session, spec_id, patch_id, expected_revision=body.expected_revision, actor_id=user.id
    )
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/{spec_id}/patches/{patch_id}/reject")
async def reject_patch(
    spec_id: str,
    patch_id: str,
    body: RejectBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await edit_service.reject_proposal(session, spec_id, patch_id, reason=body.reason)
    await session.commit()
    return ok(data, request_id=_rid(request))


@router.post("/{spec_id}/regen")
async def regenerate_block(
    spec_id: str,
    body: RegenBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from app.ppt_v2.service.regen import get_ai_adapter

    target = body.target or {}
    if target.get("kind") != "block" or not target.get("block_id"):
        raise ApiError(ErrorCode.VALIDATION, "regen.target 必须是 {kind:'block', block_id}")
    block_id = str(target["block_id"])

    row = await edit_service.load_spec_row(session, spec_id)
    spec = edit_service.spec_of(row)
    if body.expected_revision != row.revision:
        raise await edit_service._conflict(  # noqa: SLF001
            session, row, expected_revision=body.expected_revision, operations=[]
        )

    refs = body.refs or {}
    missing = [k for k in ("approved_blueprint_id", "blueprint_revision", "spec_revision", "prompt_ref") if not refs.get(k)]
    if missing:
        raise ApiError(ErrorCode.VALIDATION, f"refs 缺少必填项: {missing}")
    if str(refs["approved_blueprint_id"]) != str(spec.blueprint_ref.blueprint_id):
        raise ApiError(ErrorCode.CONFLICT, "refs.approved_blueprint_id 与教案的上游蓝图不一致（不可偷换上游事实）")
    if int(refs["spec_revision"]) != body.expected_revision:
        raise ApiError(ErrorCode.CONFLICT, "refs.spec_revision 不是本次提交所基于的版本")

    blocks = {b["block_id"]: b for b in patch_domain.lesson_plan_blocks(spec)}
    block = blocks.get(block_id)
    if block is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"教案块 {block_id} 不存在")

    ai = get_ai_adapter()
    messages = [
        {"role": "system", "content": _REGEN_SYSTEM},
        {
            "role": "user",
            "content": "\n".join(
                [
                    f"块标识：{block_id}（{block['label_zh']}）",
                    f"当前内容：{block['text']}",
                    f"教师要求：{body.instruction}",
                    f"教学依据：{', '.join(refs.get('source_refs') or spec.source_refs or []) or '（无）'}",
                    f"证据引用：{', '.join(refs.get('evidence_refs') or []) or '（无）'}",
                ]
            ),
        },
    ]
    try:
        reply = await ai.chat(messages, use_case="lesson_plan_regen")
    except Exception as exc:  # noqa: BLE001 - provider 失败诚实报出
        raise ApiError(
            ErrorCode.DEPENDENCY_UNAVAILABLE, f"重生成 provider 调用失败: {type(exc).__name__}: {exc}"
        ) from exc

    import json

    text = reply.text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1] if "\n" in text else text
    try:
        new_text = str(json.loads(text).get("text") or "").strip()
    except (ValueError, AttributeError):
        new_text = text[:2000].strip()
    if not new_text:
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "重生成 provider 返回空内容")

    operations = [
        {"op": "set_block", "target": block_id, "field": "text", "value": new_text, "before": block["text"]}
    ]
    proposal = await edit_service.create_proposal(
        session,
        spec_id,
        expected_revision=body.expected_revision,
        summary=f"局部重生成 block {block_id}",
        source="regen",
        operations=operations,
        instruction=body.instruction,
        refs=refs,
        actor_id=user.id,
    )
    proposal["target"] = {"kind": "block", "block_id": block_id}
    proposal["changed_block_ids"] = [block_id]
    proposal["refs_echo"] = refs
    proposal["provider"] = {"model": reply.model}
    await session.commit()
    return ok(proposal, request_id=_rid(request))


@router.post("/{spec_id}/exports")
async def start_export(
    spec_id: str,
    body: ExportBody,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await edit_service.load_spec_row(session, spec_id)
    if body.expected_revision != row.revision:
        raise await edit_service._conflict(  # noqa: SLF001
            session, row, expected_revision=body.expected_revision, operations=[]
        )
    # G12 前置校验：空内容教案拒绝导出并给出原因（前端按钮同口径禁用）
    blocks = patch_domain.lesson_plan_blocks(edit_service.spec_of(row))
    if not blocks:
        raise ApiError(ErrorCode.VALIDATION, "教案尚无内容，无法导出")
    data = await export_service.submit_lesson_plan_export(
        session, user_id=user.id, spec_id=spec_id, fmt=body.format
    )
    return ok(data, request_id=_rid(request))


@router.get("/{spec_id}/artifacts")
async def list_plan_artifacts(
    spec_id: str,
    request: Request,
    user: UserToken = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """最近真实导出产物摘要（大小/时间）——S6 教案导出卡「大小」的唯一真实来源。

    反空壳：只回真实 artifact 元数据（size_bytes = 实际文件字节）；从未导出过则为空，
    由前端诚实省略大小，禁止估算。
    """
    row = await edit_service.load_spec_row(session, spec_id)
    blocks = patch_domain.lesson_plan_blocks(edit_service.spec_of(row))
    artifacts: dict[str, Any] = {}
    for kind in ("markdown", "pdf", "docx"):
        art = await export_service.latest_artifact(
            session, spec_kind="lesson_plan", spec_id=spec_id, kind=kind
        )
        if art is not None:
            artifacts[kind] = {
                "artifact_id": str(art.id),
                "kind": art.kind,
                "size_bytes": art.size_bytes,
                "created_at": art.created_at.isoformat() if art.created_at else None,
                "file_name": (art.acl or {}).get("filename"),
            }
    return ok(
        {"spec_id": spec_id, "blocks": len(blocks), "artifacts": artifacts},
        request_id=_rid(request),
    )
