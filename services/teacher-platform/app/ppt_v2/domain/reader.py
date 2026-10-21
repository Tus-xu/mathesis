"""向前兼容 Reader 与未知字段策略（R10）。

契约（机器可测试）：
- 同一 major 内：未知字段被容忍（extra=ignore），schema_version 更旧的 payload 可读；
- 未知/未来 major：fail closed（UnsupportedSchemaVersion），不猜测语义；
- round-trip 保证仅在 schema_version == SCHEMA_VERSION 时成立（旧版本按兼容语义读取，
  输出以当前域模型为准，可能丢字段——调用方应记录 warnings）。
"""

from __future__ import annotations

import json
from typing import Any

from app.ppt_v2.domain.errors import DeckSpecError, UnsupportedSchemaVersion
from app.ppt_v2.domain.spec import SCHEMA_VERSION, TeachingDeckSpec

# 当前 major：跨 major 语义断裂才允许 bump（破坏性变更必须 bump major，本常量作闸门）
_CURRENT_MAJOR = SCHEMA_VERSION.split(".", 1)[0]


class DeckSpecReader:
    """把任意 JSON 载入域对象；返回 (spec, warnings)。"""

    @staticmethod
    def read(raw: str | bytes | dict[str, Any]) -> tuple[TeachingDeckSpec, list[str]]:
        data: dict[str, Any]
        if isinstance(raw, (str, bytes)):
            data = json.loads(raw)
        else:
            data = raw
        return DeckSpecReader.read_dict(data)

    @staticmethod
    def read_dict(data: dict[str, Any]) -> tuple[TeachingDeckSpec, list[str]]:
        if not isinstance(data.get("schema_version"), str):
            raise DeckSpecError("payload 缺少 schema_version 字段（无法判定契约版本）")

        version = data["schema_version"]
        major = version.split(".", 1)[0]
        if major != _CURRENT_MAJOR:
            raise UnsupportedSchemaVersion(version=version, supported=SCHEMA_VERSION)

        warnings: list[str] = []
        if version != SCHEMA_VERSION:
            warnings.append(
                f"schema_version={version} 与当前 {SCHEMA_VERSION} 不同：按向前兼容读取，"
                "未知字段被忽略，round-trip 语义不保证"
            )
            # 同 major 的未来 minor：当前模型无法表达未知枚举值，先归一为当前版本做结构校验
            data = {**data, "schema_version": SCHEMA_VERSION}
        spec = TeachingDeckSpec.model_validate(data)
        return spec, warnings
