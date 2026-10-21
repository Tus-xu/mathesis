"""Teacher Prep V2 边界守卫（R13）。

静态断言：
1. `app/teacher_prep_v2/**` 不得 import 冻结域（app.export / app.domains.* /
   app.pagination.* / app.ppt_v2 / app.textbook_v2）——跨命名空间只经 adapter/DTO；
2. `app/teacher_prep_v2/**` 零 ORM/sqlalchemy 与零 HTTP 直连 8000 库之外的事实建模：
   不得出现 __tablename__（不复制学生任务/提交事实表——8000 assignments/
   submissions 是唯一事实行，8100 只读投影 + adapter 映射）；
3. 六个公开契约名在 8100 无同名 ORM 表类（Evidence/StudentSubmission 等仅契约 DTO）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parents[2] / "app"
PREP_DIR = PLATFORM_ROOT / "teacher_prep_v2"

FORBIDDEN_LEGACY_IMPORTS = (
    "export",
    "domains.generation",
    "domains.contents",
    "pagination",
    "ppt_v2",
    "textbook_v2",
    "rag",  # 检索/embedding 能力在业务轮经 adapter 接入，契约/映射层不直连
)

_IMPORT_STMT = re.compile(
    r"^\s*(?:from\s+([.\w]+)\s+import\s+(?:\(|[^#]+)|import\s+([.\w]+))\s*$",
    re.MULTILINE,
)


@pytest.fixture(scope="module")
def prep_py_files() -> list[Path]:
    if not PREP_DIR.is_dir():
        return []
    return sorted(PREP_DIR.rglob("*.py"))


def _imported_modules(text: str) -> list[str]:
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
def test_teacher_prep_v2_never_imports_frozen_domain(prep_py_files: list[Path], forbidden: str) -> None:
    violations: list[str] = []
    for f in prep_py_files:
        text = f.read_text(encoding="utf-8")
        for mod in _imported_modules(text):
            if mod == f"app.{forbidden}" or mod.startswith(f"app.{forbidden}."):
                violations.append(f"{f.relative_to(PLATFORM_ROOT)}: import {mod}")
    assert not violations, (
        f"Teacher Prep V2 违反跨命名空间隔离：禁引 {forbidden}\n" + "\n".join(violations)
    )


def test_teacher_prep_v2_no_orm_and_no_fact_tables(prep_py_files: list[Path]) -> None:
    """零 __tablename__ / 零 sqlalchemy：本命名空间不复制任何事实表。

    8000 的 assignments / submissions / class_members 是唯一事实行；
    8100 侧仅 adapter HTTP 映射 +（业务轮）projection 映射表。
    """
    violations: list[str] = []
    for f in prep_py_files:
        text = f.read_text(encoding="utf-8")
        if re.search(r"__tablename__\s*=", text):
            violations.append(f"{f.relative_to(PLATFORM_ROOT)}: 出现 __tablename__（禁建事实表）")
        if re.search(r"import\s+sqlalchemy|\bfrom\s+sqlalchemy\b", text):
            violations.append(f"{f.relative_to(PLATFORM_ROOT)}: import sqlalchemy（禁 ORM）")
        if re.search(r"\bBase\.Session|\bSession\(\)", text):
            violations.append(f"{f.relative_to(PLATFORM_ROOT)}: 直连 DB Session（禁触 8000 库）")
    assert not violations, "\n".join(violations)


PUBLIC_CONTRACT_NAMES = (
    "PreClassDiagnosticTask",
    "ClassroomTask",
    "TaskAssignment",
    "StudentSubmission",
    "Evidence",
    "ClassLearningProfile",
)


def test_only_canonical_contract_names_exported(prep_py_files: list[Path]) -> None:
    """contracts 包对外只暴露六个规范契约名；旧别名（TaskContract/Submission）只留 reader 内部。"""
    contracts_init = PLATFORM_ROOT / "teacher_prep_v2" / "contracts" / "__init__.py"
    text = contracts_init.read_text(encoding="utf-8")
    for name in PUBLIC_CONTRACT_NAMES:
        assert name in text, f"contracts/__init__ 缺少公开契约名 {name}"
    # 旧别名只能在 reader 模块内（__init__ 禁止显式 re-export 旧名成第二套事实）
    assert "__all__" in text and "TaskContract" not in text.split("__all__")[-1].split("[")[-1].split("]")[0]


def test_prep_v2_dir_present_and_has_contracts(prep_py_files: list[Path]) -> None:
    contracts = [f for f in prep_py_files if f.parts[-2] in ("contracts",) or "contracts" in f.parts]
    assert contracts, "R13 契约包缺失（app/teacher_prep_v2/contracts/ 应为空实现外的目录不存在时才不误报）"
