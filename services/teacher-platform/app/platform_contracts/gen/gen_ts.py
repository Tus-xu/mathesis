"""生成前端 TypeScript 类型（单一权威源 = Pydantic 模型）。

输出：
- app/platform_contracts/schema/platform-contracts.ts（后端侧留档）
- D:\\frontend\\src\\features\\platform-contracts\\types\\platform-contracts.ts（消费方）

风格与 `app/teacher_prep_v2/contracts/gen/gen_ts.py` 一致（interface + union），
但独立实现，避免跨域耦合。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.platform_contracts import PLATFORM_CONTRACTS_SCHEMA_VERSION
from app.platform_contracts.bundle import PlatformContractsBundle

_HERE = Path(__file__).resolve()
SCHEMA_DIR = _HERE.parents[1] / "schema"
TS_PATH = SCHEMA_DIR / "platform-contracts.ts"

FRONTEND_TS_PATH = Path("D:/frontend/src/features/platform-contracts/types/platform-contracts.ts")

_HEADER = """// 本文件由 python -m app.platform_contracts.gen.gen_ts 生成——禁止手改；
// 权威源 = services/teacher-platform/app/platform_contracts/*.py（Pydantic）。
// 生成产物，用于前端类型层与 fixture 契约测试；漂移由后端 contract test 拦截。
"""


def _ts_type(node: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in node:
        return node["$ref"].split("/")[-1]
    if "anyOf" in node:
        parts = [_ts_type(n, defs) for n in node["anyOf"]]
        return " | ".join(dict.fromkeys(parts))
    if "const" in node:
        return json.dumps(node["const"])
    if "enum" in node:
        return " | ".join(json.dumps(v) for v in node["enum"])
    t = node.get("type")
    types = t if isinstance(t, list) else ([t] if t else [])
    if "array" in types:
        items = node.get("items", {})
        return f"{_ts_type(items, defs)}[]"
    if "integer" in types or "number" in types:
        return "number"
    if "boolean" in types:
        return "boolean"
    if "null" in types:
        return "null"
    if "string" in types:
        return "string"
    if "object" in types:
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            return f"Record<string, {_ts_type(extra, defs)}>"
        return "Record<string, unknown>"
    return "unknown"


def _render_interface(name: str, node: dict[str, Any], defs: dict[str, Any]) -> str:
    props = node.get("properties", {})
    required = set(node.get("required", []))
    lines = [f"export interface {name} {{"]
    for prop, sub in props.items():
        optional = "" if prop in required else "?"
        ts = _ts_type(sub, defs)
        alias = f'"{prop}"' if not prop.isidentifier() else prop
        lines.append(f"  {alias}{optional}: {ts}")
    if node.get("additionalProperties") is True:
        lines.append("  [key: string]: unknown")
    lines.append("}")
    return "\n".join(lines)


def generate_ts() -> str:
    schema = PlatformContractsBundle.model_json_schema()
    defs: dict[str, Any] = schema.get("$defs", {})
    chunks: list[str] = [_HEADER]
    chunks.append(f'export const PLATFORM_CONTRACTS_SCHEMA_VERSION = "{PLATFORM_CONTRACTS_SCHEMA_VERSION}"')
    chunks.append("")
    for name in sorted(defs):
        node = defs[name]
        if node.get("enum"):
            union = " | ".join(json.dumps(v) for v in node["enum"])
            chunks.append(f"export type {name} = {union}")
        elif node.get("type") == "object" or "properties" in node:
            chunks.append(_render_interface(name, node, defs))
        else:
            chunks.append(f"export type {name} = {_ts_type(node, defs)}")
        chunks.append("")
    chunks.append("export interface PlatformContractsBundle {")
    for prop, sub in schema.get("properties", {}).items():
        chunks.append(f"  {prop}?: {_ts_type(sub, defs)} | null")
    chunks.append("}")
    chunks.append("")
    return "\n".join(chunks)


def main() -> None:
    text = generate_ts()
    TS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TS_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {TS_PATH}")
    if FRONTEND_TS_PATH.parent.exists():
        FRONTEND_TS_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {FRONTEND_TS_PATH}")


if __name__ == "__main__":
    main()
