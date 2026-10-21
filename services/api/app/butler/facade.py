"""Butler Kernel v2 阶段 6A：HTTP/前端授权契约预接线（Compatibility Facade）

本阶段只完成安全传输链：HTTP 请求的 web_search_opt_in 经 Facade 进入
ButlerRequest。Facade 不被在线 Runtime 调用（v2 未切流），因此不宣称
真实请求已进入 ButlerRuntime。

- 只服务学生 chat：固定 ActorRole.STUDENT + scene="student.chat"，
  不接受调用者传入 scene/role（防止客户端自由决定 Butler 场景）；
- web_search_opt_in 直接透传（默认 False，fail-closed）。
"""

from __future__ import annotations

import uuid

from app.butler.contracts import ActorContext, ActorRole, ButlerRequest


def build_student_chat_butler_request(
    *,
    user_id: uuid.UUID,
    message: str,
    conversation_id: uuid.UUID | None,
    client_request_id: str,
    web_search_opt_in: bool = False,
) -> ButlerRequest:
    """构造学生对话场景的 ButlerRequest（可信场景由后端固定，不接受客户端决定）。"""
    return ButlerRequest(
        actor=ActorContext(user_id=user_id, role=ActorRole.STUDENT),
        message=message,
        scene="student.chat",
        conversation_id=conversation_id,
        client_request_id=client_request_id,
        web_search_opt_in=web_search_opt_in,
    )
