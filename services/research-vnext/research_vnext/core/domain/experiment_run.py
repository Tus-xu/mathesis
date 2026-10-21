"""RV11 · ExperimentRun 域 —— 策略门 / manifest 记录 / 复现对比 / 结果映射。

本模块是实验执行的**纯决策层**（不做 I/O，不碰 Docker）：

1. **策略门**（`apply_policy_gate`）：dispatch 前对"请求的资源/网络/env"做裁决。
   默认全 deny、全下限；任何试图**提高**资源或打开网络的请求一律拒绝
   （`LIMIT_ESCALATION_REJECTED` / `NETWORK_ESCALATION_REJECTED`）——
   模型（或被劫持的调用方）不能给自己加资源。
2. **manifest 记录**（`build_repro_record`）：把 sandbox 的执行 manifest 变成
   ExperimentRun 的可复现元数据记录。缺字段即拒（`REPRO_MANIFEST_INCOMPLETE`），
   绝不允许"少记一点也能过"。
3. **复现对比**（`compare_with_original`）：同 manifest 复现后的诚实判定——
   输出哈希一致 ⇒ EXACT_MATCH；哈希不一致但指标在容差内 ⇒ METRICS_TOLERANCE；
   否则 MISMATCH；缺元数据 ⇒ INSUFFICIENT_DATA（**不猜**）。
4. **结果映射**（`exec_status_to_outcome`）：sandbox exec_status → 真实
   SUCCEEDED/FAILED/CANCELLED + 错误分类。`pass` 绝不携带"结论成立"语义
   （`claim_holds` 属于 RV12 验证域，本层永不设置它）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# 服务器上限（ ceilings）：dispatch 的硬约束，模型不可见、不可协商
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LimitCeilings:
    """单次实验的硬上限。高于它 = 提权请求 = 拒绝（不是静默钳制）。"""

    cpu: float = 2.0
    memory_mb: int = 2048
    pids: int = 256
    disk_mb: int = 512
    wall_ms: int = 240_000
    output_bytes: int = 4 * 1024 * 1024


LIMIT_CEILINGS = LimitCeilings()

#: 每次运行允许注入容器 env 的**封闭**词表。容器内 env 本就是固定字面量
#: （见 sandbox 常量），这里只允许确定性相关键；任何凭证类键直接拒绝。
ENV_ALLOWLIST: frozenset[str] = frozenset({"PYTHONHASHSEED"})

#: 网络策略是**平台级常量**：研究实验默认且只能是 deny。
NETWORK_POLICY = "deny"

#: 超时预算：sandbox 墙钟 + 心跳/落库余量必须小于 worker 单 stage 超时，
#: 否则 worker 会先杀掉 handler（STAGE_TIMEOUT 可重试）→ 无意义的重试风暴。
_STAGE_HEADROOM_MS = 60_000

_LIMIT_FIELDS: dict[str, tuple[str, type]] = {
    "cpuLimit": ("cpu", float),
    "memoryLimitMb": ("memory_mb", int),
    "pidsLimit": ("pids", int),
    "diskLimitMb": ("disk_mb", int),
    "wallTimeMs": ("wall_ms", int),
    "outputLimitBytes": ("output_bytes", int),
}

_ALIASES: dict[str, str] = {
    "cpu_limit": "cpuLimit",
    "memory_limit_mb": "memoryLimitMb",
    "pids_limit": "pidsLimit",
    "disk_limit_mb": "diskLimitMb",
    "wall_time_ms": "wallTimeMs",
    "output_limit_bytes": "outputLimitBytes",
}


class ExperimentPolicyRejected(Exception):
    """策略门拒绝（可对用户解释的拒绝码；由上层转成 400 DomainError）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PolicyDecision:
    effective: dict[str, Any]
    facts: dict[str, Any] = field(default_factory=dict)


def _norm_limit_key(key: str) -> str:
    return _ALIASES.get(str(key), str(key))


