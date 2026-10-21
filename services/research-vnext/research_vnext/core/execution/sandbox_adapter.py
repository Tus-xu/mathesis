"""RV11 · sandbox adapter —— research 执行契约 → `services/sandbox` 容器执行。

**边界（诚实）**：
- 本模块**只做映射与解析**。真正的隔离由 `services/sandbox` 的 container backend
  提供（non-root / read-only rootfs / `--network=none` / cgroups / 每次运行独立
  临时工作区）。用户/模型代码**只**在 Linux 容器里执行——8300 Web 进程与 worker
  进程都不执行它（worker 进程只负责发起 `docker run` 并读取结果）。
- 隔离上限**不是本模块发明的**：容器后端的诚实矩阵见
  `services/sandbox/docs/ISOLATION-MATRIX.md`；每次运行的 manifest 都逐项记录
  哪些控制在位、哪些不在位及原因。普通容器隔离 **不是终局安全**（容器逃逸、
  rootful daemon 风险见其 KNOWN GAPS）——前端与结论层必须以 manifest 为准。
- adapter 对限额做**防御性再钳制**：即使上游漏掉策略门，也不可能把超限值
  传给容器（sandbox 的 pydantic 契约同样会拒绝越界值）。

产出物契约：实验代码通过 stdout 最后一行 `RESULT_JSON: {...}` 返回
`{metrics, table, figure}`；worker 侧据此**物化** artifacts（metrics.json /
table.csv / figure.svg），每个文件记录 sha256 且 `derivedFrom=outputHash`
（沙箱内代码无文件写能力——`open` 被守卫拒绝——因此 artifact 只能从
哈希链覆盖的输出确定性导出，这本身就是 lineage）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.experiment_run import (
    ENV_ALLOWLIST,
    LIMIT_CEILINGS,
    build_repro_record,
)

#: 实验结果信封前缀（runner 输出的最后一行）。
RESULT_JSON_PREFIX = "RESULT_JSON:"

#: 默认钉住的沙箱镜像（本机已构建的 non-root 镜像；digest 在 manifest 中记录）。
DEFAULT_PINNED_IMAGE = "math-arena-sandbox:0.2.0"

_SANDBOX_CLAMPS: dict[str, tuple[str, float, float]] = {
    # limit key -> (ResourceLimits field, min, max)
    "cpuLimit": ("cpu_limit", 0.1, LIMIT_CEILINGS.cpu),
    "memoryLimitMb": ("memory_limit_mb", 32, LIMIT_CEILINGS.memory_mb),
    "pidsLimit": ("pids_limit", 1, LIMIT_CEILINGS.pids),
    "diskLimitMb": ("disk_limit_mb", 1, LIMIT_CEILINGS.disk_mb),
    "wallTimeMs": ("wall_time_ms", 100, LIMIT_CEILINGS.wall_ms),
    "outputLimitBytes": ("output_limit_bytes", 1024, LIMIT_CEILINGS.output_bytes),
}


class SandboxUnavailable(RuntimeError):
    """sandbox 包不可导入/不可用。**必须**诚实失败，不得伪造运行。"""


def sandbox_source() -> Path:
    """`services/sandbox/src` 的位置（env `RESEARCH_SANDBOX_SRC` 可覆盖）。"""
    raw = (os_env().get("RESEARCH_SANDBOX_SRC") or "").strip()
    if raw:
        return Path(raw)
    # research_vnext/core/execution/sandbox_adapter.py
    #   parents: [0]=execution [1]=core [2]=research_vnext [3]=research-vnext [4]=services
    return Path(__file__).resolve().parents[4] / "sandbox" / "src"


def os_env() -> dict[str, str]:
    import os

    return dict(os.environ)


def pinned_image() -> str:
    return (os_env().get("RESEARCH_SANDBOX_IMAGE") or "").strip() or DEFAULT_PINNED_IMAGE


_MODULE_CACHE: dict[str, Any] = {}


class _SandboxFacade:
    """sandbox 包的统一门面：orchestrator + models 的属性直通。"""

    def __init__(self) -> None:
        import sandbox.models as _models  # noqa: PLC0415
        import sandbox.orchestrator as _orchestrator  # noqa: PLC0415

        self._orch = _orchestrator
        self._models = _models

    def __getattr__(self, name: str) -> Any:
        for source in (self._orch, self._models):
            if hasattr(source, name):
                return getattr(source, name)
        raise AttributeError(f"sandbox facade has no attribute {name!r}")


def load_sandbox_module() -> Any:
    """按路径加载 `sandbox` 包（独立服务，不进 research-vnext 依赖清单）。"""
    key = str(sandbox_source())
    if key in _MODULE_CACHE:
        return _MODULE_CACHE[key]
    if key not in sys.path:
        sys.path.insert(0, key)
    try:
        facade = _SandboxFacade()
    except ModuleNotFoundError as e:
        raise SandboxUnavailable(
            f"sandbox 包不可导入（{sandbox_source()}）：{e}。"
            "真实沙箱执行不可用——本服务拒绝伪造一次实验运行。"
        ) from e
    _MODULE_CACHE[key] = facade
    return facade


# --------------------------------------------------------------------------
# 请求映射
# --------------------------------------------------------------------------


def to_sandbox_request(req: Any, *, attempt: int = 1) -> Any:
    """research `ExecutionRequest` → sandbox `ExecutionRequest`。

    `run_id` 确定性地绑定 `(job_id, attempt)`：同 job 同 attempt 复跑得到同一
    run_id（manifest 可对账），不同 attempt 天然可区分。
    """
    sandbox = load_sandbox_module()

    code = (req.code or "").strip()
    if not code:
        raise SandboxUnavailable("ExecutionRequest.code 为空：没有代码就不存在诚实的运行")

    limits_kwargs: dict[str, Any] = {}
    for key, (field_name, low, high) in _SANDBOX_CLAMPS.items():
        value = (req.limits or {}).get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            value = {"cpuLimit": 1.0, "memoryLimitMb": 512, "pidsLimit": 64, "diskLimitMb": 64,
                     "wallTimeMs": 30000, "outputLimitBytes": 256 * 1024}[key]
        limits_kwargs[field_name] = float(min(max(float(value), low), high))
    if "wall_time_ms" in limits_kwargs:
        limits_kwargs["wall_time_ms"] = int(limits_kwargs["wall_time_ms"])
    for int_field in ("memory_limit_mb", "pids_limit", "disk_limit_mb", "output_limit_bytes"):
        limits_kwargs[int_field] = int(limits_kwargs[int_field])

    inputs: list[Any] = []
    for item in req.inputs or []:
        text = str(item.get("text") or "")
        sha = item.get("sha256") or hashlib.sha256(text.encode("utf-8")).hexdigest()
        inputs.append(
            sandbox.InputRef(name=str(item.get("name") or "input"), inline=text, sha256=sha)
        )

    image = pinned_image()
    return sandbox.ExecutionRequest(
        run_id=f"rv11-{req.job_id}-{int(attempt)}",
        experiment_id=str(req.plan_id) if req.plan_id else None,
        code=code,
        inputs=inputs,
        params=dict(req.params or {}),
        seed=int(req.seed if req.seed is not None else 0),
        dependency_lock=sandbox.DependencyLock(format="image-pin", image=image),
        limits=sandbox.ResourceLimits(**limits_kwargs),
        # 平台策略：真实隔离 + 禁网 + 模块守卫。任何一个都不由调用方翻转。
        policy=sandbox.SandboxPolicy(
            require_real_isolation=True,
            network=sandbox.NetworkPolicy.DENY,
            enforce_module_allowlist=True,
        ),
        backend="container",
        sandbox_image=image,
        labels={"jobId": str(req.job_id)[:64], "attempt": str(int(attempt))},
    )


# --------------------------------------------------------------------------
# 输出解析 / artifact 物化计划
# --------------------------------------------------------------------------


def parse_result_envelope(stdout: str) -> dict[str, Any]:
    """取 stdout 中**最后一行** `RESULT_JSON:` 信封；缺失/损坏 ⇒ `{}`（不抛）。"""
    envelope: dict[str, Any] = {}
    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith(RESULT_JSON_PREFIX):
            continue
        raw = stripped[len(RESULT_JSON_PREFIX):].strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            envelope = parsed
    return envelope


def detect_guard_violation(stderr: str) -> dict[str, str] | None:
    """从 stderr 识别 runner 的 `GUARD_VIOLATION[CODE]: detail`。"""
    for line in (stderr or "").splitlines():
        marker = "GUARD_VIOLATION["
        start = line.find(marker)
        if start < 0:
            continue
        rest = line[start + len(marker):]
        code, _, detail = rest.partition("]:")
        return {"code": code.strip(), "detail": detail.strip()}
    return None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_artifacts(run_id: str, envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """把 `RESULT_JSON` 信封物化为 artifact 文件计划（内容 + sha256 + lineage）。"""
    plan: list[dict[str, Any]] = []

    def _add(path: str, text: str) -> None:
        data = text.encode("utf-8")
        plan.append(
            {
                "path": path,
                "content": text,
                "sizeBytes": len(data),
                "sha256": _sha256_bytes(data),
                "derivedFrom": "outputHash",
                "runId": run_id,
            }
        )

    metrics = envelope.get("metrics")
    if isinstance(metrics, dict) and metrics:
        _add("artifacts/metrics.json", json.dumps(metrics, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    table = envelope.get("table")
    if isinstance(table, dict) and table.get("columns") and isinstance(table.get("rows"), list):
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow([str(c) for c in table["columns"]])
        for row in table["rows"]:
            writer.writerow(["" if v is None else str(v) for v in row])
        _add("artifacts/table.csv", buffer.getvalue())

    figure = envelope.get("figure")
    if isinstance(figure, dict) and figure.get("points"):
        _add("artifacts/figure.svg", _render_figure_svg(figure))

    return plan


def _render_figure_svg(figure: dict[str, Any]) -> str:
    """把数据点渲染成确定性 SVG 折线图（无时间戳/随机性 → 可复现哈希）。"""
    points = [
        (float(p[0]), float(p[1]))
        for p in (figure.get("points") or [])
        if isinstance(p, (list, tuple)) and len(p) >= 2
    ]
    points.sort(key=lambda xy: xy[0])
    title = str(figure.get("title") or "experiment figure")
    x_label = str(figure.get("xLabel") or "x")
    y_label = str(figure.get("yLabel") or "y")
    width, height, pad = 640, 400, 48
    if not points:
        xs = ys = [0.0]
    else:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    span_x = (x_max - x_min) or 1.0
    span_y = (y_max - y_min) or 1.0

    def px(x: float) -> float:
        return round(pad + (x - x_min) / span_x * (width - 2 * pad), 2)

    def py(y: float) -> float:
        return round(height - pad - (y - y_min) / span_y * (height - 2 * pad), 2)

    coords = " ".join(f"{px(x)},{py(y)}" for x, y in points)
    dots = "".join(
        f'<circle cx="{px(x)}" cy="{py(y)}" r="3" fill="#c2452d" />' for x, y in points
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 440" role="img" '
        f'aria-label="{title}">'
        "<rect width='680' height='440' fill='#ffffff'/>"
        f"<text x='20' y='28' font-size='16' fill='#1a1a1a'>{_esc(title)}</text>"
        f"<line x1='{pad}' y1='{height - pad}' x2='{width - pad}' y2='{height - pad}' stroke='#999' />"
        f"<line x1='{pad}' y1='{pad}' x2='{pad}' y2='{height - pad}' stroke='#999' />"
        f"<polyline fill='none' stroke='#1f4e79' stroke-width='2' points='{coords}' />"
        f"{dots}"
        f"<text x='{width - pad}' y='{height - 16}' font-size='12' fill='#555' text-anchor='end'>{_esc(x_label)}</text>"
        f"<text x='14' y='{pad - 12}' font-size='12' fill='#555'>{_esc(y_label)}</text>"
        "</svg>"
    )


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# --------------------------------------------------------------------------
# 结果映射
# --------------------------------------------------------------------------


def result_from_sandbox(
    *,
    job_id: str,
    attempt: int,
    manifest: dict[str, Any],
    stdout: str,
    stderr: str,
    argv: list[str],
    requested_limits: dict[str, Any],
    env_allowlist: list[str] | None = None,
) -> Any:
    """sandbox 结果 → research `ExecutionResult`（含完整 repro 记录）。"""
    from .executor import ExecutionResult

    envelope = parse_result_envelope(stdout)
    metrics = envelope.get("metrics") if isinstance(envelope.get("metrics"), dict) else {}
    artifacts = plan_artifacts(manifest.get("run_id") or f"rv11-{job_id}-{attempt}", envelope)
    figures = [item for item in artifacts if item["path"].endswith(".svg")]
    repro = build_repro_record(
        manifest,
        requested_limits=requested_limits,
        command=list(argv or []),
        env_allowlist=env_allowlist if env_allowlist is not None else sorted(ENV_ALLOWLIST),
    )
    exit_code = manifest.get("exit_code")
    return ExecutionResult(
        exit_code=int(exit_code) if exit_code is not None else 1,
        logs_ref="",
        figures=[{"path": f["path"], "sha256": f["sha256"]} for f in figures],
        metrics=dict(metrics),
        output={"envelopeKeys": sorted(envelope), "artifactsPlan": artifacts},
        repro=repro,
        stdout=stdout or "",
        stderr=stderr or "",
        claim_holds=None,  # 永不在此层宣布结论成立
        succeeded_stages=["execute"] if manifest.get("exec_status") == "pass" else [],
        failed_stages=[] if manifest.get("exec_status") == "pass" else ["execute"],
        not_reproducible_reason="",
    )


# --------------------------------------------------------------------------
# 执行器
# --------------------------------------------------------------------------


@dataclass
class _RawOutcome:
    manifest: dict[str, Any]
    stdout: str
    stderr: str
    argv: list[str]
    error: str | None


class SandboxExecutor:
    """`ExperimentExecutor` 协议的容器实现（生产路径）。

    `run()` 把阻塞的容器执行放进线程池，避免卡死 worker 事件循环；
    结果永远携带完整 manifest，`claim_holds` 恒为 None。
    """

    name = "sandbox"

    def __init__(self) -> None:
        self._module: Any = None

    @property
    def configured(self) -> bool:
        try:
            load_sandbox_module()
            return True
        except SandboxUnavailable:
            return False

    async def run(self, request: Any) -> Any:
        import asyncio

        sreq = to_sandbox_request(request, attempt=int(getattr(request, "attempt", 1) or 1))
        outcome = await asyncio.to_thread(self._run_blocking, sreq)
        if outcome.error is not None and not outcome.manifest:
            raise SandboxUnavailable(outcome.error)
        return result_from_sandbox(
            job_id=str(request.job_id),
            attempt=int(getattr(request, "attempt", 1) or 1),
            manifest=outcome.manifest,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            argv=outcome.argv,
            requested_limits=dict(getattr(request, "limits", {}) or {}),
            env_allowlist=sorted(ENV_ALLOWLIST),
        )

    def _run_blocking(self, sreq: Any) -> _RawOutcome:
        orchestrator = load_sandbox_module()
        try:
            result = orchestrator.run_experiment(sreq)
        except orchestrator.PayloadError as e:  # type: ignore[attr-defined]
            return _RawOutcome({}, "", f"PayloadError: {e}", [], str(e))
        except Exception as e:  # noqa: BLE001 - 后端崩溃也要变成可记录的失败
            return _RawOutcome({}, "", f"{type(e).__name__}: {e}", [], str(e))
        return _RawOutcome(
            manifest=result.manifest.model_dump(mode="json"),
            stdout=result.stdout,
            stderr=result.stderr,
            argv=list(result.argv or []),
            error=None,
        )


__all__ = [
    "DEFAULT_PINNED_IMAGE",
    "RESULT_JSON_PREFIX",
    "SandboxExecutor",
    "SandboxUnavailable",
    "detect_guard_violation",
    "parse_result_envelope",
    "pinned_image",
    "plan_artifacts",
    "result_from_sandbox",
    "sandbox_source",
    "to_sandbox_request",
]
