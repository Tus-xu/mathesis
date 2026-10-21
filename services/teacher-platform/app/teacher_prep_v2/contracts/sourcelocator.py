"""SourceLocator 契约 DTO（R13）：证据/题目引用的版本化教材定位。

ref 规范（v1，与 textbook_v2.locator 同文，见权威实现
`app/textbook_v2/locator.py`，文本必须逐字对齐，rendezvous 测试双向锚定）：
    textbook_v2/locator/v1/source/<source_id>
        [/section/<section_id>]
        [/chunk/<chunk_id> | /chunk-range/<start>-<end>]
        [/unit/<unit_id> | /problem/<problem_id> | /asset/<asset_id>]

本 DTO 只做「形状 + 语法校验」，**不解析、不触库**；解析/权限落在
textbook_v2.locator.resolve_locator（属教材域事实，不在此重复实现）。
缺失定位（旧数据/无图源）→ 对象缺省，`unknown_flags` 如实标记，禁止造假页码。
"""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from .base import PREP_CONTRACTS_SCHEMA_VERSION, REF_RE, _ContractModel

# v1 规范格式（严格锚定到行尾；与 textbook_v2.locator._V1_RE 同文）
_V1_RE = re.compile(
    r"^textbook_v2/locator/v1/source/([0-9a-fA-F-]{36})"
    r"(?:/section/([0-9a-fA-F-]{36}))?"
    r"(?:/chunk/([0-9a-fA-F-]{36})|/chunk-range/(\d+)-(\d+))?"
    r"(?:/(unit|problem|asset)/([0-9a-fA-F-]{36}))?$"
)
# legacy 格式（旧 resolve_source_ref 产出，只读兼容）
_LEGACY_RE = re.compile(
    r"^textbook_v2/source/([0-9a-fA-F-]{36})"
    r"(?:/section/([0-9a-fA-F-]{36}))?"
    r"(?:/(unit|problem|chunk)/([0-9a-fA-F-]{36}))?$"
)


class SourceLocatorRef(_ContractModel):
    """版本化定位引用：`ref` 为 canonical v1 串，`schema_version` 由 ref 自描述。"""

    schema_version: str = Field(default=PREP_CONTRACTS_SCHEMA_VERSION)
    ref: str = Field(min_length=1, max_length=512, pattern=REF_RE)
    preview: str | None = Field(default=None, max_length=200)

    @field_validator("ref")
    @classmethod
    def _must_parse_as_locator(cls, v: str) -> str:
        if _V1_RE.match(v) is None and _LEGACY_RE.match(v) is None:
            raise ValueError(f"ref 不是合法 SourceLocator v1/legacy 串: {v!r}")
        return v
