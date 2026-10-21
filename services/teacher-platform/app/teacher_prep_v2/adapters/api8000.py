"""Api8000Adapter（R13）：8100 → 8000 的 HTTP 通道（成员守卫 + 幂等下发 + 提交对账）。

职责边界：
- 只调 8000 既有端点：/api/teacher/assignments（create/list/publish/close/archive）、
  ClassMember 只读校验（/api/classes/{class_id} 成员语义）——**不改 8000 任何路径**；
- 携带调用教师 JWT（Bearer passthrough）：8000 既有的 membership/角色校验即“当前
  数据库成员关系拒绝”的裁决点；8100 侧只做转发 + 降级处理；
- 任何跨班访问（成员校验 403/404）→ ContractMembershipDenied，绝不绕过；
- HTTP 客户端失败/5xx → ContractAuthorityUnreachable（fail closed）。

适配层无 ORM、无直连 8000 库（8000 与 8100 为不同 DB，见 PORT-NAMESPACE-REGISTRY）。
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.teacher_prep_v2.adapters.dto import (
    ApiAssignmentCreate,
    ApiAssignmentListResponse,
    ApiAssignmentRow,
    ApiMembershipRow,
)
from app.teacher_prep_v2.adapters.errors import from_http_status
from app.teacher_prep_v2.adapters.mapping import (
    MappedRef,
    MappingRegistry,
    assignment_mapping_key,
    ensure_same_external,
    task_assignment_to_api_create,
)
from app.teacher_prep_v2.contracts.errors import (
    ContractAuthorityUnreachable,
    ContractIdempotencyConflict,
    ContractMembershipDenied,
)
from app.teacher_prep_v2.contracts.models import TaskAssignment


class ApiTransport(Protocol):
    """8000 HTTP 传输抽象（默认 httpx.AsyncClient；测试注入 fake）。"""

    base_url: str

    async def request(
        self, method: str, path: str, *, token: str, json: dict | None = None, params: dict | None = None
    ) -> tuple[int, dict | None]: ...


class HttpxApiTransport(ApiTransport):
    def __init__(self, base_url: str, *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def request(self, method: str, path: str, *, token: str, json: dict | None = None, params: dict | None = None) -> tuple[int, dict | None]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
                resp = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    json=json,
                    params=params,
                )
        except httpx.HTTPError as exc:  # 网络层一律视为权威不可达
            raise ContractAuthorityUnreachable(f"8000 传输失败: {exc}") from exc
        if resp.status_code == 204:
            return resp.status_code, None
        try:
            body: dict | None = resp.json() if resp.content else None
        except ValueError:
            body = None
        return resp.status_code, body


class FakeApiTransport(ApiTransport):
    """契约测试/开发用 fake：按路径返回预置响应，可用于模拟 403/409/5xx。"""

    def __init__(self, base_url: str = "http://api8000.test") -> None:
        self.base_url = base_url
        self.requests: list[tuple[str, str, dict | None]] = []
        self.handler = None  # (method, path, token, json) -> (status, body)

    async def request(self, method: str, path: str, *, token: str, json: dict | None = None, params: dict | None = None) -> tuple[int, dict | None]:
        self.requests.append((method, path, json))
        if self.handler is not None:
            return self.handler(method, path, token, json)
        return 200, {"items": []}


class Api8000Adapter:
    """8100 侧对 8000 的冻结入口：create_assignment（幂等）+ 成员守卫。"""

    SCOPE_ASSIGNMENT = "prep:assignment:8000"
    SCOPE_MEMBERSHIP = "prep:membership:8000"

    def __init__(self, transport: ApiTransport, registry: MappingRegistry) -> None:
        self._transport = transport
        self._registry = registry

    # ── 成员守卫（跨班访问裁决）─────────────────────────────────────────────
    async def require_class_membership(self, *, token: str, class_id: str) -> ApiMembershipRow:
        """裁决当前教师是否仍是该班当前成员（8000 ClassMember 事实，deleted 即拒）。

        优先 `GET /api/classes/{id}`（若 8000 暴露）；该路径不存在时回退
        `GET /api/classes/{id}/members` 并用 JWT `sub` 定位本人成员行。
        任何 403/404/成员不存在 → ContractMembershipDenied。
        """
        status, body = await self._transport.request("GET", f"/api/classes/{class_id}", token=token)
        if status == 200 and body is not None:
            row = _coerce_membership(body, class_id)
        elif status in (403, 404):
            row = await self._membership_via_members(token=token, class_id=class_id)
        elif status >= 500:
            raise ContractAuthorityUnreachable(f"成员校验时 8000 不可达（{status}）")
        else:
            raise ContractAuthorityUnreachable(f"成员校验意外响应（{status}）")
        if not row.confirmed or row.deleted:
            raise ContractMembershipDenied(f"非当前有效成员（class_id={class_id} user_id={row.user_id}）")
        if row.member_role not in ("teacher", "owner", "assistant"):
            raise ContractMembershipDenied(f"角色不允许下发任务（member_role={row.member_role}）")
        await self._registry.ensure(
            self.SCOPE_MEMBERSHIP, f"member:{class_id}:{row.user_id}", str(row.user_id)
        )
        return row

    async def _membership_via_members(self, *, token: str, class_id: str) -> ApiMembershipRow:
        """8000 无班级详情端点时：读成员表，用 token.sub 定位本人。"""
        status, body = await self._transport.request(
            "GET", f"/api/classes/{class_id}/members", token=token
        )
        if status in (403, 404):
            raise ContractMembershipDenied(f"跨班访问被 8000 成员关系拒绝（class_id={class_id}）")
        if status >= 500:
            raise ContractAuthorityUnreachable(f"成员校验时 8000 不可达（{status}）")
        if status != 200 or body is None:
            raise ContractAuthorityUnreachable(f"成员校验意外响应（{status}）")
        user_id = _jwt_sub(token)
        items = body.get("items") if isinstance(body.get("items"), list) else []
        if isinstance(body.get("data"), dict) and isinstance(body["data"].get("items"), list):
            items = body["data"]["items"]
        for item in items:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("userId") or item.get("user_id") or "")
            if iid and user_id and iid != user_id:
                continue
            if not iid and not user_id:
                continue
            return ApiMembershipRow(
                class_id=class_id,
                user_id=iid or user_id or "",
                member_role=str(item.get("memberRole") or item.get("member_role") or "student"),
                confirmed=bool(item.get("confirmed", True)),
                deleted=bool(item.get("deleted", False)),
                nickname_in_class=item.get("nicknameInClass") or item.get("nickname_in_class"),
            )
        raise ContractMembershipDenied(f"非当前有效成员（class_id={class_id} user_id={user_id}）")

    # ── R19 学情证据只读投影（统计最小素材；PII 已在 8000 出口伪名化）─────────
    async def fetch_submission_facts(self, *, token: str, assignment_id: str) -> tuple[int, dict | None]:
        """按作业拉取统计最小素材（GET /api/teacher/assignments/{id}/submission-facts）。

        返回 (status, body) 原始对：403/404 由调用方按成员守卫语义归一
        （本方法保持传输层纯度，与 request() 相同）；5xx/网络失败 → fail closed。
        """
        status, body = await self._transport.request(
            "GET", f"/api/teacher/assignments/{assignment_id}/submission-facts", token=token
        )
        # tel E2E 修复：与本 adapter 其余方法一致地拆 ApiResponse 信封——
        # profile/facts.bundle_from_payload 期望裸载荷（submissions/item_kp_map），
        # 此前拿到 {"code":0,"data":{...}} 信封 → 恒 no_data（sample_size=0）。
        if isinstance(body, dict) and isinstance(body.get("data"), dict):
            body = body["data"]
        return status, body

    # ── 任务下发（幂等创建 → 8000 assignments，draft）+ 显式发布 ─────────────
    async def create_assignment(
        self,
        *,
        token: str,
        assignment: TaskAssignment,
        creator_id: str,
        artifact_id: str | None = None,
        quiz_items: list[dict] | None = None,
        title: str | None = None,
    ) -> MappedRef:
        """把 TaskAssignment 下发到 8000 assignments（幂等；8000 恒建 draft）。

        流程：稳定键 → 查询 registry → 命中且 external_id 一致 → 返回首结果；
              未命中 → 生成 ApiAssignmentCreate（client_assignment_id=稳定键）→ POST；
              8000 409/同 IDP 键重放 → 读回已有行 → ensure 绑定 → 返回。

        tel T3：8000 ``create_assignment`` 恒建 ``status="draft"``（学生不可见），
        发布必须另行调用 :meth:`publish_assignment`（编排器在创建成功后补调）。
        ``artifact_id`` / ``quiz_items``：tel F1 起二选一（内联题组为备课诊断直发路径）；
        两者皆缺时 8000 422 拒绝 → 对账路径 → ContractIdempotencyConflict，fail-closed。
        ``title``：8000 Assignment.title 显示名（缺省回落契约 id）。
        ``creator_id``：8000 从 JWT 裁决，不进入载荷（仅保留调用方签名兼容）。
        """
        key = assignment_mapping_key(assignment)
        existing = await self._registry.resolve(self.SCOPE_ASSIGNMENT, key)
        if existing is not None:
            ensure_same_external(existing, existing.external_id)
            return existing

        payload = task_assignment_to_api_create(
            assignment,
            creator_id=creator_id,
            client_assignment_id=key[:64],
            artifact_id=artifact_id,
            quiz_items=quiz_items,
            title=title,
        )
        status, body = await self._transport.request(
            "POST", "/api/teacher/assignments", token=token, json=payload.model_dump(mode="json")
        )
        if status in (200, 201):
            external_id = _extract_assignment_id(body)
            if not external_id:
                raise ContractAuthorityUnreachable("8000 创建成功但未返回 assignment id")
            return await self._registry.ensure(self.SCOPE_ASSIGNMENT, key, external_id)
        if status in (400, 409, 422):
            # 幂等键碰撞：已有 assignment（client_assignment_id=同键），读回对账
            row = await self._fetch_by_client_key(token, key[:64])
            if row is not None:
                return await self._registry.ensure(self.SCOPE_ASSIGNMENT, key, str(row.id))
            raise ContractIdempotencyConflict(f"8000 拒绝创建且无可对账 assignment（{status}: {body}）")
        raise from_http_status(status, str(body))

    async def create_assignment_inline(
        self,
        *,
        token: str,
        class_id: str,
        title: str,
        quiz_items: list[dict[str, Any]],
        client_assignment_id: str,
        deadline: Any = None,
        assignment_type: str = "homework",
    ) -> str:
        """内联题组 → 8000 ``assignments``（draft），返回 8000 assignment id。

        与备课诊断的 ``create_assignment`` 同一条 8000 契约，但不需要
        ``TaskAssignment`` 契约对象/映射注册表——组卷中心直接选真实题目下发。
        创建恒为 draft（8000 事实）；调用方必须再显式 publish。
        幂等：同 ``client_assignment_id`` 被 8000 拒绝时读回既有行。
        """
        payload = ApiAssignmentCreate(
            class_id=class_id,
            title=str(title)[:200],
            quiz_items=quiz_items,
            deadline=deadline,
            type=assignment_type,  # type: ignore[arg-type]
            client_assignment_id=str(client_assignment_id)[:64],
        )
        status, body = await self._transport.request(
            "POST", "/api/teacher/assignments", token=token, json=payload.model_dump(mode="json")
        )
        if status in (200, 201):
            external_id = _extract_assignment_id(body)
            if not external_id:
                raise ContractAuthorityUnreachable("8000 创建作业成功但未返回 assignment id")
            return external_id
        if status in (400, 409, 422):
            row = await self._fetch_by_client_key(token, str(client_assignment_id)[:64])
            if row is not None:
                return str(row.id)
            raise ContractIdempotencyConflict(
                f"8000 拒绝创建内联题组作业且无可对账 assignment（{status}: {_detail_of(body)}）"
            )
        raise from_http_status(status, _detail_of(body))

    async def publish_assignment(
        self,
        *,
        token: str,
        assignment_id: str,
        client_request_id: str,
        idempotency_key: str | None = None,
    ) -> str:
        """把 8000 draft assignment 推到 published（tel T3 新增转发方法）。

        POST /api/teacher/assignments/{assignment_id}/publish（8000 `_assignment_transition`：
        draft→published；已 published 时幂等重放，重复调用安全）。
        成功返回 assignment id；4xx/5xx → from_http_status 契约错误（fail-closed，
        由编排器 `_publish_fail` 落 FAILED，绝不误报成功）。
        """
        payload: dict[str, Any] = {"client_request_id": str(client_request_id)[:128]}
        if idempotency_key:
            payload["idempotency_key"] = str(idempotency_key)[:256]
        status, body = await self._transport.request(
            "POST", f"/api/teacher/assignments/{assignment_id}/publish", token=token, json=payload
        )
        if status in (200, 201):
            return _extract_assignment_id(body) or assignment_id
        raise from_http_status(status, str(body))

    # ── 课堂发布（Phase B）：TeachingDeckSpec 快照 → 8000 班级课件 ────────────
    async def create_classroom_courseware(
        self, *, token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """POST /api/teacher/classroom-coursewares（8000 课堂课件创建契约）。

        8000 侧创建 ``TeachingArtifact(scene='classroom_courseware')`` 并异步准备
        讲稿/随堂练习；本方法只负责传输 + 信封解析，**绝不伪造成功**：

        - 非 2xx → :func:`from_http_status`（403/404 成员拒绝、5xx/网络 → 权威不可达）；
        - 2xx 但 ``code != 0`` → 按 8000 错误码段映射（与 from_http_status 同语义）；
        - 2xx / ``code == 0`` 但缺 ``courseware_id`` → ContractAuthorityUnreachable。

        成功返回归一化 ``{courseware_id, status}``（status 缺省 ``preparing``，
        与设计 D3「立即返回 preparing，学生只看到 published」一致）。
        """
        status, body = await self._transport.request(
            "POST", "/api/teacher/classroom-coursewares", token=token, json=payload
        )
        if status not in (200, 201):
            raise from_http_status(status, _detail_of(body))
        if not isinstance(body, dict):
            raise ContractAuthorityUnreachable("8000 创建课堂课件响应为空（契约信封缺失）")
        code = body.get("code")
        if code is not None and code != 0:
            raise from_http_status(_http_status_of_envelope_code(code), _detail_of(body))
        data = body.get("data")
        row = data if isinstance(data, dict) else body
        courseware_id = row.get("courseware_id") or row.get("id")
        if not isinstance(courseware_id, str) or not courseware_id:
            raise ContractAuthorityUnreachable("8000 创建课堂课件成功但未返回 courseware_id")
        return {
            "courseware_id": courseware_id,
            "status": str(row.get("status") or "preparing"),
        }

    async def _fetch_by_client_key(self, token: str, client_assignment_id: str) -> ApiAssignmentRow | None:
        """幂等对账读回：GET /api/teacher/assignments?client_assignment_id=…（tel F1）。

        8000 真实契约（本轮核实）：
        - 响应为 ``_ok`` 信封 ``{code,message,data:{assignments:[...]},request_id}``
          （teacher/router.py list_assignments → _ok({"assignments": …})）；
        - 行键为 ``assignment_id``（_serialize_assignment），无 ``id``/``creator_id``；
        - ``client_assignment_id`` 过滤已由 8000 列表端点支持（additive）。
        信封/键形态按宽松读取归一（fail-safe：异常载荷按无结果处理）。
        """
        status, body = await self._transport.request(
            "GET", "/api/teacher/assignments", token=token,
            params={"client_assignment_id": client_assignment_id},
        )
        if status != 200 or body is None:
            return None
        if isinstance(body, dict) and isinstance(body.get("data"), dict):
            body = body["data"]  # 拆 _ok 信封
        if not isinstance(body, dict):
            return None
        rows = body.get("assignments")
        if not isinstance(rows, list):  # 兼容 {items:[…]} 形态（测试 fake/旧约定）
            rows = body.get("items")
        if not isinstance(rows, list):
            return None
        normalized: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item.setdefault("id", item.get("assignment_id"))  # 8000 行键 assignment_id → id
            normalized.append(item)
        try:
            page = ApiAssignmentListResponse.model_validate({"items": normalized})
        except Exception:  # noqa: BLE001 —— 8000 列表载荷异常按无结果处理（fail safe）
            return None
        for row in page.items:
            if row.client_assignment_id == client_assignment_id:
                return row
        return None


def _extract_assignment_id(body: dict | None) -> str | None:
    """从 8000 创建响应提取 assignment id（兼容 {id}/{assignment}/{data:{id}} 形态）。"""
    if not body:
        return None
    for key in ("id", "assignment_id", "assignment"):
        val = body.get(key)
        if isinstance(val, str):
            return val
        if isinstance(val, dict):
            inner = val.get("id") or val.get("assignment_id")
            if isinstance(inner, str):
                return inner
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("id", "assignment_id"):
            val = data.get(key)
            if isinstance(val, str):
                return val
    return None


# 8000 平台错误码 → HTTP 段（信封 `{code,message,data}` 中 code!=0 时的映射口径）
_ENVELOPE_CODE_TO_HTTP: dict[int, int] = {
    40000: 400,
    40100: 401,
    40101: 401,
    40300: 403,
    40301: 403,
    40400: 404,
    40401: 404,
    40402: 404,
    40900: 409,
    42200: 422,
    42900: 429,
}


def _http_status_of_envelope_code(code: Any) -> int:
    """信封错误码 → HTTP 段；未知码按 502（= 权威不可达，fail closed）。"""
    try:
        value = int(code)
    except (TypeError, ValueError):
        return 502
    return _ENVELOPE_CODE_TO_HTTP.get(value, 502)


def _detail_of(body: dict | None) -> str:
    """错误细节：优先 8000 信封 ``message``/``detail``，退化到原始载荷字符串。"""
    if isinstance(body, dict):
        for key in ("message", "detail"):
            msg = body.get(key)
            if isinstance(msg, str) and msg:
                return msg
    return str(body)


def _coerce_membership(body: dict, class_id: str) -> ApiMembershipRow:
    """把 8000 班级详情/成员响应归一为 ApiMembershipRow（宽容读取，缺失按未确认）。"""
    candidate: dict[str, Any] = {}
    for key in ("membership", "me", "member"):
        val = body.get(key)
        if isinstance(val, dict):
            candidate = val
            break
    if not candidate:
        candidate = body
    return ApiMembershipRow(
        class_id=str(candidate.get("class_id") or class_id)[:36],
        user_id=str(candidate.get("user_id") or candidate.get("user") or "")[:36],
        member_role=str(candidate.get("member_role") or candidate.get("role") or "student"),
        confirmed=bool(candidate.get("confirmed", True)),
        deleted=bool(candidate.get("deleted", candidate.get("deleted_at") is not None)),
        nickname_in_class=candidate.get("nickname_in_class"),
    )


def _jwt_sub(token: str) -> str:
    """从 Bearer JWT payload 读 `sub`（不验签；验签由 8000 完成）。"""
    try:
        import base64
        import json

        parts = token.split(".")
        if len(parts) < 2:
            return ""
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad).decode("utf-8"))
        return str(payload.get("sub") or "")
    except Exception:
        return ""


__all__ = ["Api8000Adapter", "ApiTransport", "HttpxApiTransport", "FakeApiTransport"]
