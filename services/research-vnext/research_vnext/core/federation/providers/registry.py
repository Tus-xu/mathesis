"""RV06 · provider 装配：把 catalog 里的五类规范接到具体适配器。

`foundational_math` 的 engine 惰性获取：装配期**不**连接 Teacher 库，
因此只要该 provider 不在 allowlist 里，未配置只读 DSN 也不会影响其他 provider。
"""

from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .base import FederatedProvider
from .dataset import DatasetAdapter
from .foundational_math import FoundationalMathAdapter
from .literature import LiteratureAdapter
from .project_asset import ProjectAssetAdapter
from .web_external import WebExternalAdapter


def build_providers(
    db: Session,
    *,
    foundational_engine: Engine | None = None,
    foundational_database_url: str | None = None,
    include_disabled: bool = True,
) -> dict[str, FederatedProvider]:
    """装配本轮全部 provider。

    `include_disabled=True` 时 disabled 的 contract-only provider 也在注册表里 ——
    它们不会被真实调用（catalog 会先拦下），但让 `/federation/providers`
    能如实列出"存在但未启用"。
    """
    providers: dict[str, FederatedProvider] = {
        "literature": LiteratureAdapter(db),
        "project_asset": ProjectAssetAdapter(db),
        "foundational_math": FoundationalMathAdapter(
            foundational_engine, database_url=foundational_database_url
        ),
    }
    if include_disabled:
        providers["dataset"] = DatasetAdapter()
        providers["web_external"] = WebExternalAdapter()
    return providers


__all__ = ["build_providers"]
