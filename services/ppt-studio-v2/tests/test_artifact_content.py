"""T24（8200）：artifact 字节内容端点 GET /api/ppt-studio-v2/v1/artifacts/{id}/content。

冻结契约：docs/compose/spec/t24-artifact-edit-export.md S2.6 —— 8100 导出链路必须从 8200 取
真实 PPTX 字节（并校验 sha256 后再落 8100 MinIO）。

证据口径：真实 LocalArtifactStore + 真实 PptV2Artifact 行 + 真实 PPTX（python-pptx 生成，
见 tests/fixtures/t24-export-sample.pptx）；不 mock 存储、不伪造字节、不跳过 sha256 校验。
"""
from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.artifact_store import LocalArtifactStore
from app.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.persistence.models import PptV2Artifact

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "t24-export-sample.pptx"
PPTX_MEDIA = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
INTERNAL_TOKEN = "test-internal-token"  # conftest.py 注入的环境值（env 优先于 .env）
URL = "/api/ppt-studio-v2/v1/artifacts/{aid}/content"


def _auth() -> dict[str, str]:
    """内部服务令牌（既有 require_auth 通道；不新增鉴权机制）。"""
    return {"Authorization": f"Bearer {INTERNAL_TOKEN}"}


def _pptx_bytes() -> bytes:
    """真实 PPTX 字节，并自证是 OOXML 包（不是改名/伪造）。"""
    data = FIXTURE.read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert "[Content_Types].xml" in names, "fixture 不是 OOXML 包"
    assert "ppt/presentation.xml" in names, "fixture 不是真实 PPTX"
    assert data[:2] == b"PK"
    return data


@pytest.fixture
def artifact_root(tmp_path, monkeypatch) -> Iterator[Path]:  # noqa: ANN001
    """把 8200 artifact root 指向临时目录（路由内 LocalArtifactStore() 现读 settings）。"""
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _make_app(engine) -> FastAPI:  # noqa: ANN001
    """只覆盖 get_session（sqlite）；require_auth 保持真实（401 负例必须有意义）。"""
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override():
        async with sf() as s:
            yield s

    app = create_app()
    app.dependency_overrides[get_session] = _override
    return app


async def _seed(
    session: AsyncSession,
    root: Path,
    data: bytes,
    *,
    kind: str = "DECK_PPTX",
    object_key: str | None = None,
    registered_sha: str | None = None,
) -> tuple[uuid.UUID, str, str]:
    """落真实字节 + 插真实登记行；返回 (artifact_id, object_key, 登记 sha256)。"""
    sha = hashlib.sha256(data).hexdigest()
    key = object_key or f"pps2/decks/{sha}.pptx"
    LocalArtifactStore(root).put(key, data)
    row = PptV2Artifact(
        kind=kind,
        object_key=key,
        sha256=registered_sha or sha,
        size_bytes=len(data),
    )
    session.add(row)
    await session.commit()
    return row.id, key, row.sha256


async def _get(app: FastAPI, aid: str, headers: dict[str, str] | None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(URL.format(aid=aid), headers=headers or {})


# ── 正例：字节 + sha256 头 + 媒体类型 ──────────────────────────────


async def test_content_returns_verified_bytes_and_headers(engine, session, artifact_root) -> None:  # noqa: ANN001
    data = _pptx_bytes()
    aid, _key, sha = await _seed(session, artifact_root, data)
    r = await _get(_make_app(engine), str(aid), _auth())
    assert r.status_code == 200, r.text
    assert r.content == data
    assert hashlib.sha256(r.content).hexdigest() == sha
    assert r.headers["x-content-sha256"] == sha
    assert r.headers["x-artifact-kind"] == "DECK_PPTX"
    assert r.headers["content-type"].startswith(PPTX_MEDIA)


async def test_content_sha256_header_matches_row_for_png_kind(engine, session, artifact_root) -> None:  # noqa: ANN001
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    aid, _key, sha = await _seed(session, artifact_root, data, kind="PAGE_PNG", object_key=None)
    r = await _get(_make_app(engine), str(aid), _auth())
    assert r.status_code == 200, r.text
    assert r.content == data
    assert r.headers["x-content-sha256"] == sha
    assert r.headers["x-artifact-kind"] == "PAGE_PNG"
    assert r.headers["content-type"].startswith("image/png")


# ── 硬不变量：字节漂移绝不发出 ────────────────────────────────────


async def test_content_rejects_byte_drift_500_and_emits_no_bytes(engine, session, artifact_root) -> None:  # noqa: ANN001
    data = _pptx_bytes()
    aid, key, _sha = await _seed(session, artifact_root, data)
    tampered = b"TAMPERED-NOT-A-PPTX-BYTES"
    LocalArtifactStore(artifact_root).path_for(key).write_bytes(tampered)  # 盘上字节被换掉
    r = await _get(_make_app(engine), str(aid), _auth())
    assert r.status_code == 500, r.text
    body = r.json()
    assert body["code"] == 50000
    assert body["error_key"] == "artifact_hash_mismatch"
    assert "sha256" in body["message"]
    # 漂移字节一个都不能发出去
    assert tampered not in r.content
    assert bytes(r.content) != tampered
    assert "x-content-sha256" not in r.headers  # 失败响应不得带成功头（不得暗示校验通过）
    assert "x-artifact-kind" not in r.headers


async def test_content_missing_bytes_404(engine, session, artifact_root) -> None:  # noqa: ANN001
    data = _pptx_bytes()
    aid, key, _sha = await _seed(session, artifact_root, data)
    LocalArtifactStore(artifact_root).path_for(key).unlink()
    r = await _get(_make_app(engine), str(aid), _auth())
    assert r.status_code == 404, r.text
    assert r.json()["error_key"] == "artifact_bytes_missing"


# ── 404 / 401 负例 ────────────────────────────────────────────────


async def test_content_unknown_artifact_404(engine, session, artifact_root) -> None:  # noqa: ANN001
    r = await _get(_make_app(engine), str(uuid.uuid4()), _auth())
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["code"] == 40400
    assert body["error_key"] == "artifact_not_found"


async def test_content_non_uuid_id_404(engine, session, artifact_root) -> None:  # noqa: ANN001
    r = await _get(_make_app(engine), "not-a-uuid", _auth())
    assert r.status_code == 404, r.text
    assert r.json()["error_key"] == "artifact_not_found"


async def test_content_without_auth_401(engine, session, artifact_root) -> None:  # noqa: ANN001
    data = _pptx_bytes()
    aid, _key, _sha = await _seed(session, artifact_root, data)
    r = await _get(_make_app(engine), str(aid), None)
    assert r.status_code == 401, r.text
    assert r.json()["code"] == 40100
    assert r.content != data


async def test_content_wrong_token_401(engine, session, artifact_root) -> None:  # noqa: ANN001
    data = _pptx_bytes()
    aid, _key, _sha = await _seed(session, artifact_root, data)
    r = await _get(_make_app(engine), str(aid), {"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401, r.text
    assert r.json()["code"] == 40100


# ── 媒体类型映射（纯函数，含契约提到的 PLAN_JSON）──────────────────


def test_media_type_map() -> None:
    from app.artifacts import media_type_for_kind

    assert media_type_for_kind("DECK_PPTX") == PPTX_MEDIA
    assert media_type_for_kind("PAGE_PNG") == "image/png"
    assert media_type_for_kind("CONTACT_SHEET") == "image/png"
    assert media_type_for_kind("PLAN_JSON") == "application/json"
    assert media_type_for_kind("UNKNOWN_KIND") == "application/octet-stream"
