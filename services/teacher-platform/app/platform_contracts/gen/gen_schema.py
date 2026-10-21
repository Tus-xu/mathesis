"""生成 16 契约打包 JSON Schema（单一权威源 = Pydantic）。

输出：app/platform_contracts/schema/platform-contracts.schema.json
前端复制到 src/features/platform-contracts/fixtures/platform-contracts.schema.json 供运行时校验。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.platform_contracts.bundle import PlatformContractsBundle

_HERE = Path(__file__).resolve()
REPO_SCHEMA_PATH = _HERE.parents[1] / "schema" / "platform-contracts.schema.json"


def generate_schema() -> dict:
    """16 契约打包 JSON Schema（$defs 含全部子模型，根为 PlatformContractsBundle）。"""
    return PlatformContractsBundle.model_json_schema()


def render_schema_text() -> str:
    return json.dumps(generate_schema(), ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    REPO_SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPO_SCHEMA_PATH.write_text(render_schema_text(), encoding="utf-8")
    print(f"wrote {REPO_SCHEMA_PATH}")


if __name__ == "__main__":
    main()
