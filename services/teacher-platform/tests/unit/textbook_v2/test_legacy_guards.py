"""R12 TextbookPage 零新读写守卫测试（静态 + 运行）。

- 静态：生产代码（app/）中只有 allowlist 文件可引用 TextbookPage / textbook_pages，
  新 writer 路径（textbook_v2/ppt_v2/domains/agent/reliability/...）禁止构造/写入；
- 运行：guard_textbook_page_read_only 拒绝 session 中待写入 TextbookPage；
  legacy_textbook_page_write_allowed() 仅测试边界逃生舱；
  install_textbook_page_guard 挂接后普通写入不受影响、page 写入被拒。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.textbook_v2.legacy_guards import (
    TextbookPageWriteError,
    guard_textbook_page_read_only,
    install_textbook_page_guard,
    legacy_textbook_page_write_allowed,
)
from app.textbook_v2.models import TextbookPage, TextbookSource

_PLATFORM = Path(__file__).resolve().parents[3]  # tests/unit/textbook_v2 -> teacher-platform 根

# 允许引用 TextbookPage 的生产文件（定义/再导出/守卫本身；其余一律禁止）
_PAGE_REFERENCE_ALLOWLIST = {
    "app/textbook_v2/models.py",
    "app/textbook_v2/__init__.py",
    "app/textbook_v2/legacy_guards.py",
}

# 禁止出现任何 TextbookPage 写入意图的生产命名空间（新 writer 域）
_WRITE_BANNED_DIRS = (
    "app/textbook_v2/importers",
    "app/textbook_v2/retrieval",
    "app/textbook_v2/backfill_metadata.py",
    "app/textbook_v2/context_writer.py",
    "app/textbook_v2/reconciliation.py",
    "app/ppt_v2",
)


def _production_files() -> list[Path]:
    return [p for p in (_PLATFORM / "app").rglob("*.py") if "__pycache__" not in str(p)]


def _strip_docstrings(text: str) -> str:
    """去掉模块/函数 docstring 块，只对代码级引用做守卫断言。"""
    import re as _re

    return _re.sub(r'""".*?"""', "", text, flags=_re.DOTALL)


def test_static_no_textbook_page_writes_in_production() -> None:
    """生产代码中 TextbookPage 代码级引用只允许 allowlist；新 writer 域零构造/零写入。"""
    banned_refs: list[str] = []
    for f in _production_files():
        code = _strip_docstrings(f.read_text(encoding="utf-8"))
        rel = f.relative_to(_PLATFORM).as_posix()
        if ("TextbookPage" in code or "textbook_pages" in code) and rel not in _PAGE_REFERENCE_ALLOWLIST:
            banned_refs.append(rel)
    assert banned_refs == [], f"生产代码出现 TextbookPage 代码级引用（allowlist 外）: {banned_refs}"


def test_static_no_page_write_keywords_in_writers() -> None:
    """新 writer 域禁止构造 TextbookPage / 禁止写入 PDF 页资产字段。"""
    import re as _re

    ctor = _re.compile(r"TextbookPage\s*\(")
    for f in _production_files():
        rel = f.relative_to(_PLATFORM).as_posix()
        if not any(rel.startswith(b) for b in _WRITE_BANNED_DIRS):
            continue
        code = _strip_docstrings(f.read_text(encoding="utf-8"))
        assert not ctor.search(code), f"{rel} 出现 TextbookPage 构造（禁止新写入）"
        for field in ("raw_pdf_asset_ref", "page_image_asset_ref", "text_layer", "ocr_text", "checksum"):
            assert _re.search(rf"\.{field}\s*=", code) is None, f"{rel} 出现 {field} 写入"


async def test_runtime_guard_rejects_page_write():
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        src = TextbookSource(
            publisher="合成社", edition="全一册", grade="高一", volume="上册", subject="数学",
            source_repo="https://example.invalid", source_commit="0" * 40,
            source_path="synth/pages.jsonl", file_sha256="0" * 64,
            rights_status="STAGED", rights_evidence_ref="队伍确认授权",
            parser_version="synth-v1", source_kind="direct-chunk",
        )
        session.add(src)
        await session.flush()

        # 普通写入不受守卫影响
        guard_textbook_page_read_only(session)
        # 待写入 TextbookPage → 拒绝
        session.add(TextbookPage(
            textbook_id=src.id, page_no=1, printed_page_no="1",
            raw_pdf_asset_ref="textbook/synthetic/p1.pdf",
            page_image_asset_ref="textbook/synthetic/p1.png",
            checksum="0" * 64,
        ))
        with pytest.raises(TextbookPageWriteError):
            guard_textbook_page_read_only(session)

        # 逃生舱（仅测试边界）允许
        with legacy_textbook_page_write_allowed():
            guard_textbook_page_read_only(session)  # 不抛
    await engine.dispose()


async def test_runtime_guard_installed_blocks_flush():
    """install_textbook_page_guard 挂接后，page 写入在 flush 即被拒绝（fail closed）。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    uninstall = install_textbook_page_guard(maker)
    try:
        async with maker() as session:
            src = TextbookSource(
                publisher="合成社", edition="全一册", grade="高一", volume="上册", subject="数学",
                source_repo="https://example.invalid", source_commit="0" * 40,
                source_path="synth/pages.jsonl", file_sha256="1" * 64,
                rights_status="STAGED", rights_evidence_ref="队伍确认授权",
                parser_version="synth-v1", source_kind="direct-chunk",
            )
            session.add(src)
            session.add(TextbookPage(
                textbook_id=src.id, page_no=1, printed_page_no="1",
                raw_pdf_asset_ref="textbook/synthetic/p1.pdf",
                page_image_asset_ref="textbook/synthetic/p1.png",
                checksum="0" * 64,
            ))
            with pytest.raises(TextbookPageWriteError):
                await session.commit()
    finally:
        uninstall()
    await engine.dispose()
