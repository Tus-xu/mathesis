"""API 请求/响应 schema（B3 平台端点）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class PresignRequest(BaseModel):
    """双形状兼容（审计 #6 契约漂移）：前端契约 {filename, content_type, size, retry_of?}
    （teacherV3Upload.ts:46-49）与旧骨架 {bucket, object_name}。"""
    bucket: str | None = Field(default=None, pattern=r"^teacher-[a-z]+$")
    object_name: str | None = None
    filename: str | None = None
    content_type: str | None = None
    size: int | None = None
    retry_of: str | None = None


class PresignResponse(BaseModel):
    url: str
    object_name: str
    key: str | None = None          # 前端契约键名（= object_name）
    headers: dict | None = None
    demo: bool | None = None


class DemoStreamResponse(BaseModel):
    pass  # SSE 流，非 JSON 信封
