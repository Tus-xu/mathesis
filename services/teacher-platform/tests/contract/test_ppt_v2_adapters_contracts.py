"""PPT Studio V2 Adapter 契约守卫（R11）。

不触网、零密钥：把 R11 的关键事实（维度铁律、模型意图、配置模板占位、无真实 key 形态）
固化为可回归断言。真实供应商能力矩阵见 tests/integration/ppt_v2/test_capability_smoke.py
（R11_SMOKE=1 手工 opt-in）。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.ppt_v2.adapters.ai import MODEL_TEXT, MODEL_VISION
from app.ppt_v2.adapters.embedding import V2_INDEX_DIMS

ADAPTER_DIR = Path(__file__).resolve().parents[2] / "app" / "ppt_v2" / "adapters"

# 真实供应商 key 形态（sk- 后跟 ≥32 位 base64 字符）；占位符用 x 不命中
_REAL_KEY = re.compile(r"sk-[A-Za-z0-9]{32,}")

_ENV_NAMES = ("MODEL_API_KEY", "EMBEDDING_API_KEY", "VISION_API_KEY", "SEARCH_API_KEY")


def test_v2_index_dim_contract_is_768() -> None:
    """V2 embedding 铁律：真实维度必须 == 768（与 app/rag/embedder.EMBEDDING_DIMS 同源）。"""
    assert V2_INDEX_DIMS == 768


def test_model_intent_constants() -> None:
    """模型意图（用户/总手册 G4 给定）。"""
    assert MODEL_TEXT == "spark-x2.5-4b"
    assert MODEL_VISION == "xophunyuanocr"


def test_config_template_present_with_placeholders() -> None:
    """配置模板存在且覆盖四个 env 名；只含占位符，不含真实 key。"""
    cfg = ADAPTER_DIR / "config.example.env"
    assert cfg.is_file(), "缺失 config.example.env（R11 交付物）"
    text = cfg.read_text(encoding="utf-8")
    for name in _ENV_NAMES:
        assert name in text, f"config 模板缺少 {name}"
    assert not _REAL_KEY.search(text), "config 模板出现真实 key 形态"


def test_adapter_sources_have_no_real_key_shapes() -> None:
    """零秘密落盘守卫：app/ppt_v2/adapters/** 无真实 key 形态（占位符 x 不命中）。"""
    violations: list[str] = []
    for f in sorted(ADAPTER_DIR.rglob("*.py")):
        if _REAL_KEY.search(f.read_text(encoding="utf-8")):
            violations.append(str(f))
    assert not violations, "adapter 源码出现真实 key 形态:\n" + "\n".join(violations)