def apply_policy_gate(
    requested: dict[str, Any] | None,
    *,
    network: str | None = None,
    environment: dict[str, str] | None = None,
    stage_timeout_seconds: int | None = None,
) -> PolicyDecision:
    """对一次 dispatch 的资源/网络/env 请求做**全有或全无**的裁决。"""
    requested = requested or {}
    if not isinstance(requested, dict):
        raise ExperimentPolicyRejected("LIMIT_VALUE_INVALID", "limits 必须是对象")

    normalized: dict[str, Any] = {}
    for raw_key, value in requested.items():
        key = _norm_limit_key(str(raw_key))
        if key not in _LIMIT_FIELDS:
            raise ExperimentPolicyRejected(
                "LIMIT_FIELD_UNKNOWN",
                f"未知资源字段 {raw_key!r}（允许: {sorted(_LIMIT_FIELDS)}）——"
                "不认识的请求不能被静默忽略",
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ExperimentPolicyRejected(
                "LIMIT_VALUE_INVALID", f"{key} 必须是正数，收到 {value!r}"
            )
        normalized[key] = value

    ceiling_map = {
        "cpuLimit": LIMIT_CEILINGS.cpu,
        "memoryLimitMb": LIMIT_CEILINGS.memory_mb,
        "pidsLimit": LIMIT_CEILINGS.pids,
        "diskLimitMb": LIMIT_CEILINGS.disk_mb,
        "wallTimeMs": LIMIT_CEILINGS.wall_ms,
        "outputLimitBytes": LIMIT_CEILINGS.output_bytes,
    }
    escalated = [k for k, v in normalized.items() if float(v) > float(ceiling_map[k])]
    if escalated:
        raise ExperimentPolicyRejected(
            "LIMIT_ESCALATION_REJECTED",
            f"请求超出服务器上限: {', '.join(f'{k}={normalized[k]}' for k in sorted(escalated))}；"
            f"上限 {json_compact(ceiling_map)}。模型不能提高资源配额。",
        )

    wall_ms = int(normalized.get("wallTimeMs", ceiling_map["wallTimeMs"]))
    if stage_timeout_seconds is not None:
        budget_ms = max(0, int(stage_timeout_seconds) * 1000 - _STAGE_HEADROOM_MS)
        if wall_ms > budget_ms:
            raise ExperimentPolicyRejected(
                "WALL_TIME_EXCEEDS_STAGE_BUDGET",
                f"wallTimeMs={wall_ms} 超出 worker stage 预算 {budget_ms}ms"
                f"（stage {stage_timeout_seconds}s - {_STAGE_HEADROOM_MS // 1000}s 余量）——"
                "超预算会被 worker 先杀死并进入无意义重试",
            )

    if network is not None and str(network).lower() != NETWORK_POLICY:
        raise ExperimentPolicyRejected(
            "NETWORK_ESCALATION_REJECTED",
            f"实验网络策略固定为 {NETWORK_POLICY}；请求 network={network!r} 是提权，予以拒绝。",
        )

    environment = environment or {}
    if not isinstance(environment, dict):
        raise ExperimentPolicyRejected("ENV_VALUE_INVALID", "environment 必须是对象")
    for key, value in environment.items():
        if str(key) not in ENV_ALLOWLIST:
            raise ExperimentPolicyRejected(
                "ENV_KEY_NOT_ALLOWED",
                f"env 键 {key!r} 不在允许列表 {sorted(ENV_ALLOWLIST)} 内——"
                "sandbox 不注入任何主机/凭证环境变量",
            )
        if str(value) != "0":
            raise ExperimentPolicyRejected(
                "ENV_VALUE_INVALID", f"env {key} 只接受确定值 '0'，收到 {value!r}"
            )

    effective = {
        "cpuLimit": float(normalized.get("cpuLimit", 1.0)),
        "memoryLimitMb": int(normalized.get("memoryLimitMb", 512)),
        "pidsLimit": int(normalized.get("pidsLimit", 64)),
        "diskLimitMb": int(normalized.get("diskLimitMb", 64)),
        "wallTimeMs": int(normalized.get("wallTimeMs", min(30_000, LIMIT_CEILINGS.wall_ms))),
        "outputLimitBytes": int(
            normalized.get("outputLimitBytes", 256 * 1024)
        ),
        "network": NETWORK_POLICY,
    }
    facts = {
        "network": NETWORK_POLICY,
        "envAllowlist": sorted(ENV_ALLOWLIST),
        "ceilings": ceiling_map,
        "escalationBlocked": escalated,
    }
    return PolicyDecision(effective=effective, facts=facts)


def json_compact(obj: Any) -> str:
    import json

    return json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


# --------------------------------------------------------------------------
# manifest 记录
# --------------------------------------------------------------------------

_REQUIRED_SANDBOX_KEYS = (
    "code_hash",
    "input_hash",
    "output_hash",
    "dependency_lock_hash",
    "image_digest",
    "seed",
    "exec_status",
)


def build_repro_record(
    manifest: dict[str, Any],
    *,
    requested_limits: dict[str, Any],
    command: list[str],
    env_allowlist: list[str],
) -> dict[str, Any]:
    """sandbox manifest → ExperimentRun.repro 记录（缺关键字段即拒）。

    记录的是任务书要求的全部字段：imageDigest / dependencyLock / seed /
    code/input/output hashes / command / envAllowlist / resourceLimits。
    """
    missing = [k for k in _REQUIRED_SANDBOX_KEYS if manifest.get(k) in (None, "")]
    if missing:
        raise ExperimentPolicyRejected(
            "REPRO_MANIFEST_INCOMPLETE",
            f"sandbox manifest 缺少关键字段: {missing} —— 不完整的溯源记录不能入库",
        )
    isolation_controls = manifest.get("isolation_controls_enforced") or {}
    if not isinstance(isolation_controls, dict):
        isolation_controls = dict(isolation_controls)
    return {
        "codeHash": manifest["code_hash"],
        "inputHash": manifest["input_hash"],
        "outputHash": manifest["output_hash"],
        "artifactHash": manifest.get("artifact_hash") or "",
        "image": manifest.get("image") or "",
        "imageDigest": manifest["image_digest"],
        "dependencyLockHash": manifest["dependency_lock_hash"],
        "dependencyLockFormat": str(manifest.get("dependency_lock_format") or ""),
        "seed": int(manifest["seed"]),
        "execStatus": str(manifest["exec_status"]),
        "command": [str(x) for x in (command or [])],
        "envAllowlist": sorted(env_allowlist or []),
        "resourceLimits": {"requested": dict(requested_limits or {}), "effective": dict(manifest.get("effective_limits") or {})},
        "isolationMode": str(manifest.get("isolation_mode") or ""),
        "isolationControls": isolation_controls,
        "unenforcedReasons": dict(manifest.get("unenforced_reasons") or {}),
        "sandboxVersion": str(manifest.get("sandbox_version") or ""),
        "platform": str(manifest.get("platform") or ""),
        "warnings": [str(w) for w in (manifest.get("warnings") or [])],
        "truncation": dict(manifest.get("truncation") or {}),
        "durationMs": int(manifest.get("duration_ms") or 0),
    }


#: 复现对比所需最低字段。`outputHash` 在这里也是必须的——
#: （executor 侧的 4 键判定管"能不能宣称可复现"；本表管"能不能做诚实对比"，
#: 没有输出哈希的两侧对比只能是猜测，因此直接 INSUFFICIENT_DATA。）
_REPRO_MINIMUM = ("imageDigest", "dependencyLockHash", "seed", "codeHash", "outputHash")


def reproducibility_missing(repro: dict[str, Any]) -> list[str]:
    if not isinstance(repro, dict):
        return list(_REPRO_MINIMUM)
    return [k for k in _REPRO_MINIMUM if not repro.get(k)]


# --------------------------------------------------------------------------
# 复现对比
# --------------------------------------------------------------------------


def compare_with_original(
    original: dict[str, Any],
    replica: dict[str, Any],
    *,
    metrics_orig: dict[str, Any] | None = None,
    metrics_replica: dict[str, Any] | None = None,
    tolerance: float = 0.0,
) -> dict[str, Any]:
    """同 manifest 复现后的**诚实**判定。缺数据 ⇒ INSUFFICIENT_DATA，不猜。"""
    miss_o = reproducibility_missing(original)
    miss_r = reproducibility_missing(replica)
    if miss_o or miss_r:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "outputHashMatch": None,
            "seedMatch": None,
            "codeHashMatch": None,
            "imageDigestMatch": None,
            "metricDeltas": [],
            "tolerance": tolerance,
            "missing": {"original": miss_o, "replica": miss_r},
        }

    def _pair(key: str) -> bool:
        return bool(original.get(key)) and original.get(key) == replica.get(key)

    output_match = _pair("outputHash")
    seed_match = _pair("seed")
    code_match = _pair("codeHash")
    image_match = _pair("imageDigest")

    deltas: list[dict[str, Any]] = []
    mo = metrics_orig or {}
    mr = metrics_replica or {}
    for key in sorted(set(mo) & set(mr)):
        a, b = mo[key], mr[key]
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
            delta = abs(float(a) - float(b))
            deltas.append(
                {
                    "key": key,
                    "original": a,
                    "replica": b,
                    "delta": delta,
                    "withinTolerance": delta <= float(tolerance),
                }
            )
        elif a != b:
            deltas.append({"key": key, "original": a, "replica": b, "delta": None, "withinTolerance": False})

    if output_match and seed_match and code_match and image_match and all(
        d["delta"] == 0 for d in deltas
    ):
        verdict = "EXACT_MATCH"
    elif all(d["withinTolerance"] for d in deltas) and seed_match and code_match and image_match:
        verdict = "METRICS_TOLERANCE"
    else:
        verdict = "MISMATCH"
    return {
        "verdict": verdict,
        "outputHashMatch": output_match,
        "seedMatch": seed_match,
        "codeHashMatch": code_match,
        "imageDigestMatch": image_match,
        "metricDeltas": deltas,
        "tolerance": tolerance,
    }


