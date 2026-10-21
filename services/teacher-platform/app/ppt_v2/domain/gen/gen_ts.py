"""从 JSON Schema 确定性生成 TypeScript 接口（禁止手写第三份类型漂移）。

覆盖的 schema 特性（均为 Pydantic v2 对 spec.py 的实际输出）：
    $ref / const / enum / anyOf（含 null 成员） / type 数组 /
    array+items / object（properties 或 additionalProperties） /
    string/integer/boolean。数值约束（minimum 等）不影响 TS 类型。

输出：app/ppt_v2/domain/schema/deck-spec.ts
前端复制到 src/features/ppt-studio-v2/types/deck-spec.ts。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SCHEMA_FILE = Path(__file__).resolve().parents[1] / "schema" / "deck-spec.schema.json"
_OUT = _SCHEMA_FILE.with_name("deck-spec.ts")

_PRIMITIVES = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
}


def _ts_literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value)


def _ts_type(node: dict, defs: dict[str, Any]) -> tuple[str, bool]:
    """返回 (TS 类型串, 是否含 null) 。"""
    if "$ref" in node:
        return node["$ref"].rsplit("/", 1)[-1], False
    if "const" in node:
        return _ts_literal(node["const"]), False
    if "enum" in node:
        return " | ".join(_ts_literal(v) for v in node["enum"]), False
    if "anyOf" in node:
        parts: list[str] = []
        nullable = False
        for sub in node["anyOf"]:
            t, has_null = _ts_type(sub, defs)
            if has_null:
                nullable = True
            else:
                parts.append(t)
        unique = list(dict.fromkeys(parts))
        return (" | ".join(unique) if unique else "null"), nullable
    jtype = node.get("type")
    if isinstance(jtype, list):
        types = [t for t in jtype if t != "null"]
        nullable = "null" in jtype
        if not types:
            return "unknown", nullable
        return _PRIMITIVES.get(types[0], "unknown"), nullable
    if jtype == "array":
        items = node.get("items", {})
        inner, _ = _ts_type(items, defs)
        return f"{inner}[]", False
    if jtype == "object":
        if "properties" in node:
            props = node["properties"]
            required = set(node.get("required", []))
            lines = [_emit_field(k, v, defs, k in required) for k, v in props.items()]
            return "{\n    " + "\n    ".join(lines) + "\n  }", False
        ap = node.get("additionalProperties")
        if isinstance(ap, dict):
            inner, _ = _ts_type(ap, defs)
            return f"Record<string, {inner}>", False
        return "Record<string, unknown>", False
    if jtype in _PRIMITIVES:
        return _PRIMITIVES[jtype], False
    if jtype == "null":
        return "null", True
    return "unknown", False


def _emit_field(name: str, node: dict, defs: dict[str, Any], required: bool) -> str:
    base, nullable = _ts_type(node, defs)
    if nullable and not required and base != "null":
        # null 成员剥掉，用 `?` 表达可选；若必填且可为 null 则显式并集保留
        pass
    if nullable and required and base != "null":
        base = f"{base} | null"
    opt = "" if required else "?"
    return f"{name}{opt}: {base}"


def generate_ts(schema: dict) -> str:
    defs = schema.get("$defs", {})
    root_title = schema.get("title", "TeachingDeckSpec")
    # schema_version 常量 = 根 properties.schema_version.const
    root_props = schema.get("properties", {})
    version_node = root_props.get("schema_version", {})
    version_const = version_node.get("const", "2.0.0")
    lines = [
        "// 本文件由 python -m app.ppt_v2.domain.gen.gen_ts 生成——禁止手改；",
        "// 权威源 = services/teacher-platform/app/ppt_v2/domain/spec.py（Pydantic）。",
        "// 生成产物，用于前端类型层与 fixture 契约测试；漂移由后端 contract test 拦截。",
        "",
        f"export const DECK_SCHEMA_VERSION = {_ts_literal(version_const)}",
        f"export type DeckRoot = {root_title}",
        "",
    ]
    for def_name, def_node in defs.items():
        if def_node.get("type") != "object" or "properties" not in def_node:
            # 引用/别名类型（本契约中没有顶层非对象 defs，防御性跳过）
            continue
        lines.append(f"export interface {def_name} {{")
        required = set(def_node.get("required", []))
        for field_name, field_node in def_node["properties"].items():
            lines.append("  " + _emit_field(field_name, field_node, defs, field_name in required))
        lines.append("}\n")

    # 根对象（Pydantic 根模型内联在 schema 顶层，不在 $defs）——必须同样生成接口
    if "properties" in schema:
        lines.append(f"export interface {root_title} {{")
        required = set(schema.get("required", []))
        for field_name, field_node in schema["properties"].items():
            lines.append("  " + _emit_field(field_name, field_node, defs, field_name in required))
        lines.append("}\n")
    return "\n".join(lines)


def main() -> None:
    schema = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))
    _OUT.write_text(generate_ts(schema), encoding="utf-8")
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
