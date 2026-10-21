# coding: utf-8
"""figure_presets 种子完整性 + 扫描增强镜像 + p2t 结果映射 + quiz 查询构建。"""
from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy.dialects import postgresql

from app.domains.catalog.service import QuizQueryFilters, build_quiz_query
from app.domains.figures.seeds import FIGURE_PRESETS_SEED, preset_rows
from app.inference.ocr.pix2text_client import _map_results
from app.inference.scan.scan_enhance import (
    SCAN_DEFAULTS,
    ScanParams,
    bgr_to_luma,
    enhance_luma,
    enhance_pixels,
    remove_shadow,
)

EXPECTED_IDS = [
    'solid/cube', 'solid/cuboid', 'solid/cube-section', 'solid/cube-net', 'solid/pyramid',
    'solid/prism', 'solid/tetra', 'solid/cylinder', 'solid/cone', 'solid/frustum', 'solid/sphere',
    'conic/ellipse', 'conic/ellipse-coordinate', 'conic/hyperbola', 'conic/parabola',
    'function/quadratic', 'function/sine', 'function/exp-log',
    'plane/triangle-heights', 'plane/circle-line',
    'stat/bar', 'stat/normal',
    'solid/pyramid-section', 'solid/sphere-section', 'plane/line-plane',
    'conic/ellipse-tangent', 'function/intersection',
]


def test_seeds_align_with_frontend_registry():
    assert [p['id'] for p in FIGURE_PRESETS_SEED] == EXPECTED_IDS
    assert len(FIGURE_PRESETS_SEED) == 27
    assert len({p['id'] for p in FIGURE_PRESETS_SEED}) == 27
    assert {p['kind'] for p in FIGURE_PRESETS_SEED} == {'solid', 'conic', 'function', 'plane', 'stat'}


def test_seed_rows_params_schema_roundtrip():
    rows = {r['id']: r for r in preset_rows()}
    cube = rows['solid/cube']
    assert cube['params_schema']['params'] == [
        {'key': 's', 'label': '边长', 'min': 1, 'max': 3, 'step': 0.1, 'def': 2},
    ]
    assert cube['params_schema']['toggles'] == [{'key': 'showLabels', 'label': '顶点标注', 'def': True}]
    for r in preset_rows():
        for q in r['params_schema']['params']:
            assert q['min'] <= q['def'] <= q['max']
        assert r['level'] in (1, 2) and r['kind'] and r['name']


# ---- 扫描增强：与前端 scanEnhance.ts 逐字镜像（golden 手算值）----

def test_enhance_luma_mirror_golden():
    p = SCAN_DEFAULTS  # brightness 6, contrast 30, whiten, strength 0.55
    assert p == ScanParams(6, 30, True, 0.55)
    f = 1.3
    # v=200：c=(200-128)*1.3+128+6=227.6 → ≥148 → 148+(227.6-148)*1.55=271.38 → clamp 255
    assert enhance_luma(200, p) == pytest.approx(255.0)
    # v=100：c=97.6 < 148 → 不提白
    assert enhance_luma(100, p) == pytest.approx((100 - 128) * f + 128 + 6)
    # v=148：对比+亮度后 c=160 ≥148 → 148+(160-148)*1.55=166.6（阈值作用于变换后亮度）
    assert enhance_luma(148, p) == pytest.approx(166.6)
    # v=150：c=162.6 → 148+(162.6-148)*1.55=170.63
    assert enhance_luma(150, p) == pytest.approx(148 + (((150 - 128) * f + 134) - 148) * 1.55)
    # whiten 关闭：纯对比+亮度
    p2 = ScanParams(brightness=0, contrast=0, whiten=False, strength=0)
    assert enhance_luma(77, p2) == 77


def test_enhance_pixels_gray_output():
    img = np.zeros((4, 3, 3), dtype=np.uint8)
    img[..., 2] = 200  # 红通道（BGR）
    out = enhance_pixels(img, SCAN_DEFAULTS)
    assert out.dtype == np.uint8 and out.shape == (4, 3)
    luma = float(bgr_to_luma(img[0, 0].astype(np.float64)))
    assert out[0, 0] == int(np.clip(enhance_luma(luma, SCAN_DEFAULTS), 0, 255))


def test_remove_shadow_runs():
    import cv2

    img = np.full((64, 64), 200, dtype=np.uint8)
    img[:32] = 160  # 模拟阴影
    out = remove_shadow(img)
    assert out.dtype == np.uint8 and out.shape == img.shape
    assert cv2.mean(out)[0] > cv2.mean(img)[0]  # 整体提白


# ---- p2t results → OcrBlock 映射（notes/B4-notes.md 主题一）----

def test_p2t_mapping_formula_and_text():
    results = [
        {'type': 'isolated', 'text': '$$\n\\frac{x^{2}}{a^{2}}=1\n$$', 'score': 0.91,
         'position': [100, 200, 300, 260]},
        {'type': 'text', 'text': '已知椭圆，求离心率。', 'score': 0.97, 'position': [10, 10, 90, 30]},
    ]
    blocks = _map_results(results, (1000, 500))
    assert [b.kind for b in blocks] == ['formula', 'text']
    assert blocks[0].latex == '\\frac{x^{2}}{a^{2}}=1' and blocks[0].text == ''
    assert blocks[0].confidence == pytest.approx(0.91)
    x, y, w, h = blocks[1].image_region
    assert (x, y, w, h) == pytest.approx((0.01, 0.02, 0.08, 0.04))


def test_p2t_mapping_empty_and_skip():
    assert _map_results([], (100, 100)) == []
    assert _map_results([{'type': 'text', 'text': ''}], (100, 100)) == []  # 空文本跳过
    assert _map_results(['garbage'], (100, 100)) == []


# ---- quiz 查询构建（SQL 断言；total 独立 COUNT）----

def test_quiz_query_filters_compile():
    stmt, count = build_quiz_query(QuizQueryFilters(kp=['G2-XX-01'], q='椭圆', difficulty='medium', q_type='solve'))
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert 'kp_code =' in sql and 'array_position' in sql
    assert 'ilike' in sql.lower() and 'stem_plain' in sql and 'stem_latex' in sql
    assert 'difficulty =' in sql and 'q_type =' in sql
    sql_literal = str(stmt.compile(
        dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True},
    ))
    assert 'LIMIT 20' in sql_literal and 'OFFSET 0' in sql_literal
    csql = str(count.compile(dialect=postgresql.dialect()))
    assert 'count' in csql.lower() and 'limit' not in csql.lower()


def test_quiz_query_folder_subquery_and_limit_guard():
    import uuid

    fid = uuid.uuid4()
    stmt, _ = build_quiz_query(QuizQueryFilters(folder=fid))
    assert 'folder_question_refs' in str(stmt.compile(dialect=postgresql.dialect()))
    with pytest.raises(ValueError):
        QuizQueryFilters(limit=0)
    with pytest.raises(ValueError):
        QuizQueryFilters(limit=101)
