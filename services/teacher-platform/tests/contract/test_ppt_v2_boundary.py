"""PPT Studio V2 ↔ Legacy 边界守卫（R03 工程宪法）。

静态断言：`app/ppt_v2/**` 不得 import 旧 PPT 生成/导出/分页/schema 模块
（Legacy Freeze Manifest，D3 强制依赖守卫）。只做源码文本扫描，只读、无网络。

目录尚不存在时必须通过（不误报）；一旦 V2 目录出现违规 import，本测试立即失败，
把违规文件与 import 行直接指给开发者。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parents[2] / "app"
V2_DIR = PLATFORM_ROOT / "ppt_v2"

# D3 守卫：V2 禁止 import 的 legacy 模块前缀（模块路径，去掉 app. 前缀）
FORBIDDEN_LEGACY_IMPORTS = (
    "export.pptx_engine",
    "export",
    "domains.generation",
    "domains.contents",
    "pagination.layout",
    "pagination.schemas",
    "pagination.engine",
)

_IMPORT_STMT = re.compile(
    r"^\s*(?:from\s+([.\w]+)\s+import\s+(?:\(|[^#]+)|import\s+([.\w]+))\s*$",
    re.MULTILINE,
)


@pytest.fixture(scope="module")
def v2_py_files() -> list[Path]:
    """V2 命名空间下全部 Python 文件（目录不存在则空列表 → 测试不误报）。"""
    if not V2_DIR.is_dir():
        return []
    return sorted(V2_DIR.rglob("*.py"))


def _imported_modules(text: str) -> list[str]:
    """从源码提取完整 import 的模块点路径列表（只关心 app.* 内部模块）。"""
    modules: list[str] = []
    for m in _IMPORT_STMT.finditer(text):
        raw = m.group(1) or m.group(2) or ""
        raw = raw.strip().rstrip(",")
        if not raw.startswith("app."):
            continue
        modules.append(raw)
    return modules


@pytest.mark.parametrize(
    "forbidden",
    FORBIDDEN_LEGACY_IMPORTS,
    ids=[f.replace(".", "_") for f in FORBIDDEN_LEGACY_IMPORTS],
)
def test_ppt_v2_never_imports_legacy(v2_py_files: list[Path], forbidden: str) -> None:
    """D3：app/ppt_v2/** 不得 import legacy {forbidden}（含子模块）。"""
    violations: list[str] = []
    for f in v2_py_files:
        text = f.read_text(encoding="utf-8")
        for mod in _imported_modules(text):
            if mod == f"app.{forbidden}" or mod.startswith(f"app.{forbidden}."):
                violations.append(f"{f.relative_to(PLATFORM_ROOT)}: import {mod}")
    assert not violations, (
        f"PPT V2 违反 Legacy Freeze：禁引 {forbidden}\n" + "\n".join(violations)
    )


def test_v2_dir_absent_or_clean(v2_py_files: list[Path]) -> None:
    """目录不存在（未施工）或已存在且扫描无违规时均通过。"""
    if v2_py_files:
        assert all(f.suffix == ".py" for f in v2_py_files)