"""可插拔沙箱执行器接口（RV11）。

**边界声明（诚实）**：真实沙箱执行是 **WP-I** 的交付物。本模块只定义
**类型化契约**与一个可插拔接口，并提供一个"未配置即诚实失败"的默认实现。

因此本服务**永远不会**假装跑过实验：
- `NullExecutor` 抛 `ExecutorNotConfigured`（`EXECUTOR_NOT_CONFIGURED` 属于
  **永久**错误 ⇒ 立即失败、不重试）；
- `ExperimentRunRow.exit_code` 与 `claim_holds` 是**两个独立字段** ——
  执行器成功返回 0 **不**意味着结论成立；`claim_holds` 保持 `None`（未判定）
  直到有独立的验证记录（`VerificationRecord`）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class ExecutorNotConfigured(RuntimeError):
    """没有配置沙箱执行器。**必须**诚实失败，不得伪造一次运行。"""


@dataclass
class ExecutionRequest:
    """一次实验运行的输入（全部是可复现元数据的**引用**）。

    RV11 起附加字段（加法式，向后兼容）：
    - `code`：实验代码正文（dispatch 时已由策略门记录 sha256）；
    - `inputs`：具名输入 `[{name, text, sha256?}]`（宿主侧读取并哈希）；
    - `limits`：**策略门裁决后**的有效限额（adapter 仍会防御性再钳制）；
    - `attempt`：当前 attempt（进 run_id，保证每次 attempt 的 run 可区分）；
    - `reproduce_of`：复现来源 run id（复现运行才携带）。
    """

    job_id: str
    session_id: str
    plan_id: str | None
    code_ref: str
    dataset_ref: str
    environment: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    seed: int | None = None
    timeout_seconds: int = 300
    canary: bool = True
    code: str | None = None
    inputs: list[dict[str, Any]] = field(default_factory=list)
    limits: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1
    reproduce_of: str | None = None


@dataclass
class ExecutionResult:
    """一次运行的结果。

    `exit_code` **不**携带结论语义。`claim_holds` 由独立的验证步骤决定；
    执行器返回时它**必须**保持 `None`。
    """

    exit_code: int
    logs_ref: str = ""
    figures: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    repro: dict[str, Any] = field(default_factory=dict)
    #: 容器内 stdout 原文（日志落盘与 RESULT_JSON 解析源）
    stdout: str = ""
    #: 容器内 stderr 原文（守卫裁决/traceback 源；worker 据此识别 GUARD_VIOLATION）
    stderr: str = ""
    #: 显式地**不**给默认 True：None = 尚未判定
    claim_holds: bool | None = None
    succeeded_stages: list[str] = field(default_factory=list)
    failed_stages: list[str] = field(default_factory=list)
    not_reproducible_reason: str = ""


@runtime_checkable
class ExperimentExecutor(Protocol):
    """沙箱执行器协议（WP-I 实现它；本服务只按此协议调用）。"""

    name: str
    configured: bool

    async def run(self, request: ExecutionRequest) -> ExecutionResult: ...


class NullExecutor:
    """未配置执行器时的默认实现：**任何**调用都失败。"""

    name = "null"
    configured = False

    async def run(self, request: ExecutionRequest) -> ExecutionResult:  # noqa: ARG002
        raise ExecutorNotConfigured(
            "未配置沙箱执行器（RESEARCH_EXECUTOR_ENDPOINT / RESEARCH_EXECUTOR 均未设置）。"
            "真实沙箱执行属 WP-I 交付范围；本服务拒绝伪造一次实验运行。"
        )


class HttpExecutor:
    """把执行委托给外部沙箱服务（WP-I 的 HTTP 契约）。

    只做一次 POST + 结果映射。**不做**重试：重试由 job runtime 的
    退避与分类统一负责，避免两层重试叠加成重试风暴。
    """

    name = "http"

    def __init__(self, endpoint: str, *, token: str = "", timeout_seconds: int = 300) -> None:
        self._endpoint = endpoint
        self._token = token
        self._timeout = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self._endpoint)

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        payload = {
            "job_id": request.job_id,
            "session_id": request.session_id,
            "plan_id": request.plan_id,
            "code_ref": request.code_ref,
            "dataset_ref": request.dataset_ref,
            "environment": request.environment,
            "params": request.params,
            "stages": request.stages,
            "seed": request.seed,
            "timeout_seconds": request.timeout_seconds,
        }
        async with httpx.AsyncClient(trust_env=False, timeout=self._timeout) as c:
            resp = await c.post(self._endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return ExecutionResult(
            exit_code=int(data.get("exit_code", 1)),
            logs_ref=str(data.get("logs_ref", "")),
            figures=list(data.get("figures", []) or []),
            metrics=dict(data.get("metrics", {}) or {}),
            output=dict(data.get("output", {}) or {}),
            repro=dict(data.get("repro", {}) or {}),
            # 执行器的**任何**输出都不得被解释成"结论成立"
            claim_holds=None,
            succeeded_stages=list(data.get("succeeded_stages", []) or []),
            failed_stages=list(data.get("failed_stages", []) or []),
            not_reproducible_reason=str(data.get("not_reproducible_reason", "")),
        )


_executor: ExperimentExecutor | None = None


def get_executor() -> ExperimentExecutor:
    """当前进程的执行器。

    - `RESEARCH_EXECUTOR_ENDPOINT` 设置 → HTTP 执行器（外部沙箱服务）；
    - `RESEARCH_EXECUTOR=sandbox` → **内置 sandbox adapter**（容器后端，
      用户代码只在 Linux 容器里执行，worker 进程本身不执行用户代码）；
    - 都没配 → NullExecutor（诚实失败）。
    """
    global _executor
    if _executor is None:
        kind = os.getenv("RESEARCH_EXECUTOR", "").strip().lower()
        endpoint = os.getenv("RESEARCH_EXECUTOR_ENDPOINT", "").strip()
        if endpoint:
            _executor = HttpExecutor(
                endpoint,
                token=os.getenv("RESEARCH_EXECUTOR_TOKEN", ""),
                timeout_seconds=int(os.getenv("RESEARCH_EXECUTOR_TIMEOUT_SECONDS", "300")),
            )
        elif kind == "sandbox":
            from .sandbox_adapter import SandboxExecutor

            _executor = SandboxExecutor()
        else:
            _executor = NullExecutor()
    return _executor


def set_executor(e: ExperimentExecutor | None) -> None:
    global _executor
    _executor = e


def executor_configured() -> bool:
    return get_executor().configured


def reproducibility_ok(repro: dict[str, Any]) -> tuple[bool, str]:
    """可复现性判定。

    缺失 image digest 或依赖锁哈希 ⇒ **不可复现**。这是刻意的严格判据：
    "在我机器上能跑"不是可复现。
    """
    missing = [k for k in ("imageDigest", "dependencyLockHash", "seed", "codeHash") if not repro.get(k)]
    if missing:
        return False, f"缺少可复现元数据: {', '.join(missing)}"
    return True, ""
