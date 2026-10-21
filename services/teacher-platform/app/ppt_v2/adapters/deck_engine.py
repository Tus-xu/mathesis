"""deck 生产引擎客户端（T24）：8100 → 8200（VPL 生成）→ 8201（渲染/PDF）。

为什么不把渲染搬进 8100：R21 已把「python-pptx + VPL」定为唯一生产引擎并落在
8200/8201。8100 只做编辑与编排，引擎不变——否则 T24 会悄悄换掉 PPT 生产引擎。

失败一律显式：网络/引擎错误抛 EngineUnavailable，绝不返回空字节或假产物。
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.core.config import get_settings

JOB_GENERATE = "ppt.deck.generate"
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "DLQ"}


class EngineError(RuntimeError):
    """引擎侧明确拒绝（非瞬态，重试无用）。"""


class EngineUnavailable(RuntimeError):
    """引擎不可达/超时（可重试）。"""


def _unwrap(payload: Any) -> Any:
    """8200/8201 都用 {code,message,data} 信封；非 0 视为引擎侧拒绝。"""
    if isinstance(payload, dict) and "code" in payload:
        if int(payload.get("code", 0)) != 0:
            raise EngineError(f"{payload.get('message')} (code={payload.get('code')})")
        return payload.get("data")
    return payload


class DeckEngineClient:
    def __init__(self, *, timeout_s: float = 60.0) -> None:
        settings = get_settings()
        self._studio_base = settings.ppt_studio_base_url.rstrip("/")
        self._studio_token = settings.ppt_studio_internal_token
        self._presenton_base = settings.presenton_base_url.rstrip("/")
        self._presenton_token = settings.presenton_service_token
        self._timeout = timeout_s

    # ── 8200 ────────────────────────────────────────────────────────────────
    def _studio_headers(self) -> dict[str, str]:
        if not self._studio_token:
            raise EngineUnavailable("未配置 PPT_STUDIO_INTERNAL_TOKEN，无法调用 8200")
        return {"Authorization": f"Bearer {self._studio_token}"}

    async def _studio(self, method: str, path: str, *, json_body: dict | None = None) -> Any:
        url = f"{self._studio_base}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(method, url, json=json_body, headers=self._studio_headers())
        except httpx.HTTPError as exc:
            raise EngineUnavailable(f"8200 不可达: {exc}") from exc
        if resp.status_code == 401:
            raise EngineError("8200 拒绝凭据（internal token 无效）")
        if resp.status_code >= 400:
            raise EngineError(f"8200 {method} {path} → HTTP {resp.status_code}: {resp.text[:300]}")
        return _unwrap(resp.json())

    async def ingest_deck(
        self, spec: dict[str, Any], assets_inline: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """assets_inline: {asset_id: base64} —— 8200 生成时要求资产字节在场，且会校验 sha256。"""
        body: dict[str, Any] = {"spec": spec}
        if assets_inline:
            body["assets_inline"] = assets_inline
        return await self._studio("POST", "/api/ppt-studio-v2/v1/decks/ingest", json_body=body)

    async def submit_generate(self, deck_id: str) -> dict[str, Any]:
        # deadline 900s 与 8100 导出轮询上限（export_artifact_v2._poll_engine_job）对齐：
        # 引擎先于客户端到点（DLQ），8100 看到的是引擎侧真实终态而非客户端盲超时。
        return await self._studio(
            "POST",
            "/api/ppt-studio-v2/v1/jobs",
            json_body={"job_type": JOB_GENERATE, "payload": {"deck_id": deck_id}, "timeout_s": 900},
        )

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return await self._studio("GET", f"/api/ppt-studio-v2/v1/jobs/{job_id}")

    async def list_job_events(self, job_id: str) -> list[dict[str, Any]]:
        """8200 作业 durable 事件（页级 checkpoint 进度；与 get_job 同一鉴权/信封）。"""
        data = await self._studio("GET", f"/api/ppt-studio-v2/v1/jobs/{job_id}/events")
        return list(data or [])

    async def list_artifacts(self, deck_id: str) -> list[dict[str, Any]]:
        data = await self._studio("GET", f"/api/ppt-studio-v2/v1/decks/{deck_id}/artifacts")
        return list(data or [])

    async def artifact_bytes(self, artifact_id: str) -> bytes:
        url = f"{self._studio_base}/api/ppt-studio-v2/v1/artifacts/{artifact_id}/content"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=self._studio_headers())
        except httpx.HTTPError as exc:
            raise EngineUnavailable(f"8200 内容取回失败: {exc}") from exc
        if resp.status_code >= 400:
            raise EngineError(f"8200 artifact content → HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.content

    # ── 8201 ────────────────────────────────────────────────────────────────
    async def export_pdf(self, pptx_bytes: bytes, *, file_stem: str) -> dict[str, Any]:
        if not self._presenton_token:
            raise EngineUnavailable("未配置 PRESENTON_SERVICE_TOKEN，无法调用 8201")
        url = f"{self._presenton_base}/api/v1/deck/export/pdf"
        body = {"pptx_b64": base64.b64encode(pptx_bytes).decode("ascii"), "file_stem": file_stem}
        try:
            async with httpx.AsyncClient(timeout=max(self._timeout, 300.0)) as client:
                resp = await client.post(
                    url, json=body, headers={"X-Presenton-Service-Token": self._presenton_token}
                )
        except httpx.HTTPError as exc:
            raise EngineUnavailable(f"8201 不可达: {exc}") from exc
        if resp.status_code >= 400:
            raise EngineError(f"8201 PDF 导出失败 → HTTP {resp.status_code}: {resp.text[:300]}")
        data = _unwrap(resp.json())
        if not isinstance(data, dict) or not data.get("pdf_b64"):
            raise EngineError("8201 PDF 导出返回体缺少 pdf_b64")
        data["pdf_bytes"] = base64.b64decode(data["pdf_b64"])
        return data


def get_deck_engine() -> DeckEngineClient:
    return DeckEngineClient()
