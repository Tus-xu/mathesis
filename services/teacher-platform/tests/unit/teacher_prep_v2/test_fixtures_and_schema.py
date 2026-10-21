"""R13 契约测试 B：fixture ↔ JSON Schema ↔ Python 模型 一致性 + 生成无漂移。

验收门（对应 provider/consumer contract tests）：
- 每个 valid fixture 必须通过其契约自身的 JSON Schema（jsonschema，draft 2020-12）；
- 每个 invalid fixture 必须被自身契约拒绝（JSON Schema 或 Pydantic 至少一层失败）；
- 重新生成 JSON Schema/TS 与提交文件字节一致（禁止手改生成物）；
- golden（每契约 full 变体）规范哈希被钉死，前端 vitest 用同一常量互证
  （Python 与 TypeScript 对同一 fixture 语义解释必须同一）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError

from app.teacher_prep_v2.contracts.catalog import CONTRACT_MODELS
from app.teacher_prep_v2.contracts.gen.gen_schema import generate_schema
from app.teacher_prep_v2.contracts.gen.gen_ts import generate_ts

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "app" / "teacher_prep_v2" / "contracts"
FIXTURES = CONTRACTS_DIR / "fixtures"
SCHEMA_DIR = CONTRACTS_DIR / "schema"

# 与前端 test/teacher-prep-v2/prepContracts.test.ts 共享的规范哈希（机器互证锚点）
CANONICAL_SHA256 = {
    "PreClassDiagnosticTask": "b1b95506b341d47108961f01e1d3122ebed4a1240f21f67699a963651d8fb90e",
    "ClassroomTask": "7f29e5d2ce2b63b8a85bfc7854fd8fa798d10fb5bff6c235742956cbe1bb6f8f",
    "TaskAssignment": "17979de23ac7f67465f08e2a92031af460e2a5eba6816b07125e7cdf7b01f378",
    "StudentSubmission": "1ff891a57c7bb646e5d7a61f62c4f0d714bfd6fb47cf8f5319c22edb6fe63d89",
    "Evidence": "db8d0cd49ec75d7ad21ac926fb30b0d66c0c9844073cee897d2825d3f63f4eca",
    "ClassLearningProfile": "e1a1a291ac84ead3f30844ea615aa56d663b02527e1cd70cf8041e19d0dee4cc",
}

SCHEMA_FILE = SCHEMA_DIR / "prep-contracts.schema.json"
TS_FILE = SCHEMA_DIR / "prep-contracts.ts"


def _fixture_files(kind: str, mod: str) -> list[Path]:
    base = FIXTURES / kind / mod
    if not base.is_dir():
        return []
    return sorted(base.glob("*.json"))


def _canonical(obj) -> str:
    if isinstance(obj, dict):
        return "{" + ",".join(
            json.dumps(k, ensure_ascii=False) + ":" + _canonical(v)
            for k, v in sorted(obj.items(), key=lambda kv: json.dumps(kv[0], ensure_ascii=False))
        ) + "}"
    if isinstance(obj, list):
        return "[" + ",".join(_canonical(v) for v in obj) + "]"
    return json.dumps(obj, ensure_ascii=False)


# ── 生成无漂移 ────────────────────────────────────────────────────────────────


def test_json_schema_regeneration_matches_committed() -> None:
    committed = SCHEMA_FILE.read_text(encoding="utf-8")
    regenerated = json.dumps(generate_schema(), ensure_ascii=False, indent=2) + "\n"
    assert regenerated == committed, "JSON Schema 漂移：请运行 python -m app.teacher_prep_v2.contracts.gen.gen_schema"


def test_typescript_regeneration_matches_committed() -> None:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    committed_ts = TS_FILE.read_text(encoding="utf-8")
    regenerated_ts = generate_ts(schema)
    assert regenerated_ts == committed_ts, "TypeScript 漂移：请运行 python -m app.teacher_prep_v2.contracts.gen.gen_ts"


# ── fixture ↔ 契约 schema ↔ 模型 三向校验 ─────────────────────────────────────


@pytest.mark.parametrize(
    "kind,root_key",
    [
        ("PreClassDiagnosticTask", "pre_class_diagnostic_task"),
        ("ClassroomTask", "classroom_task"),
        ("TaskAssignment", "task_assignment"),
        ("StudentSubmission", "student_submission"),
        ("Evidence", "evidence"),
        ("ClassLearningProfile", "class_learning_profile"),
    ],
)
def test_bundle_schema_contains_all_roots(kind: str, root_key: str) -> None:
    schema = generate_schema()
    assert kind in schema["$defs"], f"打包 schema 缺少 {kind} 定义"
    assert root_key in schema["properties"], f"打包 schema 根缺少 {root_key}"


@pytest.mark.parametrize("kind,name", [(k, f.name) for k in CONTRACT_MODELS for f in _fixture_files(k, "valid")])
def test_valid_fixture_passes_own_json_schema(kind: str, name: str) -> None:
    data = json.loads((FIXTURES / kind / "valid" / name).read_text(encoding="utf-8"))
    schema = CONTRACT_MODELS[kind].model_json_schema()
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: e.path.__str__())
    assert not errors, [e.message for e in errors][:5]


@pytest.mark.parametrize("kind,name", [(k, f.name) for k in CONTRACT_MODELS for f in _fixture_files(k, "invalid")])
def test_invalid_fixture_rejected(kind: str, name: str) -> None:
    """invalid fixture 必须在 JSON Schema 或 Pydantic 至少一层失败（不得静默通过）。"""
    data = json.loads((FIXTURES / kind / "invalid" / name).read_text(encoding="utf-8"))
    schema = CONTRACT_MODELS[kind].model_json_schema()
    schema_failed = False
    try:
        Draft202012Validator(schema).validate(data)
    except SchemaValidationError:
        schema_failed = True
    model_failed = False
    try:
        CONTRACT_MODELS[kind].model_validate(data)
    except Exception:  # noqa: BLE001 —— ValidationError 为主，边界异常也算拒收
        model_failed = True
    assert schema_failed or model_failed, f"{kind}/{name} 应被拒绝但两层都通过"


def test_golden_fixture_canonical_hash_pinned() -> None:
    """golden 哈希被钉死；任一侧（Python/前端 TS）语义漂移即失败。"""
    for kind, expected in CANONICAL_SHA256.items():
        path = FIXTURES / kind / "valid" / {
            "PreClassDiagnosticTask": "diagnostic_full.json",
            "ClassroomTask": "classroom_full.json",
            "TaskAssignment": "assignment_provisioned.json",
            "StudentSubmission": "submission_graded.json",
            "Evidence": "evidence_photo.json",
            "ClassLearningProfile": "profile_full.json",
        }[kind]
        data = json.loads(path.read_text(encoding="utf-8"))
        actual = hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()
        assert actual == expected, (
            f"{kind} golden 规范哈希被改变（{actual} != {expected}）：若本轮有意修改 fixture，"
            "必须同步更新本文件与前端 test/teacher-prep-v2/prepContracts.test.ts 的常量"
        )


def test_contract_fixture_coverage() -> None:
    """每个契约至少各有 1 个 valid 与 1 个 invalid fixture（“有效/无效 fixture”验收）。"""
    for kind in CONTRACT_MODELS:
        assert _fixture_files(kind, "valid"), f"{kind} 缺少 valid fixture"
        assert _fixture_files(kind, "invalid"), f"{kind} 缺少 invalid fixture"


def test_schema_forbids_engine_geometry_and_page_refs() -> None:
    """契约字段不得出现引擎坐标（x/y/w/h）与假页码字段，防止 V3 遗毒泄漏进跨端契约。"""
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    for def_name, node in schema["$defs"].items():
        props = set(node.get("properties", {}))
        assert not (
            {"x", "y", "w", "h"} & props
        ), f"{def_name} 出现引擎坐标字段"
        assert not ({"page_refs", "page_ref", "page_no"} & props), f"{def_name} 出现假页码字段"
