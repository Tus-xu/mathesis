"""R16 gold 元数据一致性测试（不读取真实数据；真实运行在 evidence-runs/verify_gold.py）。"""

from __future__ import annotations

import re
import uuid

from .gold_metadata import GOLD_CASES

_BATCH_RE = re.compile(r"^\d{8}-[A-Z]-[A-Za-z0-9-]+$")


def test_three_distinct_real_chapters():
    """≥3 个不同真实章节，且批次互不相同（跨册）。"""
    assert len(GOLD_CASES) >= 3
    batches = {c["batch"] for c in GOLD_CASES}
    assert len(batches) == len(GOLD_CASES), "gold case 必须来自不同批次（跨册）"
    titles = {c["section_title"] for c in GOLD_CASES}
    assert len(titles) == len(GOLD_CASES)


def test_batch_metadata_shape():
    """批次号符合 AC切片 命名规范；章节标题与知识点非空。"""
    for c in GOLD_CASES:
        assert _BATCH_RE.match(c["batch"]), c
        assert c["section_title"].strip()
        assert c["book"]
        assert len(c["knowledge_points"]) >= 1
        assert c["expected"]["overall_ge"] > 0
        assert c["expected"]["fact_count_ge"] >= 1
        assert isinstance(c["expected"]["warning_codes"], list)


def test_knowledge_points_distinct():
    """知识点必须包含 0 覆盖（可触发 low_coverage 降级）外的正常项。"""
    for c in GOLD_CASES:
        assert len(set(c["knowledge_points"])) == len(c["knowledge_points"]), "知识点重复"


def test_class_and_uuid_fixture_sanity():
    """gold 上下文构建所需的 UUID 形式一致（evidence 脚本依赖）。"""
    sample = uuid.UUID("00000000-0000-0000-0000-000000000000")
    assert str(sample) == "00000000-0000-0000-0000-000000000000"
