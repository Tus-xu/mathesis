"""API 层流程测试（依赖覆盖为 SQLite）：submit(201) → get → cancel → DLQ 列表。"""
from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import require_auth
from app.db.session import get_session
from app.main import create_app


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-internal-token"}


def _make_app(engine):  # noqa: ANN001
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override():
        async with sf() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[require_auth] = _override
    return app


async def test_submit_get_cancel(engine) -> None:  # noqa: ANN001
    app = _make_app(engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/ppt-studio-v2/v1/jobs",
            headers=_headers(),
            json={
                "job_type": "ppt.deck.prepare",
                "payload": {"presenton_probe": False, "deck_id": "d-x"},
                "max_attempts": 2,
                "timeout_s": 300,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["code"] == 0
        job_id = body["data"]["id"]

        r2 = await client.get(f"/api/ppt-studio-v2/v1/jobs/{job_id}", headers=_headers())
        assert r2.status_code == 200
        assert r2.json()["data"]["status"] == "PENDING"

        r3 = await client.post(f"/api/ppt-studio-v2/v1/jobs/{job_id}/cancel", headers=_headers())
        assert r3.status_code == 200
        assert r3.json()["data"]["cancel_requested"] is True

        r4 = await client.get("/api/ppt-studio-v2/v1/dlq", headers=_headers())
        assert r4.status_code == 200


async def test_submit_unknown_job_type_rejected(engine) -> None:  # noqa: ANN001
    app = _make_app(engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/ppt-studio-v2/v1/jobs",
            headers=_headers(),
            json={"job_type": "evil.type", "payload": {}},
        )
        assert r.status_code == 422
        assert r.json()["code"] == 42200
