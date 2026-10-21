"""RV09 · 记忆层（短期 checkpoint + 长期偏好命名空间）。"""

from .namespaces import (  # noqa: F401
    FORBIDDEN_SOURCES,
    MemoryNamespaceError,
    MemoryNamespaceViolation,
    MemoryPurposeUnknown,
    Namespace,
    PURPOSES,
    assert_same_namespace,
    assert_source_allowed,
    make_namespace,
    namespace_key,
)

__all__ = [
    "FORBIDDEN_SOURCES",
    "MemoryNamespaceError",
    "MemoryNamespaceViolation",
    "MemoryPurposeUnknown",
    "Namespace",
    "PURPOSES",
    "assert_same_namespace",
    "assert_source_allowed",
    "make_namespace",
    "namespace_key",
]
