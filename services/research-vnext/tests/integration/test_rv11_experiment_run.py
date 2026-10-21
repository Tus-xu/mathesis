"""RV11 · ExperimentRun 真实链路集成测试（真实 PostgreSQL + 真实 HTTP）。

链路：experiment_plan 提案（stub LLM）→ 人工确认 → dispatch（策略门/绑定）
    → ExperimentRunRow + JobRow（RV03 队列/租约/围栏）→ cancel / reproduce
    → worker 结果落库（exit 0 ≠ claim）。

真实容器执行属 `tests/real_sandbox/`（Docker 标记）与本轮 E2E；本文件不跑 Docker。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta

import pytest

# 本文件会留下多个 QUEUED job（背压按 schema 全局计数）——放宽 provider/user 配额，
# 否则后续用例会被先前用例的在队 job 顶到 429（行为本身是对的，测试需要隔离度）。
os.environ.setdefault("RESEARCH_MAX_JOBS_PER_PROVIDER", "200")
os.environ.setdefault("RESEARCH_MAX_JOBS_PER_USER", "200")

from research_vnext.core.db.engine import session_scope
from research_vnext.core.db.models import (
    ExperimentRunRow,
    JobRow,
    ResearchBlueprintRow,
)
from tests.integration.conftest import auth_headers, create_session
from tests.integration.test_rv09_intake_to_maps import (  # noqa: F401
    _accept,
    _pending,
    _send,
    sess,
    stub_llm,
)

BASE = "/api/research-v1"

PLAN_CODE = (
    "import json\n"
    "import random\n"
    "random.seed(SEED)\n"
    'n = int(PARAMS.get("samples", 1000))\n'
    "inside = sum(1 for _ in range(n) if random.random() ** 2 + random.random() ** 2 <= 1.0)\n"
    'print("RESULT_JSON:", json.dumps({"metrics": {"pi_estimate": 4 * inside / n}}))\n'
)

PLAN_PAYLOAD = {
    "objective": "蒙特卡洛估计 π（确定性种子）",
    "code_ref": "inline:monte-carlo-pi",
    "code": PLAN_CODE,
    "dataset_ref": "inline:unit-square",
    "environment": {},
    "params": {"samples": 1000},
    "metrics": ["pi_estimate"],
    "seed": 42,
    "stages": [{"name": "execute", "text": "运行采样"}],
}


def _proposal(kind: str, payload: dict, *, text: str = "实验计划已生成") -> str:
    return json.dumps({"kind": kind, "text": text, "payload": payload}, ensure_ascii=False)


def _plan_proposal_pending(client, s, stub_llm, payload):  # noqa: ANN001
    stub_llm(_proposal("experiment_plan", payload))
    _send(client, s, "请把上面的路线变成可执行实验计划")
    items = _pending(client, s)
    return next(p for p in items if p.get("payload", {}).get("proposal_kind") == "experiment_plan")


@pytest.fixture()
def plan_ready(client, sess, stub_llm):  # noqa: F811
    """确认一个最小 experiment_plan（含 inline 代码）。返回 confirmed_object。"""

    def _confirm(payload=PLAN_PAYLOAD):
        target = _plan_proposal_pending(client, sess, stub_llm, payload)
        status, body = _accept(client, sess, target["id"])
        assert status == 200, body
        return body["confirmed_object"]

    return _confirm


def _seed_blueprint(s) -> uuid.UUID:
    with session_scope() as db:
        row = ResearchBlueprintRow(
            id=uuid.uuid4(),
            session_id=s["session_id"],
            tenant_id=s["tenant_id"],
            revision=1,
            content={"routeId": "r1", "objective": "π 估计"},
            content_hash="0" * 64,
            confirmed_at=None,
            confirmed_by=s["actor_id"],
            trace_id="0" * 32,
            created_at=None,
        )
        db.add(row)
        db.flush()
        return row.id


def _dispatch(client, s, *, body=None):
    return client.post(
        f"{BASE}/sessions/{s['session_id']}/experiment-runs:dispatch",
        json=body or {},
        headers=auth_headers(s["ctx"]),
    )


def _run_detail(client, s, run_id):
    return client.get(
        f"{BASE}/sessions/{s['session_id']}/experiment-runs/{run_id}",
        headers=auth_headers(s["ctx"]),
    )


def _cancel(client, s, run_id):
    return client.post(
        f"{BASE}/sessions/{s['session_id']}/experiment-runs/{run_id}:cancel",
        json={"reason": "test"},
        headers=auth_headers(s["ctx"]),
    )


def _reproduce(client, s, run_id):
    return client.post(
        f"{BASE}/sessions/{s['session_id']}/experiment-runs/{run_id}:reproduce",
        json={},
        headers=auth_headers(s["ctx"]),
    )


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


@pytest.mark.usefixtures("app_env")
class TestDispatch:
    def test_dispatch_without_plan_rejected(self, client, sess):
        _seed_blueprint(sess)
        r = _dispatch(client, sess)
        assert r.status_code == 404, r.text
        assert r.json()["error"]["code"] == "PLAN_NOT_FOUND"

    def test_confirm_then_dispatch_creates_run_and_job(self, client, sess, plan_ready):
        plan = plan_ready()
        _seed_blueprint(sess)
        r = _dispatch(client, sess)
        assert r.status_code == 200, r.text
        body = r.json()["run"]
        assert body["status"] == "QUEUED"
        assert body["executor"] == "sandbox"
        assert body["planId"] == plan["id"]
        repro = body["repro"]
        assert repro["codeSha256"] and len(repro["codeSha256"]) == 64
        assert repro["blueprint"]["blueprintId"]
        assert repro["effectiveLimits"]["network"] == "deny"
        assert repro["envAllowlist"] == ["PYTHONHASHSEED"]
        with session_scope() as db:
            run = db.get(ExperimentRunRow, uuid.UUID(body["id"]))
            assert run is not None
            job = db.query(JobRow).filter(JobRow.input_payload["run_id"].astext == body["id"]).one()
            assert job.job_type == "experiment.run"
            assert job.status == "QUEUED"

    def test_limit_escalation_rejected(self, client, sess, plan_ready):
        plan_ready()
        _seed_blueprint(sess)
        r = _dispatch(client, sess, body={"limits": {"memoryLimitMb": 999999}})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "LIMIT_ESCALATION_REJECTED"

    def test_network_escalation_rejected(self, client, sess, plan_ready):
        plan_ready()
        _seed_blueprint(sess)
        r = _dispatch(client, sess, body={"network": "allow"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "NETWORK_ESCALATION_REJECTED"

    def test_plan_without_code_rejected_at_dispatch(self, client, sess, plan_ready, stub_llm):  # noqa: F811
        plan_ready()
        payload = dict(PLAN_PAYLOAD)
        payload.pop("code")
        target = _plan_proposal_pending(client, sess, stub_llm, payload)
        status, body = _accept(client, sess, target["id"])
        assert status == 200, body
        _seed_blueprint(sess)
        r = _dispatch(client, sess)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "PLAN_NO_CODE"


# --------------------------------------------------------------------------
# cancel / reproduce / 查询
# --------------------------------------------------------------------------


@pytest.mark.usefixtures("app_env")
class TestLifecycle:
    def test_cancel_queued_run(self, client, sess, plan_ready):
        plan_ready()
        _seed_blueprint(sess)
        body = _dispatch(client, sess).json()["run"]
        r = _cancel(client, sess, body["id"])
        assert r.status_code == 200, r.text
        assert r.json()["run"]["status"] == "CANCELLED"
        detail = _run_detail(client, sess, body["id"]).json()["run"]
        assert detail["status"] == "CANCELLED"
        assert detail["job"]["status"] == "CANCELLED"

    def test_cancel_terminal_run_rejected(self, client, sess, plan_ready):
        plan_ready()
        _seed_blueprint(sess)
        body = _dispatch(client, sess).json()["run"]
        assert _cancel(client, sess, body["id"]).status_code == 200
        r = _cancel(client, sess, body["id"])
        assert r.status_code == 409

    def test_reproduce_requires_terminal_run(self, client, sess, plan_ready):
        plan_ready()
        _seed_blueprint(sess)
        body = _dispatch(client, sess).json()["run"]
        r = _reproduce(client, sess, body["id"])
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "RUN_NOT_TERMINAL"

    def test_reproduce_references_same_manifest(self, client, sess, plan_ready):
        plan_ready()
        _seed_blueprint(sess)
        first = _dispatch(client, sess).json()["run"]
        _cancel(client, sess, first["id"])  # 终态化；复现引用同一 immutable dispatch 记录
        r = _reproduce(client, sess, first["id"])
        assert r.status_code == 200, r.text
        second = r.json()["run"]
        assert second["id"] != first["id"]
        assert second["status"] == "QUEUED"
        assert second["repro"]["reproduceOf"] == first["id"]
        with session_scope() as db:
            jobs = (
                db.query(JobRow)
                .filter(
                    JobRow.job_type == "experiment.run",
                    JobRow.session_id == sess["session_id"],
                )
                .order_by(JobRow.created_at)
                .all()
            )
            first_payload = jobs[0].input_payload
            second_payload = jobs[-1].input_payload
            # 事件瘦身：代码/输入/哈希不进队列 payload；两次 dispatch 引用同一 plan 与限额
            assert first_payload["plan_id"] == second_payload["plan_id"]
            assert first_payload["seed"] == second_payload["seed"]
            assert first_payload["limits"] == second_payload["limits"]
            assert "code" not in first_payload
            assert second_payload["reproduce_of"] == first["id"]

    def test_detail_and_list_expose_progress_and_manifest(self, client, sess, plan_ready):
        plan_ready()
        _seed_blueprint(sess)
        body = _dispatch(client, sess).json()["run"]
        detail = _run_detail(client, sess, body["id"]).json()["run"]
        assert detail["job"]["progress"] == 0
        assert detail["repro"]["blueprint"]["revision"] == 1
        listing = client.get(
            f"{BASE}/sessions/{sess['session_id']}/experiment-runs",
            headers=auth_headers(sess["ctx"]),
        )
        assert listing.status_code == 200
        assert any(item["id"] == body["id"] for item in listing.json()["items"])

    def test_cross_tenant_read_denied(self, client, sess, plan_ready, ctx_factory):
        plan_ready()
        _seed_blueprint(sess)
        body = _dispatch(client, sess).json()["run"]
        other_ctx = ctx_factory(tenant_id=uuid.uuid4())
        r = client.get(
            f"{BASE}/sessions/{sess['session_id']}/experiment-runs/{body['id']}",
            headers=auth_headers(other_ctx),
        )
        assert r.status_code in (401, 403)


# --------------------------------------------------------------------------
# worker 结果落库（真实 job_service 语义，无 Docker；走真实 adapter 组合）
# --------------------------------------------------------------------------


def _result(run_id: str, *, exec_status="pass", exit_code=0, stdout="", stderr=""):
    """sandbox manifest dict → research ExecutionResult（经真实 result_from_sandbox）。"""
    from research_vnext.core.execution.sandbox_adapter import result_from_sandbox

    manifest = {
        "run_id": f"rv11-{run_id}-1",
        "code_hash": "a" * 64,
        "input_hash": "b" * 64,
        "output_hash": "c" * 64,
        "artifact_hash": "d" * 64,
        "dependency_lock_hash": "e" * 64,
        "image": "math-arena-sandbox:0.2.0",
        "image_digest": "sha256:" + "f" * 64,
        "seed": 42,
        "exec_status": exec_status,
        "exit_code": exit_code,
        "duration_ms": 1000,
        "backend": "container",
        "isolation_mode": "container",
        "isolation_controls_enforced": {"network_isolation": True},
        "unenforced_reasons": {"tmpfs_workdir": "PARTIAL by design"},
        "effective_limits": {"cpu_limit": 1.0},
        "warnings": [],
        "truncation": {"truncated": False},
        "sandbox_version": "0.2.0",
        "platform": "linux (container)",
        "runtime_facts": {},
    }
    return result_from_sandbox(
        job_id="job-1",
        attempt=1,
        manifest=manifest,
        stdout=stdout,
        stderr=stderr,
        argv=["docker", "run"],
        requested_limits={"cpuLimit": 1.0},
        env_allowlist=["PYTHONHASHSEED"],
    )


@pytest.mark.usefixtures("app_env")
class TestWorkerOutcome:
    def _claimed(self, client, s, plan_ready):
        plan_ready()
        _seed_blueprint(s)
        _resp = _dispatch(client, s)
        assert _resp.status_code == 200, _resp.text
        body = _resp.json()["run"]
        from research_vnext.core.application import job_service as js
        from research_vnext.core.config import consumer_name

        with session_scope() as db:
            job = db.query(JobRow).filter(JobRow.input_payload["run_id"].astext == body["id"]).one()
            claimed = js.claim_job(db, job_id=job.id, worker=consumer_name())
            return body["id"], job.id, int(claimed.fencing_token), dict(claimed.input_payload or {})

    def test_exit_zero_does_not_set_claim_holds(self, client, sess, plan_ready):
        run_id, _job, _fence, _p = self._claimed(client, sess, plan_ready)
        from research_vnext.core.application import experiment_run_service as svc

        res = _result(run_id, stdout='RESULT_JSON: {"metrics": {"pi_estimate": 3.14}}')
        svc.complete_run(run_id=uuid.UUID(run_id), result=res, cancelled=False)
        with session_scope() as db:
            run = db.get(ExperimentRunRow, uuid.UUID(run_id))
            assert run.status == "SUCCEEDED"
            assert run.exit_code == 0
            assert run.claim_holds is None  # exit 0 ≠ 结论成立
            assert run.metrics.get("pi_estimate") == pytest.approx(3.14)
            assert run.repro["imageDigest"].startswith("sha256:")
            assert run.repro["outputHash"] == "c" * 64
            assert run.result.get("artifacts"), "应物化 metrics artifact"
            assert run.logs_ref, "stdout 日志应落盘并可引用"

    def test_sandbox_fail_is_real_failed(self, client, sess, plan_ready):
        run_id, _job, _fence, _p = self._claimed(client, sess, plan_ready)
        from research_vnext.core.application import experiment_run_service as svc

        res = _result(run_id, exec_status="fail", exit_code=1, stderr="Traceback...")
        svc.complete_run(run_id=uuid.UUID(run_id), result=res, cancelled=False)
        with session_scope() as db:
            run = db.get(ExperimentRunRow, uuid.UUID(run_id))
            assert run.status == "FAILED"
            assert run.claim_holds is None

    def test_guard_violation_recorded_and_permanent(self, client, sess, plan_ready):
        run_id, _job, _fence, _p = self._claimed(client, sess, plan_ready)
        from research_vnext.core.application import experiment_run_service as svc

        res = _result(
            run_id,
            exec_status="fail",
            exit_code=3,
            stderr="GUARD_VIOLATION[BLOCKED_IMPORT]: module 'socket' is blocked",
        )
        svc.complete_run(run_id=uuid.UUID(run_id), result=res, cancelled=False)
        with session_scope() as db:
            run = db.get(ExperimentRunRow, uuid.UUID(run_id))
            assert run.status == "FAILED"
            assert run.not_reproducible_reason.startswith("GUARD_VIOLATION")
            assert (run.result or {}).get("errorCode") == "SANDBOX_GUARD_VIOLATION"

    def test_reproduce_comparison_recorded_honestly(self, client, sess, plan_ready):
        from research_vnext.core.application import experiment_run_service as svc

        plan_ready()
        _seed_blueprint(sess)
        _resp = _dispatch(client, sess)
        assert _resp.status_code == 200, _resp.text
        first = _resp.json()["run"]
        res = _result(first["id"], stdout='RESULT_JSON: {"metrics": {"pi_estimate": 3.14159}}')
        svc.complete_run(run_id=uuid.UUID(first["id"]), result=res, cancelled=False)
        # 一键复现 → 新 run → 完成时自动与原 run 对比
        second = _reproduce(client, sess, first["id"]).json()["run"]
        res2 = _result(second["id"], stdout='RESULT_JSON: {"metrics": {"pi_estimate": 3.14159}}')
        summary = svc.complete_run(run_id=uuid.UUID(second["id"]), result=res2, cancelled=False)
        comparison = summary["comparison"]
        assert comparison is not None
        assert comparison["verdict"] in ("EXACT_MATCH", "METRICS_TOLERANCE")
        assert comparison["outputHashMatch"] is True

    def test_stale_fencing_rejected_for_experiment_job(self, client, sess, plan_ready):
        run_id, job_id, old_fence, _p = self._claimed(client, sess, plan_ready)
        from research_vnext.core.application import job_service as js

        # 租约过期 → 新 worker 接管 → fence 递增
        with session_scope() as db:
            job = db.get(JobRow, job_id)
            job.lease_expires_at = js._now() - timedelta(seconds=1)
            db.flush()
            reclaimed = js.claim_job(db, job_id=job_id, worker="worker-b")
            assert int(reclaimed.fencing_token) == old_fence + 1
        # 旧 worker 迟到上报 → 必须被拒
        with session_scope() as db:
            with pytest.raises(js.StaleFencingToken):
                js.complete_job(db, job_id=job_id, fencing_token=old_fence, result={"fake": True})
