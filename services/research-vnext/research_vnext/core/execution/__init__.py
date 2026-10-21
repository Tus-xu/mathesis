"""可插拔沙箱执行器（RV11 契约；真实执行属 WP-I）。"""

from .executor import (  # noqa: F401
    ExecutionRequest,
    ExecutionResult,
    ExecutorNotConfigured,
    ExperimentExecutor,
    HttpExecutor,
    NullExecutor,
    executor_configured,
    get_executor,
    reproducibility_ok,
    set_executor,
)

__all__ = [
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutorNotConfigured",
    "ExperimentExecutor",
    "HttpExecutor",
    "NullExecutor",
    "executor_configured",
    "get_executor",
    "reproducibility_ok",
    "set_executor",
]
