"""R05 合成 fixture：一份 3 页高中数学教材（合成数据，非真实未授权教材内容）。

供 R05 验收「完整入库并回溯每个 unit/problem 到原页」，并作为 R06+ 的最小样本基线。
刻意使用虚构出版社/版本/标题，禁止填写任何真实教材摘录——未授权内容零进仓库。
"""

from __future__ import annotations

import uuid

from app.textbook_v2.models import (
    TextbookChunk,
    TextbookContentUnit,
    TextbookPage,
    TextbookProblem,
    TextbookSection,
    TextbookSource,
)

SYNTH_SOURCE_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")


def build_synthetic_textbook() -> tuple[
    TextbookSource, list[TextbookPage], list[TextbookSection], list[TextbookContentUnit], TextbookProblem, TextbookChunk
]:
    """构造一份合成 3 页教材（抽象代数复习样例，非真实教材内容）。

    ERD：source ── pages(3) / sections(2，章→节父子) ── units / problem / chunk。
    返回 (source, pages, sections, units, problem, chunk)，均为未持久化 ORM 对象。
    """
    source = TextbookSource(
        id=SYNTH_SOURCE_ID,
        school_id=None,  # 平台级
        publisher="合成出版社",
        edition="全一册",
        grade="高一",
        volume="上册",
        subject="数学",
        source_repo="https://example.invalid/synthetic-textbook.git",
        source_commit="0000000000000000000000000000000000000000",
        source_path="synthetic/high-math-vol1.pdf",
        file_sha256="0" * 64,
        rights_status="QUARANTINED",
        rights_evidence_ref=None,
        parser_version="synth-v1",
    )

    pages = [
        TextbookPage(
            textbook_id=source.id,
            page_no=1,
            printed_page_no="1",
            raw_pdf_asset_ref=f"textbook/synthetic/{source.id}/p1.pdf",
            page_image_asset_ref=f"textbook/synthetic/{source.id}/p1.png",
            text_layer="首页占位文本层",
            checksum=f"p1-{'a' * 64}",
        ),
        TextbookPage(
            textbook_id=source.id,
            page_no=2,
            printed_page_no="2",
            raw_pdf_asset_ref=f"textbook/synthetic/{source.id}/p2.pdf",
            page_image_asset_ref=f"textbook/synthetic/{source.id}/p2.png",
            text_layer="第二页占位文本层",
            checksum=f"p2-{'b' * 64}",
        ),
        TextbookPage(
            textbook_id=source.id,
            page_no=3,
            printed_page_no="3",
            raw_pdf_asset_ref=f"textbook/synthetic/{source.id}/p3.pdf",
            page_image_asset_ref=f"textbook/synthetic/{source.id}/p3.png",
            text_layer="第三页练习题页",
            checksum=f"p3-{'c' * 64}",
        ),
    ]

    chapter = TextbookSection(
        textbook_id=source.id,
        parent_id=None,
        level=1,
        order=1,
        title="第1章 合成代数",
        normalized_title="合成代数",
        page_start=1,
        page_end=3,
        section_path="1",
    )
    lesson = TextbookSection(
        textbook_id=source.id,
        parent_id=None,  # 父子关系由测试在 flush 后回填
        level=2,
        order=2,
        title="1.1 合成代数初步",
        normalized_title="合成代数初步",
        page_start=1,
        page_end=2,
        section_path="1.1",
    )
    sections = [chapter, lesson]

    units = [
        TextbookContentUnit(
            section_id=None,  # 同理由测试回填
            page_from=1,
            page_to=1,
            type="introduction",
            text="本章引入",
            extraction_method="synthetic",
            review_status="pending",
            confidence=1.0,
        ),
        TextbookContentUnit(
            section_id=None,
            page_from=1,
            page_to=2,
            type="definition",
            text="合成量定义",
            latex="a+b",
            source_bbox={"page": 1, "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.1},
            extraction_method="synthetic",
            review_status="pending",
            confidence=1.0,
        ),
        TextbookContentUnit(
            section_id=None,
            page_from=2,
            page_to=2,
            type="figure",
            asset_refs=[{"kind": "TEXTBOOK", "object_key": f"textbook/synthetic/{source.id}/fig1.png"}],
            extraction_method="synthetic",
            review_status="pending",
            confidence=1.0,
        ),
    ]

    problem = TextbookProblem(
        section_id=None,
        kind="exercise",
        order=1,
        stem_text="合成练习题题干",
        solution_text="合成解答",
        page_ref=3,
        stem_bbox={"page": 3, "x": 0.05, "y": 0.1},
        original_asset_refs=[{"kind": "TEXTBOOK", "object_key": f"textbook/synthetic/{source.id}/p3-fig.png"}],
        knowledge_tags=["合成代数"],
        difficulty="中",
        provenance=f"textbook_sources/{source.id}/page_3",
    )

    chunk = TextbookChunk(
        section_id=None,
        page_range=[1, 2],
        text="合成章节分块文本",
        embedding_model=None,
        embedding_dim=None,
        chunker_version="synth-v1",
        content_hash="chunk-" + "d" * 64,
        index_version=0,
    )

    return source, pages, sections, units, problem, chunk


def build_synthetic_textbook_staged() -> TextbookSource:
    """R05 验收用的 STAGED 样本（权利证据已填）——未授权真实教材不适用。

    每次调用生成新的 id，避免多 source 测试中的主键冲突。
    """
    source = build_synthetic_textbook()[0]
    source.id = uuid.uuid4()
    source.rights_status = "STAGED"
    source.rights_evidence_ref = "rights/evidence/synthetic-license.pdf"
    return source
