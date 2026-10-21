"""RV11 · ExperimentRun 应用服务 —— dispatch / cancel / reproduce / 结果落库。

**不变量**：
1. 发 job 的**唯一**前提：experiment_plan 已经人工确认落为不可变修订行
   （`ExperimentPlanRow`），且会话里存在已确认的 ResearchBlueprint 作为溯源绑定。
   提案（proposal）阶段绝不创建 job（RV10 已钉住 `ExperimentRunRow=0`）。
2. 资源/网络/env 由 `domain.experiment_run.apply_policy_gate` 裁决——
   超限请求是**提权**，拒绝而非钳制；模型不能提高资源或打开网络。
3. `experiment_run` 表里 `claim_holds` 永远不在这里被写成 True/False——
   它属于 RV12 验证域；exit 0 与"结论成立"是两回事。
4. 代码与输入不进队列 payload（事件瘦身 + 防脱敏层误伤代码）：
   job 只携带 `run_id`/`plan_id`/seed/limits；worker 按 run 的 dispatch 记录
   回读不可变的 plan 行，并校验 `sha256(code) == codeSha256`。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..domain.errors import DomainError
from ..domain.experiment_run import (
    ENV_ALLOWLIST,
    NETWORK_POLICY,
    ExperimentPolicyRejected,
    apply_policy_gate,
    build_repro_record,  # noqa: F401  (对外再导出：worker/api 共用同一记录构造器)
    compare_with_original,
)
from ..db import models as M
from ..observability.redaction import redact_mapping

#: 复现对比的默认数值容差（可经 env 覆盖；如实记录在对比结果里）。
DEFAULT_REPRO_TOLERANCE = 1e-9

_TERMINAL_RUN_STATUSES = ("SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED")


# --------------------------------------------------------------------------
# 领域错误（HTTP 映射）
# --------------------------------------------------------------------------


class RunNotFound(DomainError):
    code = "RUN_NOT_FOUND"
    http_status = 404


class PlanNotFound(DomainError):
    code = "PLAN_NOT_FOUND"
    http_status = 404


class PlanNoCode(DomainError):
    code = "PLAN_NO_CODE"
    http_status = 400


class BlueprintNotFound(DomainError):
    code = "BLUEPRINT_NOT_FOUND"
    http_status = 409


class RunNotTerminal(DomainError):
    code = "RUN_NOT_TERMINAL"
    http_status = 409


class RunAlreadyTerminal(DomainError):
    code = "RUN_ALREADY_TERMINAL"
    http_status = 409


class PolicyGateError(DomainError):
    """策略门拒绝（400）。code 透传 `ExperimentPolicyRejected.code`。"""

    http_status = 400

    def __init__(self, rejected: ExperimentPolicyRejected) -> None:
        super().__init__(rejected.message)
        self.code = rejected.code
        self.message = rejected.message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


def _latest_plan(db: Session, session_id: uuid.UUID, revision: int | None) -> M.ExperimentPlanRow:
    query = db.query(M.ExperimentPlanRow).filter(M.ExperimentPlanRow.session_id == session_id)
    row = (
        query.filter(M.ExperimentPlanRow.revision == revision).first()
        if revision is not None
        else query.order_by(M.ExperimentPlanRow.revision.desc()).first()
    )
    if row is None:
        raise PlanNotFound(f"本会话没有已确认的实验计划（revision={revision}）")
    return row


def _latest_blueprint(db: Session, session_id: uuid.UUID) -> M.ResearchBlueprintRow:
    row = (
        db.query(M.ResearchBlueprintRow)
        .filter(M.ResearchBlueprintRow.session_id == session_id)
        .order_by(M.ResearchBlueprintRow.revision.desc())
        .first()
    )
    if row is None:
        raise BlueprintNotFound("本会话没有已确认的 ResearchBlueprint——实验必须绑定一个被确认的路线")
    return row


def dispatch(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    plan_revision: int | None = None,
    requested_limits: dict[str, Any] | None = None,
    network: str | None = None,
    environment: dict[str, str] | None = None,
    client_request_id: str = "",
    trace_id: str = "",
) -> dict[str, Any]:
    """人工确认后的计划 → 一次新的 ExperimentRun + RV03 job（同事务）。"""
    from ..config import job_stage_timeout_seconds
    from ..application import job_service as js
    from ..execution.sandbox_adapter import pinned_image

    plan = _latest_plan(db, session_id, plan_revision)
    blueprint = _latest_blueprint(db, session_id)
    content = dict(plan.content or {})

    code = str(content.get("code") or "").strip()
    if not code:
        raise PlanNoCode("计划没有可执行代码（code 为空）——不会为空计划发 job")
    # 完整性哈希按**原文**（不 strip）：worker 侧回读 plan 行的也是原文，
    # 两侧口径必须逐字节一致（strip 与原文差一个尾换行即触发完整性失败）。
    code_sha256 = _sha256(str(content.get("code") or ""))

    raw_inputs = [x for x in (content.get("inputs") or []) if isinstance(x, dict) and x.get("name")]
    inputs_payload = [
        {"name": str(x["name"]), "text": str(x.get("text") or "")} for x in raw_inputs
    ]
    input_hashes = {item["name"]: _sha256(item["text"]) for item in inputs_payload}

    seed_raw = content.get("seed")
    seed = int(seed_raw) if isinstance(seed_raw, (int, float)) and seed_raw >= 0 else 0

    env_requested = environment if environment is not None else (
        content.get("environment") if isinstance(content.get("environment"), dict) else {}
    )
    try:
        decision = apply_policy_gate(
            requested_limits,
            network=network,
            environment=env_requested,
            stage_timeout_seconds=job_stage_timeout_seconds(),
        )
    except ExperimentPolicyRejected as e:
        raise PolicyGateError(e) from e

    run_id = uuid.uuid4()
    dispatch_record: dict[str, Any] = {
        "requestedLimits": dict(requested_limits or {}),
        "effectiveLimits": decision.effective,
        "gateFacts": decision.facts,
        "network": NETWORK_POLICY,
        "envAllowlist": sorted(ENV_ALLOWLIST),
        "environment": dict(env_requested or {}),
        "codeSha256": code_sha256,
        "inputHashes": input_hashes,
        "seed": seed,
        "plan": {
            "planId": str(plan.id),
            "revision": int(plan.revision),
            "contentHash": plan.content_hash or "",
        },
        "blueprint": {
            "blueprintId": str(blueprint.id),
            "revision": int(blueprint.revision),
            "contentHash": blueprint.content_hash or "",
        },
        "image": pinned_image(),
        "reproduceOf": None,
        "dispatchedAt": _now().isoformat(),
    }

    run = M.ExperimentRunRow(
        id=run_id,
        session_id=session_id,
        tenant_id=tenant_id,
        plan_id=plan.id,
        status="QUEUED",
        executor="sandbox",
        code_ref=str(content.get("code_ref") or content.get("codeRef") or "")[:512],
        dataset_ref=str(content.get("dataset_ref") or content.get("datasetRef") or "")[:512],
        environment=dict(env_requested or {}),
        params=dict(content.get("params") or {}),
        result={"jobId": None},  # flush 后回填
        metrics={},
        logs_ref="",
        figures=[],
        exit_code=None,
        claim_holds=None,  # 未判定（不是 True）
        not_reproducible_reason="",
        # 说明：dispatch 记录**不做** redaction —— 它只含服务端生成的哈希/ID/数值
        # （无用户 PII、无密钥）；套用通用脱敏会把 ≥32 位 hex 误标 [REDACTED_HEX]
        # （RV10 FINAL-GATE L-1 的共享层已知缺陷），使哈希链失去可对账性。
        repro=dispatch_record,
        client_request_id=client_request_id,
        trace_id=trace_id,
        created_at=_now(),
    )
    db.add(run)
    db.flush()

    # 事件瘦身：代码/输入**不进**队列 payload；worker 按 run_id 回读不可变 plan。
    job_payload = {
        "run_id": str(run_id),
        "plan_id": str(plan.id),
        "seed": seed,
        "limits": decision.effective,
        "inputHashes": input_hashes,
        "reproduce_of": None,
    }
    job = js.enqueue(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        job_type="experiment.run",
        input_payload=job_payload,
        input_ref=f"experiment_run:{run_id}",
        user_id=actor_id,
        provider="sandbox",
        client_request_id=client_request_id,
        trace_id=trace_id,
    )
    run.result = {"jobId": str(job.id)}
    db.flush()
    return view(run, job)


def dispatch_reproduce(
    db: Session,
    *,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    actor_id: uuid.UUID,
    client_request_id: str = "",
    trace_id: str = "",
) -> dict[str, Any]:
    """一键复现：**新** ExperimentRun，引用同一不可变 dispatch 记录（同一 plan 行）。"""
    from ..application import job_service as js
    from ..execution.sandbox_adapter import pinned_image

    original = db.get(M.ExperimentRunRow, run_id)
    if original is None or original.session_id != session_id:
        raise RunNotFound(f"实验运行不存在: {run_id}")
    if original.status not in _TERMINAL_RUN_STATUSES:
        raise RunNotTerminal(f"原运行仍在 {original.status}——只有终态运行可以复现")

    plan = db.get(M.ExperimentPlanRow, original.plan_id) if original.plan_id else None
    if plan is None:
        raise PlanNotFound("原运行引用的实验计划已不可解析——无法复现")
    content = dict(plan.content or {})
    code = str(content.get("code") or "").strip()
    if not code:
        raise PlanNoCode("原计划没有可执行代码——无法复现")

    prior_repro = dict(original.repro or {})
    effective = dict(prior_repro.get("effectiveLimits") or {})
    seed = int(prior_repro.get("seed") or 0)
    code_sha = str(prior_repro.get("codeSha256") or "")
    # 与 dispatch 同口径：按 plan.content 的**原文**哈希（strip 会差尾换行）
    if not code_sha or _sha256(str(content.get("code") or "")) != code_sha:
        # 不可变 plan 行被意外改动的防御性校验（行本不该变；变了就诚实失败）
        raise PlanNoCode("原计划的代码哈希与 dispatch 记录不一致——拒绝复现（完整性校验失败）")

    run_id_new = uuid.uuid4()
    repro = dict(prior_repro)
    repro.update(
        {
            "reproduceOf": str(original.id),
            "originalOutputHash": str(prior_repro.get("outputHash") or ""),
            "image": str(prior_repro.get("image") or pinned_image()),
            "dispatchedAt": _now().isoformat(),
        }
    )
    run = M.ExperimentRunRow(
        id=run_id_new,
        session_id=session_id,
        tenant_id=original.tenant_id,
        plan_id=original.plan_id,
        status="QUEUED",
        executor="sandbox",
        code_ref=original.code_ref,
        dataset_ref=original.dataset_ref,
        environment=dict(original.environment or {}),
        params=dict(original.params or {}),
        result={"jobId": None},
        metrics={},
        logs_ref="",
        figures=[],
        exit_code=None,
        claim_holds=None,
        not_reproducible_reason="",
        repro=repro,
        client_request_id=client_request_id,
        trace_id=trace_id or original.trace_id,
        created_at=_now(),
    )
    db.add(run)
    db.flush()

    job_payload = {
        "run_id": str(run_id_new),
        "plan_id": str(original.plan_id),
        "seed": seed,
        "limits": effective,
        "reproduce_of": str(original.id),
    }
    job = js.enqueue(
        db,
        session_id=session_id,
        tenant_id=original.tenant_id,
        job_type="experiment.run",
        input_payload=job_payload,
        input_ref=f"experiment_run:{run_id_new}",
        user_id=actor_id,
        provider="sandbox",
        client_request_id=client_request_id,
        trace_id=trace_id or original.trace_id,
    )
    run.result = {"jobId": str(job.id)}
    db.flush()
    return view(run, job)


# --------------------------------------------------------------------------
# cancel
# --------------------------------------------------------------------------


def cancel(
    db: Session,
    *,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    reason: str,
    actor_id: uuid.UUID,
    trace_id: str = "",
) -> dict[str, Any]:
    """请求取消：QUEUED 立即 CANCELLED；RUNNING 落取消请求，worker 安全点生效。"""
    from ..application import job_service as js

    run = db.get(M.ExperimentRunRow, run_id)
    if run is None or run.session_id != session_id:
        raise RunNotFound(f"实验运行不存在: {run_id}")
    if run.status in _TERMINAL_RUN_STATUSES:
        raise RunAlreadyTerminal(f"运行已处于终态 {run.status}，无法取消")

    job = _job_for_run(db, run_id)
    if job is not None:
        js.request_cancel(db, job_id=job.id, reason=reason or "cancel_requested", trace_id=trace_id)
        job_state = js.get_job(db, job.id)
    else:
        job_state = None

    # job 已立即终态（QUEUED/RETRYING 路径）⇒ run 同步终态；
    # RUNNING 路径保持原状，等 worker 在安全点收尾（complete_run cancelled=True）。
    if job_state is not None and job_state.status == "CANCELLED":
        run.status = "CANCELLED"
        run.not_reproducible_reason = ""  # 取消不是不可复现
        db.flush()
    return view(run, job_state)


def _job_for_run(db: Session, run_id: uuid.UUID) -> M.JobRow | None:
    rows = (
        db.query(M.JobRow)
        .filter(M.JobRow.input_payload["run_id"].astext == str(run_id))
        .order_by(M.JobRow.created_at.desc())
        .all()
    )
    return rows[0] if rows else None


# --------------------------------------------------------------------------
# worker 侧：执行材料回读（DB 是唯一事实权威）
# --------------------------------------------------------------------------


def execution_material(db: Session, *, run_id: uuid.UUID) -> dict[str, Any]:
    """按 run 的 dispatch 记录回读不可变 plan 行，并做完整性校验。

    worker 不信任队列 payload 里的任何代码——代码只存在于已确认的 plan 行；
    `sha256(code) == codeSha256`（dispatch 时记录）不匹配 ⇒ 拒绝执行。
    """
    run = db.get(M.ExperimentRunRow, run_id)
    if run is None:
        raise RunNotFound(f"实验运行不存在: {run_id}")
    repro = dict(run.repro or {})
    plan = db.get(M.ExperimentPlanRow, run.plan_id) if run.plan_id else None
    if plan is None:
        raise PlanNotFound(f"运行引用的实验计划不可解析: {run.plan_id}")
    content = dict(plan.content or {})
    code = str(content.get("code") or "")
    code_sha = str(repro.get("codeSha256") or "")
    integrity_ok = bool(code) and bool(code_sha) and _sha256(code) == code_sha

    inputs = [
        {"name": str(x["name"]), "text": str(x.get("text") or "")}
        for x in (content.get("inputs") or [])
        if isinstance(x, dict) and x.get("name")
    ]
    recorded_hashes = dict(repro.get("inputHashes") or {})
    actual_hashes = {item["name"]: _sha256(item["text"]) for item in inputs}
    input_hashes_ok = recorded_hashes == actual_hashes

    return {
        "run_id": str(run.id),
        "plan_id": str(run.plan_id),
        "code_ref": run.code_ref,
        "dataset_ref": run.dataset_ref,
        "code": code,
        "inputs": inputs,
        "params": dict(run.params or {}),
        "seed": int(repro.get("seed") or 0),
        "limits": dict(repro.get("effectiveLimits") or {}),
        "environment": dict(repro.get("environment") or {}),
        "reproduce_of": repro.get("reproduceOf"),
        "integrity_ok": integrity_ok,
        "input_hashes_ok": input_hashes_ok,
    }


# --------------------------------------------------------------------------
# worker 结果落库
# --------------------------------------------------------------------------


def _artifact_root(run_id: str) -> Path:
    raw = (os.getenv("RESEARCH_RUN_ARTIFACT_ROOT") or "").strip()
    base = Path(raw) if raw else Path(__file__).resolve().parents[3] / ".runs"
    return base / run_id


def _materialize(run_id: str, artifacts_plan: list[dict[str, Any]], stdout: str, stderr: str) -> dict[str, Any]:
    """把 artifact 计划与日志写到 per-run 目录（worker 侧；沙箱内代码无文件写能力）。"""
    root = _artifact_root(run_id)
    written: list[dict[str, Any]] = []
    try:
        for item in artifacts_plan or []:
            rel = str(item.get("path") or "")
            if not rel or ".." in rel or rel.startswith("/"):
                continue  # path traversal 防御：只允许白名单相对路径
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # 必须按**字节**写：write_text 在 Windows 会把 \n 翻译成 \r\n，
            # 落盘字节数与 DB 记录的 sha256/sizeBytes 不再一致（实测踩过）。
            target.write_bytes(str(item.get("content") or "").encode("utf-8"))
            written.append(
                {
                    "path": rel,
                    "sizeBytes": int(item.get("sizeBytes") or 0),
                    "sha256": str(item.get("sha256") or ""),
                    "derivedFrom": "outputHash",
                }
            )
        logs_dir = root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "stdout.txt").write_bytes((stdout or "").encode("utf-8", "surrogateescape"))
        (logs_dir / "stderr.txt").write_bytes((stderr or "").encode("utf-8", "surrogateescape"))
    except OSError:
        # 证据文件写不进去时**不改变运行结论**，但如实登记缺口
        return {"artifacts": written, "logs_ref": "", "evidenceGap": "ARTIFACT_WRITE_FAILED"}

    return {"artifacts": written, "logs_ref": f".runs/{run_id}/logs/stdout.txt", "evidenceGap": ""}


def complete_run(
    *,
    run_id: uuid.UUID,
    result: Any,
    cancelled: bool = False,
) -> dict[str, Any]:
    """worker 执行完一次 sandbox 运行后把**真实结果**写回 run 行。

    `result` 是 research `ExecutionResult`（含完整 repro 记录与 stderr）。
    本函数是 run 行**唯一**的结果写入点；`claim_holds` 永不在此设置。
    """
    from ..domain.experiment_run import exec_status_to_outcome
    from ..execution.sandbox_adapter import detect_guard_violation

    with _session_scope() as db:
        run = db.get(M.ExperimentRunRow, run_id)
        if run is None:
            raise RunNotFound(f"实验运行不存在: {run_id}")

        repro_record = dict(result.repro or {})
        guard = detect_guard_violation(getattr(result, "stderr", "") or "")
        outcome = exec_status_to_outcome(
            str(repro_record.get("execStatus") or ""),
            guard_violation=guard,
            cancelled=cancelled,
        )

        stored = dict(run.repro or {})
        stored.update(repro_record)
        run.repro = stored  # dispatch 记录 + 完成 manifest 合并后写回

        materialized = _materialize(
            str(run_id),
            list((result.output or {}).get("artifactsPlan") or []),
            getattr(result, "stdout", "") or "",
            getattr(result, "stderr", "") or "",
        )

        run.status = outcome.run_status
        run.exit_code = int(result.exit_code) if result.exit_code is not None else None
        run.metrics = redact_mapping(dict(result.metrics or {}))
        run.figures = [
            {"path": f.get("path"), "sha256": f.get("sha256")} for f in (result.figures or [])
        ]
        run.logs_ref = str(materialized["logs_ref"] or "")

        if outcome.run_status == "FAILED" and not repro_record:
            run.not_reproducible_reason = "NO_MANIFEST"
        elif guard:
            run.not_reproducible_reason = f"GUARD_VIOLATION:{guard.get('code', '')}"[:512]

        # 一键复现的诚实对比：引用原 run 的完成态 manifest，差异如实记录。
        # （先算好再一次性赋值 —— JSONB 原位变更在部分 flush 路径下不触发 UPDATE，实测踩过。）
        comparison: dict[str, Any] | None = None
        if stored.get("reproduceOf"):
            original = db.get(M.ExperimentRunRow, uuid.UUID(str(stored["reproduceOf"])))
            if original is not None:
                tolerance = float(os.getenv("RESEARCH_REPRO_TOLERANCE", str(DEFAULT_REPRO_TOLERANCE)))
                comparison = compare_with_original(
                    dict(original.repro or {}),
                    stored,
                    metrics_orig=dict(original.metrics or {}),
                    metrics_replica=dict(run.metrics or {}),
                    tolerance=tolerance,
                )

        run.result = {
            **(run.result or {}),
            "artifacts": materialized["artifacts"],
            "evidenceGap": materialized["evidenceGap"],
            "errorCode": outcome.error_code,
            "exitCode": run.exit_code,
            # 关键：exit 0 ≠ 结论成立；这里只记录执行事实
            "claimHolds": None,
            "guardViolation": guard,
            "reproComparison": comparison,
        }

        db.flush()
        return {
            "run_id": str(run_id),
            "status": run.status,
            "error_code": outcome.error_code,
            "retryable": outcome.retryable,
            "exit_code": run.exit_code,
            "claim_holds": None,
            "comparison": (run.result or {}).get("reproComparison"),
        }


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------


def view(run: M.ExperimentRunRow, job: M.JobRow | None = None) -> dict[str, Any]:
    """对外投影。`claimHolds=None` 会被如实传给前端（不是 True）。"""
    from ..application.job_service import public_view as job_public_view

    result = dict(run.result or {})
    out: dict[str, Any] = {
        "id": str(run.id),
        "sessionId": str(run.session_id),
        "planId": str(run.plan_id) if run.plan_id else None,
        "status": run.status,
        "executor": run.executor,
        "codeRef": run.code_ref,
        "datasetRef": run.dataset_ref,
        "params": dict(run.params or {}),
        "metrics": dict(run.metrics or {}),
        "figures": list(run.figures or []),
        "logsRef": run.logs_ref,
        "exitCode": run.exit_code,
        "claimHolds": run.claim_holds,
        "notReproducibleReason": run.not_reproducible_reason,
        "repro": dict(run.repro or {}),
        "result": result,
        "artifacts": list(result.get("artifacts") or []),
        "reproComparison": result.get("reproComparison"),
        "traceId": run.trace_id,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
    }
    if job is not None:
        out["job"] = job_public_view(job)
    return out


def get_run(db: Session, *, session_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
    run = db.get(M.ExperimentRunRow, run_id)
    if run is None or run.session_id != session_id:
        raise RunNotFound(f"实验运行不存在: {run_id}")
    return view(run, _job_for_run(db, run_id))


def list_runs(db: Session, *, session_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        db.query(M.ExperimentRunRow)
        .filter(M.ExperimentRunRow.session_id == session_id)
        .order_by(M.ExperimentRunRow.created_at.desc())
        .limit(max(1, min(int(limit), 200)))
        .all()
    )
    return [view(r, _job_for_run(db, r.id)) for r in rows]


def _session_scope():
    from ..db.engine import session_scope

    return session_scope()


__all__ = [
    "BlueprintNotFound",
    "PlanNoCode",
    "PlanNotFound",
    "PolicyGateError",
    "RunAlreadyTerminal",
    "RunNotFound",
    "RunNotTerminal",
    "cancel",
    "complete_run",
    "dispatch",
    "dispatch_reproduce",
    "execution_material",
    "get_run",
    "list_runs",
    "view",
]