# --------------------------------------------------------------------------
# exec_status → 真实结果
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RunOutcome:
    run_status: str
    error_code: str
    retryable: bool


_STATUS_MAP: dict[str, RunOutcome] = {
    "pass": RunOutcome("SUCCEEDED", "", False),
    "fail": RunOutcome("FAILED", "SANDBOX_FAILED", False),
    "timeout": RunOutcome("FAILED", "SANDBOX_TIMEOUT", False),
    "refused": RunOutcome("FAILED", "SANDBOX_REFUSED", False),
    "error": RunOutcome("FAILED", "PROVIDER_ERROR", True),
}


def exec_status_to_outcome(
    exec_status: str,
    *,
    guard_violation: dict[str, Any] | None = None,
    cancelled: bool = False,
) -> RunOutcome:
    """sandbox exec_status → 对外状态。取消优先于一切成功语义。"""
    if cancelled:
        return RunOutcome("CANCELLED", "CANCELLED", False)
    if guard_violation:
        return RunOutcome("FAILED", "SANDBOX_GUARD_VIOLATION", False)
    outcome = _STATUS_MAP.get(exec_status)
    if outcome is None:
        return RunOutcome("FAILED", "SANDBOX_UNKNOWN_STATUS", False)
    return outcome


__all__ = [
    "ENV_ALLOWLIST",
    "LIMIT_CEILINGS",
    "LimitCeilings",
    "NETWORK_POLICY",
    "ExperimentPolicyRejected",
    "PolicyDecision",
    "RunOutcome",
    "apply_policy_gate",
    "build_repro_record",
    "compare_with_original",
    "exec_status_to_outcome",
    "reproducibility_missing",
]
