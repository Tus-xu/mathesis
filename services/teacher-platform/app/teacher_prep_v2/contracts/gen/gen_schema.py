"""生成六契约打包 JSON Schema（单一权威源 = Pydantic）。

输出：app/teacher_prep_v2/contracts/schema/prep-contracts.schema.json
前端复制到 src/features/teacher-prep-v2/fixtures/prep-contracts.schema.json 供运行时校验。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.teacher_prep_v2.contracts.catalog import bundle_schema

_OUT = Path(__file__).resolve().parents[1] / "schema" / "prep-contracts.schema.json"


def generate_schema() -> dict:
    """六契约打包 JSON Schema（$defs 含全部子模型，根为 PreClassContractsBundle）。"""
    return bundle_schema()


def main() -> None:
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(
        json.dumps(generate_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
