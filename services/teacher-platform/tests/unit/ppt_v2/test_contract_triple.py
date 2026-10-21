"""三端一致性契约测试（R10）：Pydantic 权威源 → JSON Schema → TypeScript，禁止手写漂移。

验收门：
- 重新生成的 JSON Schema 与提交文件字节一致（任何模型变更必须同步提交 schema）；
- 重新生成的 TypeScript 类型与提交文件字节一致（前端类型层即生成产物）；
- golden fixtures 的规范序列化 SHA256 与前端 vitest 断言的常量一致
  （Python 与 TypeScript 对同一 fixture 的语义解释必须同一，否则两侧互证失效）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.ppt_v2.domain.gen.gen_schema import generate_schema
from app.ppt_v2.domain.gen.gen_ts import generate_ts

# 与前端 test/teacher/pptStudioV2DeckSpec.test.ts 共享的规范哈希（机器互证锚点）
# R20 契约再生成：TeachingDeckSpec 增补可选 projection 血缘块 → fixtures 加 "projection": null，哈希同步前移
CANONICAL_SHA256 = {
    "deck_golden_a.json": "4508dcc051e9f642ecc07aa543fe7b39e85044cb8672fc052db317af36a9d209",
    "deck_golden_b.json": "33175967ae1b2a39df5e9fbfec03986a8457431be951242419de9b61e4c97a17",
}

_DOMAIN_SCHEMA = Path(__file__).resolve().parents[3] / "app" / "ppt_v2" / "domain" / "schema"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_json_schema_regeneration_matches_committed() -> None:
    committed = (_DOMAIN_SCHEMA / "deck-spec.schema.json").read_text(encoding="utf-8")
    regenerated = json.dumps(generate_schema(), ensure_ascii=False, indent=2) + "\n"
    assert regenerated == committed, (
        "JSON Schema 漂移：请重新运行 python -m app.ppt_v2.domain.gen.gen_schema 并提交，"
        "禁止手工改 schema 文件"
    )


def test_typescript_regeneration_matches_committed() -> None:
    schema = json.loads((_DOMAIN_SCHEMA / "deck-spec.schema.json").read_text(encoding="utf-8"))
    committed_ts = (_DOMAIN_SCHEMA / "deck-spec.ts").read_text(encoding="utf-8")
    regenerated_ts = generate_ts(schema)
    assert regenerated_ts == committed_ts, (
        "TypeScript 类型漂移：请重新运行 python -m app.ppt_v2.domain.gen.gen_ts "
        "并同步前端 src/features/ppt-studio-v2/types/deck-spec.ts"
    )


def test_pydantic_dumps_conform_to_own_schema() -> None:
    """Pydantic 模型输出必须能被其自身生成的 JSON Schema 描述（结构性 sanity）。"""
    schema = generate_schema()
    data = json.loads((_FIXTURES / "deck_golden_a.json").read_text(encoding="utf-8"))
    assert schema["$defs"]["SlideSpec"]  # defs 存在
    assert set(data.keys()) == set(schema["properties"].keys())


def _canonical(obj) -> str:
    if isinstance(obj, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _canonical(v) for k, v in sorted(obj.items())) + "}"
    if isinstance(obj, list):
        return "[" + ",".join(_canonical(v) for v in obj) + "]"
    return json.dumps(obj, ensure_ascii=False)


def test_golden_fixture_canonical_hash_pinned() -> None:
    """黄金 fixture 的规范哈希被钉死；任一侧（Python/前端 TS）语义漂移即失败。"""
    for name, expected in CANONICAL_SHA256.items():
        data = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
        actual = hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()
        assert actual == expected, (
            f"{name} 规范哈希被改变（{actual} != {expected}）：若本轮有意修改 fixture，"
            "必须同步更新本文件与前端测试的常量"
        )
