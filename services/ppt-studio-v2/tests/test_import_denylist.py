"""边界守卫（R15）：8200 不得 import 8100 teacher-prep ORM/router/service 与 legacy V3。

- test_ppt_studio_never_imports_8100：静态扫描 app/** 源码 import 语句；
- test_isolated_import_subprocess：子进程在「仅 8200 目录」的 PYTHONPATH 下整包导入，
  任何隐藏的 8100 依赖会直接 import 失败（8100 不在 path）→ 证明 PYTHONPATH 隔离。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = SERVICE_ROOT / "app"

# 与 8100 teacher-platform 冲突的模块前缀（8200 一律禁止 import；legacy V3 deck JSON 同列）。
# 注：app.reliability 是 8200 自持命名空间（同名模块），对 8100 reliability 的排除
#     由 test_isolated_import_subprocess（PYTHONPATH 隔离）强制，不列入静态名单。
FORBIDDEN_8100_IMPORTS = (
    "app.ppt_v2",          # 8100 的 ppt_v2 命名空间
    "app.teacher_prep",    # 8100 Teacher Prep 事实权威
    "app.teacher_prep_v2",
    "app.textbook_v2",
    "app.export",          # legacy PPTX engine
    "app.domains",         # 8100 业务域（含 legacy deck/contents/generation）
    "app.pagination",      # 旧 V3 分页/layout
    "app.rag",
    "app.llm",
    "app.agent",
    "app.tasks",
    "app.storage",
    "app.inference",
    "app.core.security",   # 8100 身份模块（8200 自持 service_auth）
    "app.core.response",
    "app.core.idempotency",
    "app.core.sse",
)

_IMPORT_STMT = re.compile(
    r"^\s*(?:from\s+([.\w]+)\s+import\s+(?:\(|[^#]+)|import\s+([.\w]+))\s*$",
    re.MULTILINE,
)


@pytest.fixture(scope="module")
def ps2_py_files() -> list[Path]:
    if not APP_DIR.is_dir():
        return []
    return sorted(APP_DIR.rglob("*.py"))


def _imported_modules(text: str) -> list[str]:
    modules: list[str] = []
    for m in _IMPORT_STMT.finditer(text):
        raw = m.group(1) or m.group(2) or ""
        modules.append(raw.strip().rstrip(","))
    return modules


@pytest.mark.parametrize("forbidden", FORBIDDEN_8100_IMPORTS, ids=[f.replace(".", "_") for f in FORBIDDEN_8100_IMPORTS])
def test_ppt_studio_never_imports_8100(ps2_py_files: list[Path], forbidden: str) -> None:
    violations: list[str] = []
    for f in ps2_py_files:
        text = f.read_text(encoding="utf-8")
        for mod in _imported_modules(text):
            if mod == forbidden or mod.startswith(f"{forbidden}."):
                violations.append(f"{f.relative_to(APP_DIR)}: import {mod}")
    assert not violations, f"8200 违反 8100 隔离守卫：禁引 {forbidden}\n" + "\n".join(violations)


def test_isolated_import_subprocess() -> None:
    """子进程 PYTHONPATH 仅含 8200 根目录（不含 teacher-platform）→ 全量导入成功。"""
    script = (
        "import os, sys;"
        "assert not any('teacher-platform' in p for p in sys.path), sys.path;"
        "import app.main, app.worker, app.api.routes, app.config, app.service_auth, app.trace;"
        "import app.reliability.jobs, app.reliability.worker, app.reliability.dispatcher,"
        " app.reliability.reconciler, app.reliability.broker, app.reliability.queue_policy,"
        " app.reliability.inbox, app.reliability.steps, app.reliability.presenton_client;"
        "import app.persistence.models, app.contracts.deck, app.contracts.job_protocols;"
        "print('ISOLATED_IMPORT_OK')"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SERVICE_ROOT)
    env["DATABASE_URL"] = "sqlite+aiosqlite://"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=SERVICE_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, f"被隐藏的 8100 依赖阻断：\n{result.stdout}\n{result.stderr}"
    assert "ISOLATED_IMPORT_OK" in result.stdout
