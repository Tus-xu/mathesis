"""`.env` 装载器（8300 生产就绪所需的唯一运行期便利设施）。

存在理由：`config.py` 只从**进程环境变量**读取配置，本仓库没有任何东西会去读
`services/research-vnext/.env`。没有这一步，`.env` 只是一份写给人看的文件，
服务根本起不来 —— 即"配置存在但未接线"。

铁律：
1. **已存在的进程环境变量永远优先**（`override=False` 语义）。这是隔离测试能继续
   工作的前提：`tests/conftest.py` 在导入 `research_vnext` **之前**就把
   `RESEARCH_DB_SCHEMA` 固定成独立 schema（`rv8300_it_*`），本模块绝不会覆盖它。
2. **只解析，不打印**。任何值都不写日志、不进异常消息（密钥脱敏）。
3. `.env` 缺失时静默跳过（CI / 生产用真实环境变量注入）。
"""

from __future__ import annotations

import os
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = SERVICE_ROOT / ".env"

_loaded = False


def _parse(text: str) -> dict[str, str]:
    """解析最小 dotenv 子集：KEY=VALUE、`#` 注释、可选的成对引号。

    故意不实现变量插值 / 多行值 —— 我们只需要"够用且可预测"。
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_dotenv(path: Path | None = None) -> list[str]:
    """把 `.env` 中**尚未存在**的键注入 `os.environ`，返回被注入的键名列表。

    返回键名（而非值）以便调用方安全地记录"装了什么"，不泄露内容。
    """
    global _loaded
    if _loaded:
        return []
    _loaded = True

    target = path or ENV_FILE
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return []

    applied: list[str] = []
    for key, value in _parse(text).items():
        if key in os.environ:
            continue  # 显式环境变量优先（测试隔离依赖此行为）
        os.environ[key] = value
        applied.append(key)
    return applied
