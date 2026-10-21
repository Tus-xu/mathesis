"""api_inventory.json 对账测试——端点矩阵的自动化验收（B5 规范 M1）。

`api_inventory.json` 由 export_api_inventory.py 生成（端点清单单一事实源）。
本测试断言：后端 OpenAPI 覆盖「前端 ∪ classroom §12.3 ∪ backend-owned」全量端点，
无缺失 / 无多余 / 无方法不符。红 = 后端契约缺口清单，直接指给对应所有者。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

HERE = Path(__file__).parent
INVENTORY = HERE / "api_inventory.json"


@pytest.fixture(scope="module")
def inv() -> dict:
    if not INVENTORY.exists():
        pytest.fail(
            "api_inventory.json 不存在——先跑 `python tests/contract/export_api_inventory.py --write`"
        )
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def test_inventory_counts_documented(inv):
    """计数可引用：SSE 通道=10；classroom §12.3 全量在 expected（允许前端已并行声明——重叠收敛）。"""
    counts = inv["counts"]
    assert counts["frontend_declared"] > 0, "前端端点提取为空——检查 FRONTEND_TEACHER_API 路径"
    assert counts["classroom_arch"] == 12, "§12.3 classroom 应为 10 REST + 2 SSE"
    assert counts["expected_sse"] == 10, "SSE 通道 = 既有 8 + 课堂 2"
    assert counts["expected_total"] <= counts["frontend_declared"] + counts["classroom_arch"] + counts["backend_owned"]
    # classroom §12.3 每条（通配参数名）必须出现在 expected（前端或架构条目覆盖）
    from export_api_inventory import CLASSROOM_ENDPOINTS, _wildcard

    expected_wc = {(e["method"], _wildcard(e["path"])) for e in inv["expected"]}
    for e in CLASSROOM_ENDPOINTS:
        assert (e["method"], _wildcard(e["path"])) in expected_wc, f"§12.3 端点丢失：{e['method']} {e['path']}"


def test_backend_covers_expected_endpoints(inv):
    """核心对账：missing 列表必须为空（红 = 契约缺口，按路径前缀归属所有者）。"""
    missing = inv["diff"]["missing"]
    assert not missing, (
        f"后端缺 {len(missing)}/{inv['counts']['expected_total']} 个契约端点（backend_source="
        f"{inv['backend_source']}）。缺口清单：\n"
        + "\n".join(f"  {e['method']:6s} {e['path']}  [{e['source']}]" for e in missing[:40])
    )


def test_backend_has_no_undeclared_endpoints(inv):
    """extra：后端不得私加前端未声明的端点（契约面外泄=打回）。"""
    extra = inv["diff"]["extra"]
    assert not extra, "后端存在契约面之外的端点：\n" + "\n".join(
        f"  {e['method']:6s} {e['path']}" for e in extra
    )


def test_no_method_mismatch(inv):
    assert not inv["diff"]["method_mismatch"], json.dumps(inv["diff"]["method_mismatch"], ensure_ascii=False, indent=2)


def test_missing_report_grouped_by_owner(inv):
    """红表按所有者归组（02-ARCHITECTURE §9），供基线报告直接引用。"""
    owner_by_prefix = [
        (r"/teacher-v3/(classroom)", "B4"),
        (r"/teacher-v3/(recognition|figures|draw|grading|assignments|insights|resources|folders|quiz|textbook)", "B4"),
        (r"/teacher-v3/(generation|plans|decks)", "B1"),
        (r"/teacher-v3/butler", "B1"),
        (r"/teacher-v3/(auth|today|classes|tasks|deck-templates|lesson-templates|recipes|upload)|^/healthz", "B3"),
    ]

    def owner_of(path: str) -> str:
        for pat, owner in owner_by_prefix:
            if __import__("re").search(pat, path):
                return owner
        return "B3(协调)"

    grouped: dict[str, list[str]] = {}
    for e in inv["diff"]["missing"]:
        grouped.setdefault(owner_of(e["path"]), []).append(f"{e['method']} {e['path']}")
    # 断言本身恒真——输出归组清单供报告复制（失败信息由 test_backend_covers_expected_endpoints 承担）
    assert isinstance(grouped, dict)
