"""跨端契约公共基座（R13）：ID、租户/班级/会话/所有人、provenance、PII/retention、revision。

设计约束：
- 契约对 JSON/TS 消费方是“数据形状 + 语义”的双重承诺；本文件只定义形状与校验，
  不触达任何 ORM / 8000 数据（事实表法定在 8000，见 ADAPTER-MAPPING.md）。
- 所有 ID 一律为小写 UUID 字符串（`UuidStr`），JSON Schema 输出 `pattern`，
  前端 mini-validator 与 Python 侧 pidantic pattern 双端同文验证。
- `schema_version` 采用 `<major>.<minor>.<patch>`；major 断裂语义见
  BREAKING-CHANGE-RULES.md（reader 对 major>current fail closed）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# 当前契约目录版本；任何 major 断裂必须 bump（reader fail closed）
PREP_CONTRACTS_SCHEMA_VERSION = "1.0.0"

UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
IDEMPOTENCY_KEY_RE = r"^[A-Za-z0-9._:-]{8,128}$"
REF_RE = r"^[A-Za-z0-9._/-]{1,512}$"

# 稳定 ID 类型（全部为字符串形式，便于跨端传输；pattern 双端校验）
UuidStr = Annotated[str, Field(pattern=UUID_RE)]
TenantId = UuidStr
ClassId = UuidStr
SessionId = UuidStr
OwnerId = UuidStr
StudentId = UuidStr
IdempotencyKey = Annotated[str, Field(pattern=IDEMPOTENCY_KEY_RE)]
OpaqueRef = Annotated[str, Field(pattern=REF_RE)]

# 数据敏感分级（PII/retention 审计口径，生产企业保留策略在 ADAPTER-MAPPING.md 映射表）
PiiClass = Literal[
    "STUDENT_IDENTIFIER",       # 学生身份/学号
    "STUDENT_ANSWERS",          # 学生作答内容
    "POSSIBLY_HANDWRITING",     # 可能含手写内容的证据图
    "MATH_REASONING",           # 数学推理（一般无身份信息）
    "AGGREGATE_ONLY",           # 聚合/画像（不含身份）
]
RetentionPolicy = Literal[
    "TERM",                     # 当期学期结束即到期
    "TERM_1YEAR",               # 学期 + 1 年
    "ACADEMIC_CYCLE",           # 学段周期
    "ANONYMIZED",               # 匿名化后可长存
]


class _ContractModel(BaseModel):
    """全部契约模型基类：
    - extra="ignore"：同 major 内的新增字段对旧 reader 容忍（向前兼容）；
    - str_strip_whitespace：字段去首尾空白，避免脏输入进入语义。
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class DataRetention(_ContractModel):
    """数据保留策略（PII/retention）。

    策略在契约层只做“声明”，落地执行（TTL 任务、匿名化、删除权限）
    属于生产企业（8000/8100 各自事实域）的运维责任，见 BREAKING-CHANGE-RULES.md。
    """

    policy: RetentionPolicy  # type: ignore[valid-type]
    ttl_days: int | None = Field(default=None, ge=1)
    owner_delete_allowed: bool = Field(default=False)
    justification: str | None = None


class Provenance(_ContractModel):
    """来源与幂等元数据（数据血缘 + 重试去重锚点）。

    - `idempotency_key`：写路径的请求指纹键，配合
      app/reliability/idempotency.py 的 reserve/complete 使用（重试返首响应）；
    - `source`：LLM proposal 语义（ai_suggestion）与教师确认事实（teacher）必须分开，
      禁止把未确认 AI 输出当事实。
    """

    producer_service: str = Field(min_length=1, max_length=64)  # teacher-platform | api | student-client
    producer_version: str = Field(min_length=1, max_length=32)
    actor_id: UuidStr
    idempotency_key: IdempotencyKey
    created_at: datetime
    source: Literal["teacher", "student", "system", "ai_suggestion", "migration"] = "teacher"
    note: str | None = Field(default=None, max_length=512)


class RevisionMixin(_ContractModel):
    """乐观并发：每次语义变更由调用方 +1；patch 类入口必须校验 expected_revision。"""

    revision: int = Field(default=0, ge=0)


def utc_now() -> datetime:
    return datetime.now(UTC)


# 根模型字段顺序（registry / catalog 生成使用）：保持稳定，避免无谓 schema 漂移
CONTRACT_KINDS = (
    "PreClassDiagnosticTask",
    "ClassroomTask",
    "TaskAssignment",
    "StudentSubmission",
    "Evidence",
    "ClassLearningProfile",
)
