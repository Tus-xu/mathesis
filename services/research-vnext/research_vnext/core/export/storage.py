"""RV14 · 导出产物的对象存储 key 规则。

复用 RV04 的 MinIO 客户端与 registry 冻结前缀（rvs/），
key 命名空间：`rvs/<tenant>/paper-exports/<artifact_id>/<filename>`。
"""

from __future__ import annotations

from .serializer import FORMAT_EXTENSIONS


def export_object_key(prefix: str, tenant_id: str, artifact_id: str, fmt: str) -> str:
    filename = FORMAT_EXTENSIONS[fmt]
    return f"{prefix}{tenant_id}/paper-exports/{artifact_id}/{filename}"
