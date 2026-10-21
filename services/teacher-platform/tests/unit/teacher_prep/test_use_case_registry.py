"""use_case 注册红线锁定：所有 app/ 调用点引用的 LLM use_case 必须已注册（W1-BE）。

背景（2026-09-19 实测）：`app/teacher_prep/specs/router.py` 调用
`use_case="lesson_plan_regen"`，但 USE_CASE_ROUTES 未注册 → LlmGateway
resolve_route 抛 ValueError → specs/regen 链路 500。本测试静态扫描全部
调用点字面量，防止再次漏注册。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.llm.config import USE_CASE_ROUTES, get_llm_settings

PLATFORM_ROOT = Path(__file__).resolve().parents[3] / "app"

# use_case="xxx" 字面量（动态传参的透传点由调用方字面量覆盖）
_USE_CASE_LITERAL = re.compile(r'use_case\s*=\s*"([a-z0-9_]+)"')


def _scan_use_case_literals() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(PLATFORM_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _USE_CASE_LITERAL.finditer(text):
            found.setdefault(match.group(1), []).append(
                str(path.relative_to(PLATFORM_ROOT.parent))
            )
    return found


def test_all_use_case_call_sites_registered():
    found = _scan_use_case_literals()
    assert found, "扫描应至少发现一个 use_case 调用点（防扫描失效）"
    missing = {name: files for name, files in found.items() if name not in USE_CASE_ROUTES}
    assert not missing, f"未注册的 use_case 调用点：{missing}"


def test_regen_use_cases_resolvable():
    """specs/regen 链路（lesson_plan_regen / ppt_regen）必须可解析出路由。"""
    settings = get_llm_settings()
    for use_case in ("lesson_plan_regen", "ppt_regen"):
        route = settings.resolve_route(use_case)
        assert route.primary, f"{use_case} 必须解析出主模型"
        assert route.timeout_s >= 60, f"{use_case} 结构化重生成超时不应过短"


def test_teacher_prep_core_use_cases_registered():
    for use_case in (
        "teacher_prep_intent",
        "teacher_prep_blueprint",
        "teacher_prep_diagnostic",
    ):
        assert use_case in USE_CASE_ROUTES
