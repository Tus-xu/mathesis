"""PPT Studio V2 领域错误分类（R10）。

用途：把 TeachingDeckSpec / DeckPatch 的验证、并发与只读规则固化成明确的异常类型，
上层（application / api）据此区分「冲突可重试」与「契约非法」，不得用裸 ValueError
穿透到 presenter 层。

分类层次：
    DeckSpecError                   —— V2 领域契约根异常
    ├─ UnsupportedSchemaVersion     —— 未来/未知 major 的 schema_version（fail closed）
    ├─ DeckPatchError               —— patch 应用失败
    │  ├─ RevisionConflictError     —— expected_revision 与当前 revision 不符（乐观并发）
    │  └─ PatchValidationError      —— patch 操作本身非法
    │     ├─ PreserveOriginalViolationError —— 试图关闭 TEACHER_UPLOAD 的原件保护
    │     ├─ ImmutableFieldError    —— 试图修改不可变字段（object_key/sha256/…）
    │     ├─ UnknownSlideError      —— patch 目标 slide 不存在
    │     └─ UnknownAssetError      —— patch 目标 asset 不存在
"""

from __future__ import annotations


class DeckSpecError(RuntimeError):
    """TeachingDeckSpec 领域契约根异常。"""


class UnsupportedSchemaVersion(DeckSpecError):
    """读取到与当前实现不同 major 的 schema_version。

    策略：fail closed（不猜测语义），由调用方决定升级 reader 或拒绝请求。
    """

    def __init__(self, version: str, supported: str) -> None:
        self.version = version
        self.supported = supported
        super().__init__(f"不支持的 schema_version={version}（当前支持 {supported}）")


class DeckPatchError(DeckSpecError):
    """DeckPatch 应用失败基类。"""


class RevisionConflictError(DeckPatchError):
    """乐观并发冲突：调用方携带的 expected_revision 已过期。"""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"revision 冲突：期望 {expected}，当前 {actual}（请基于最新 revision 重放 patch）")


class PatchValidationError(DeckPatchError):
    """DeckPatchProposal 结构或语义非法。"""


class PreserveOriginalViolationError(PatchValidationError):
    """TEACHER_UPLOAD 原件保护被关闭（V2 Iron Law，任何 transform 不得关闭）。"""


class ImmutableFieldError(PatchValidationError):
    """试图修改不可变字段（原始资产 object_key/sha256/kind/preserve_original 等）。"""


class UnknownSlideError(PatchValidationError):
    """patch 引用了不存在的 slide id。"""


class UnknownAssetError(PatchValidationError):
    """patch 引用了不存在的 asset id。"""
