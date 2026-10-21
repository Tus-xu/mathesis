"""8201 Presenton adapter 客户端（8200 → 8201 私有链路，service token 鉴权）。

浏览器/前端绝不直接调用；本客户端是 8200 唯一出站消费方。
8201 不可用时：probe()/render_page()/assemble() 抛 PresentonUnavailable（worker 转为 transient 重试）。
语义约定：
- 503（引擎不可用/模拟故障）→ PresentonUnavailable（transient，可退避重试）；
- 其余非 2xx → EngineRejected（内容/契约问题，重试无益，非 transient）。
"""
from __future__ import annotations

import httpx


class PresentonUnavailable(Exception):
    pass


class EngineRejected(Exception):
    """8201 明确拒绝（非可用性问题）：页面 QA 不合格、装配失败、契约错误等。"""

    def __init__(self, message: str, *, status_code: int = 0, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class PresentonClient:
    def __init__(self, base_url: str, service_token: str, timeout_s: float = 3.0):
        self._base = base_url.rstrip("/")
        self._token = service_token
        self._timeout = timeout_s

    def _headers(self) -> dict[str, str]:
        return {"X-Presenton-Service-Token": self._token}

    async def probe(self) -> None:
        """拉活：8201 私有健康口；失败/非 200 → PresentonUnavailable（transient）。"""
        try:
            async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
                resp = await client.get(
                    f"{self._base}/api/v1/health",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise PresentonUnavailable(f"presenton unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200 or resp.json().get("status") != "ok":
            raise PresentonUnavailable(f"presenton unhealthy (http {resp.status_code})")

    async def _post(self, path: str, body: dict, timeout_s: float) -> dict:
        try:
            async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
                resp = await client.post(f"{self._base}{path}", json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise PresentonUnavailable(f"presenton unreachable: {type(exc).__name__}") from exc
        if resp.status_code == 503:
            raise PresentonUnavailable(f"presenton unavailable (503): {resp.text[:200]}")
        if resp.status_code < 200 or resp.status_code >= 300:
            raise EngineRejected(
                f"presenton rejected {path} (http {resp.status_code})",
                status_code=resp.status_code,
                detail=resp.text[:500],
            )
        return resp.json()

    async def render_page(self, body: dict, *, timeout_s: float = 150.0) -> dict:
        return await self._post("/api/v1/deck/pages/render", body, timeout_s)

    async def assemble(self, body: dict, *, timeout_s: float = 480.0) -> dict:
        return await self._post("/api/v1/deck/assemble", body, timeout_s)

    async def artifact_meta(self, ref: str, *, timeout_s: float = 5.0) -> dict | None:
        """存在性探测：404 → None（checkpoint 失效信号 → 重渲染）。"""
        try:
            async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
                resp = await client.get(
                    f"{self._base}/api/v1/artifacts/{ref}",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise PresentonUnavailable(f"presenton unreachable: {type(exc).__name__}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise PresentonUnavailable(f"artifact meta http {resp.status_code}")
        return resp.json()

    async def engine_version(self, *, timeout_s: float = 5.0) -> str:
        """引擎版本（checkpoint 兼容性判定：版本不同 → 强制重渲染）。"""
        try:
            async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
                resp = await client.get(f"{self._base}/api/v1/health", headers=self._headers())
        except httpx.HTTPError as exc:
            raise PresentonUnavailable(f"presenton unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise PresentonUnavailable(f"presenton unhealthy (http {resp.status_code})")
        return str(resp.json().get("engine", "unknown"))

    async def artifact_bytes(self, ref: str, *, timeout_s: float = 60.0) -> bytes:
        """字节获取（8200 校验/留存产物用）。"""
        try:
            async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
                resp = await client.get(
                    f"{self._base}/api/v1/artifacts/{ref}/content",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise PresentonUnavailable(f"presenton unreachable: {type(exc).__name__}") from exc
        if resp.status_code == 404:
            raise EngineRejected(f"artifact content missing: {ref[:12]}", status_code=404)
        if resp.status_code != 200:
            raise EngineRejected(f"artifact content http {resp.status_code}", status_code=resp.status_code)
        return resp.content
