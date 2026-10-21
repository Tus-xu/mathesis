"""生成 JSON Schema（单一权威源 = Pydantic）。

输出：app/ppt_v2/domain/schema/deck-spec.schema.json
前端复制到 src/features/ppt-studio-v2/fixtures/deck-spec.schema.json 供运行时校验。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.ppt_v2.domain.spec import TeachingDeckSpec

_OUT = Path(__file__).resolve().parents[1] / "schema" / "deck-spec.schema.json"


def generate_schema() -> dict:
    """TeachingDeckSpec 的 JSON Schema（含 $defs：SlideSpec/AssetRef/…）。"""
    return TeachingDeckSpec.model_json_schema()


def main() -> None:
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(
        json.dumps(generate_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
