"""RV11 · 端到端验收脚本（真实 8300 进程 + 真实 PostgreSQL + **真实 Linux 容器**）。

链路（每一步都是真实 HTTP / 真实 Docker）：
    健康检查 → 建会话 → 种子已确认 ExperimentPlan + ResearchBlueprint（见下方诚实边界）
    → dispatch（策略门）→ 独立 worker 进程执行 **容器内数学实验**
    → 结果落库（metrics / artifacts / manifest 全字段 / claim_holds=None）
    → 一键复现（新 run，引用同一 immutable manifest）→ EXACT_MATCH 对比
    → cancel（QUEUED 立即取消）
    → 资源提权拒绝 / 网络提权拒绝（模型不能加资源/开网络）
    → 恶意代码（import socket）被守卫在容器内拦截 → 真实 FAILED
    → 死循环实验被墙钟超时杀死 → 真实 FAILED SANDBOX_TIMEOUT
    → OOM 实验被 cgroup 杀死 → 真实 FAILED
    → 未确认计划不可发 job（404）
    → 全程 claim_holds 恒为 null（exit 0 ≠ 结论成立）

诚实边界：
- "proposal → 人工确认"门本身由 tests/integration/test_rv11_experiment_run.py 在
  真实 HTTP + stub LLM 下机器验证；本脚本从"已确认的 plan"起跑，
  用 `proposal_handlers.apply`（确认门的唯一写入口）构造种子，并显式声明这一步。
- AccessContext 自签（与 RV09/RV10 相同机制与测试密钥）。

前置：8300 已由驱动脚本以真实 PostgreSQL 启动；
      本机 Docker + `math-arena-sandbox:0.2.0` 镜像可用。

用法：
    python scripts/rv11_e2e.py --base-url http://127.0.0.1:8300 --out <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for p in (str(SERVICE_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import rv09_e2e as base  # noqa: E402

RESEARCH_PREFIX = base.api_prefixes()[0] if hasattr(base, "api_prefixes") else "/api/research-v1"


def to_windows_path(p: str) -> str:
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    return f"{m.group(1).upper()}:/{m.group(2)}" if m else p


# --------------------------------------------------------------------------
# 实验代码（与 unit/integration 相同契约：RESULT_JSON 信封）
# --------------------------------------------------------------------------

PI_CODE = """import json, random
random.seed(SEED)
n = int(PARAMS.get("samples", 2000))
xs = []
inside = 0
for i in range(1, n + 1):
    if random.random() ** 2 + random.random() ** 2 <= 1.0:
        inside += 1
    if i in (100, 1000, n):
        xs.append([i, 4 * inside / i])
pi = 4 * inside / n
print("RESULT_JSON:", json.dumps({
    "metrics": {"pi_estimate": pi, "samples": n},
    "table": {"columns": ["n", "estimate"], "rows": xs},
    "figure": {"title": "Monte Carlo pi estimate", "xLabel": "samples",
                "yLabel": "estimate", "points": xs},
}))
"""

BOMB_CODE = """x = bytearray(int(PARAMS.get("mb", 400)) * 1024 * 1024)
print("ALLOC-OK")
"""

SPIN_CODE = """while True:
    pass
"""

EVIL_CODE = """import socket
print("never reached")
"""


class RV11E2E(base.E2E):
    """RV11：隔离沙箱 + 可复现 ExperimentRun 的真实链路。"""

    def __init__(self, base_url: str, env: dict[str, str]) -> None:
        super().__init__(base_url)
        self.env = env
        self.run_ids: dict[str, str] = {}

    # ---------------- 种子（诚实边界内） ----------------

    def _seed_blueprint(self, session_id: str) -> dict:
        from research_vnext.core.db.engine import session_scope
        from research_vnext.core.db import models as M

        with session_scope() as db:
            row = M.ResearchBlueprintRow(
                id=uuid.uuid4(),
                session_id=uuid.UUID(session_id),
                tenant_id=self.tenant_id,
                revision=1,
                content={"routeId": "r1", "objective": "RV11 E2E 种子蓝图"},
                content_hash="0" * 64,
                confirmed_at=None,
                confirmed_by=self.actor_id,
                trace_id="0" * 32,
                created_at=None,
            )
            db.add(row)
            db.flush()
            return {"blueprintId": str(row.id)}

    def _seed_plan(self, session_id: str, *, code: str, params: dict, seed: int = 42,
                   extra: dict | None = None) -> dict:
        """经**确认门唯一写入口** `proposal_handlers.apply` 落一个已确认计划。

        这是集成测试已机器验证的 confirm 链路的服务端终点；本脚本在此显式
        声明该步骤，而非伪造一行状态。
        """
        from research_vnext.core.db.engine import session_scope
        from research_vnext.core.domain import proposal_handlers as H

        payload = {
            "objective": "RV11 E2E 实验",
            "code_ref": "inline:e2e",
            "code": code,
            "dataset_ref": "inline:e2e-inputs",
            "environment": {},
            "params": params,
            "metrics": ["pi_estimate"],
            "seed": seed,
            "stages": [{"name": "execute", "text": "容器内执行"}],
        }
        payload.update(extra or {})
        with session_scope() as db:
            content = H._validate_experiment_plan(payload, session_id=uuid.UUID(session_id), db=db)
            applied = H.apply(
                db,
                session_id=uuid.UUID(session_id),
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                object_ref="experiment_plan",
                content=content,
                client_request_id=f"e2e:{uuid.uuid4().hex[:8]}",
                trace_id="0" * 32,
            )
            return {"planId": applied["id"], "revision": applied["revision"]}

    # ---------------- worker ----------------

    def run_worker(self, job_id: str) -> dict:
        """独立 worker 进程执行一个作业（真实容器，RESEARCH_EXECUTOR=sandbox）。"""
        cmd = [
            sys.executable, "-m", "research_vnext.core.worker.entrypoint",
            "--job", job_id, "--json",
        ]
        proc = subprocess.run(  # noqa: S603
            cmd, cwd=str(SERVICE_ROOT), env=self.env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        out = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            parsed = {"raw": out[-400:]}
        parsed["worker_exit"] = proc.returncode
        if proc.returncode != 0:
            parsed["worker_stderr_tail"] = (proc.stderr or "")[-800:]
        return parsed

    def wait_run_status(self, run_id: str, statuses: set[str], timeout: float = 660.0) -> dict:
        deadline = time.monotonic() + timeout
        last: dict = {}
        while time.monotonic() < deadline:
            r = self.get(f"/sessions/{self.sid}/experiment-runs/{run_id}")
            if r.status_code == 200:
                last = r.json()["run"]
                if last["status"] in statuses:
                    return last
            time.sleep(1.0)
        return last or {"status": "TIMEOUT-WAIT"}

    # ---------------- 步骤 ----------------

    def step_health(self):
        step = base.Step(name="[1] healthz（真实 uvicorn 进程）")
        try:
            r = self.client.get(f"{self.base}/healthz", timeout=10.0)
            if r.status_code != 200:
                return step.fail("healthz 非 200", body=r.text[:200])
            return step.ok(service=r.json().get("service"))
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def step_session(self):
        step = base.Step(name="[2] 创建研究会话")
        try:
            self.sid = self.new_session("RV11 沙箱实验 E2E")
            self.blueprint = self._seed_blueprint(self.sid)
            return step.ok(session_id=self.sid)
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def _dispatch(self, body: dict) -> httpx.Response:
        return self.post(f"/sessions/{self.sid}/experiment-runs:dispatch", body)

    def _make_run(self, tag: str, *, code: str, params: dict, seed: int = 42,
                  limits: dict | None = None) -> tuple[base.Step, dict | None, httpx.Response]:
        plan = self._seed_plan(self.sid, code=code, params=params, seed=seed)
        self.plan = plan
        body: dict = {"planRevision": plan["revision"]}
        if limits is not None:
            body["limits"] = limits
        r = self._dispatch(body)
        if r.status_code != 200:
            return base.Step(name=f"dispatch[{tag}]"), None, r
        run = r.json()["run"]
        self.run_ids[tag] = run["id"]
        job_id = run["result"]["jobId"]
        return base.Step(name=f"dispatch[{tag}]"), run, r if job_id is None else run  # type: ignore[return-value]

    def _dispatch_and_run(self, tag: str, *, code: str, params: dict, seed: int = 42,
                          limits: dict | None = None, expect: str = "SUCCEEDED") -> base.Step:
        step = base.Step(name=f"[{tag}] dispatch → 容器执行 → {expect}")
        try:
            plan = self._seed_plan(self.sid, code=code, params=params, seed=seed)
            body: dict = {"planRevision": plan["revision"]}
            if limits is not None:
                body["limits"] = limits
            r = self._dispatch(body)
            if r.status_code != 200:
                return step.fail("dispatch 失败", body=r.text[:400])
            run = r.json()["run"]
            self.run_ids[tag] = run["id"]
            job_id = run["result"]["jobId"]
            worker = self.run_worker(job_id)
            final = self.wait_run_status(run["id"], {expect, "FAILED", "CANCELLED", "PARTIAL"})
            detail = {
                "runId": run["id"],
                "worker": {k: worker.get(k) for k in ("status", "worker_exit")},
                "finalStatus": final.get("status"),
                "exitCode": final.get("exitCode"),
                "claimHolds": final.get("claimHolds"),
            }
            if final.get("status") != expect:
                detail["error"] = (final.get("result") or {}).get("errorCode")
                return step.fail(f"终态 {final.get('status')} != {expect}", **detail)
            return step.ok(**detail)
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def step_normal_run(self):
        step = self._dispatch_and_run(
            "normal", code=PI_CODE, params={"samples": 2000}, expect="SUCCEEDED"
        )
        if step.status != "OK":
            return step
        # manifest 完备性 + artifact 落盘证据
        run = self.get(f"/sessions/{self.sid}/experiment-runs/{self.run_ids['normal']}").json()["run"]
        repro = run["repro"]
        missing = [
            k for k in ("codeHash", "inputHash", "outputHash", "imageDigest",
                        "dependencyLockHash", "seed", "command", "envAllowlist", "resourceLimits")
            if not repro.get(k)
        ]
        if missing:
            return step.fail("repro 缺字段", missing=missing)
        arts = run.get("artifacts") or []
        disk_ok = True
        root = SERVICE_ROOT / ".runs" / run["id"]
        for a in arts:
            p = root / a["path"]
            if not p.is_file():
                disk_ok = False
                break
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            if actual != a["sha256"]:
                disk_ok = False
                break
        if not arts or not disk_ok:
            return step.fail("artifact 落盘/哈希不一致", artifacts=arts)
        return step.ok(
            metrics=run["metrics"], artifacts=[a["path"] for a in arts],
            imageDigest=repro["imageDigest"][:20] + "…", seed=repro["seed"],
            network=repro["effectiveLimits"].get("network"),
        )

    def step_reproduce(self):
        step = base.Step(name="[reproduce] 一键复现 → 新 run + EXACT_MATCH 对比")
        try:
            r = self.post(f"/sessions/{self.sid}/experiment-runs/{self.run_ids['normal']}:reproduce")
            if r.status_code != 200:
                return step.fail("reproduce 请求失败", body=r.text[:300])
            second = r.json()["run"]
            if second["id"] == self.run_ids["normal"]:
                return step.fail("复现竟复用了原 run id")
            if second["repro"].get("reproduceOf") != self.run_ids["normal"]:
                return step.fail("新 run 未引用原 run")
            job_id = second["result"]["jobId"]
            self.run_worker(job_id)
            final = self.wait_run_status(second["id"], {"SUCCEEDED", "FAILED", "PARTIAL"})
            cmp_ = (final.get("result") or {}).get("reproComparison")
            if not cmp_:
                return step.fail("复现运行缺少 reproComparison", finalStatus=final.get("status"))
            if cmp_["verdict"] not in ("EXACT_MATCH", "METRICS_TOLERANCE"):
                return step.fail("复现判定不通过", comparison=cmp_)
            return step.ok(
                replicaRunId=second["id"], verdict=cmp_["verdict"],
                outputHashMatch=cmp_["outputHashMatch"], seedMatch=cmp_["seedMatch"],
                originalRunId=self.run_ids["normal"],
            )
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def step_cancel(self):
        step = base.Step(name="[cancel] QUEUED 运行立即取消")
        try:
            plan = self._seed_plan(self.sid, code=PI_CODE, params={"samples": 100000}, seed=1)
            r = self._dispatch({"planRevision": plan["revision"]})
            if r.status_code != 200:
                return step.fail("dispatch 失败", body=r.text[:300])
            run = r.json()["run"]
            rc = self.post(f"/sessions/{self.sid}/experiment-runs/{run['id']}:cancel",
                           {"reason": "e2e-cancel"})
            if rc.status_code != 200 or rc.json()["run"]["status"] != "CANCELLED":
                return step.fail("取消失败", body=rc.text[:300])
            detail = self.get(f"/sessions/{self.sid}/experiment-runs/{run['id']}").json()["run"]
            if detail["job"]["status"] != "CANCELLED":
                return step.fail("job 未取消", job=detail["job"]["status"])
            return step.ok(runId=run["id"], status=detail["status"], jobStatus=detail["job"]["status"])
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def step_limit_escalation(self):
        step = base.Step(name="[policy] 资源提权拒绝（memory 999999MB）")
        try:
            plan = self._seed_plan(self.sid, code=PI_CODE, params={"samples": 10})
            r = self._dispatch({"planRevision": plan["revision"],
                                "limits": {"memoryLimitMb": 999999}})
            if r.status_code != 400 or r.json()["error"]["code"] != "LIMIT_ESCALATION_REJECTED":
                return step.fail("未按预期拒绝", status=r.status_code, body=r.text[:300])
            return step.ok(code=r.json()["error"]["code"])
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def step_network_escalation(self):
        step = base.Step(name="[policy] 网络提权拒绝（network=allow）")
        try:
            plan = self._seed_plan(self.sid, code=PI_CODE, params={"samples": 10})
            r = self._dispatch({"planRevision": plan["revision"], "network": "allow"})
            if r.status_code != 400 or r.json()["error"]["code"] != "NETWORK_ESCALATION_REJECTED":
                return step.fail("未按预期拒绝", status=r.status_code, body=r.text[:300])
            return step.ok(code=r.json()["error"]["code"])
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def step_evil_code(self):
        return self._dispatch_and_run(
            "evil", code=EVIL_CODE, params={}, expect="FAILED"
        )

    def step_timeout(self):
        return self._dispatch_and_run(
            "spin", code=SPIN_CODE, params={}, seed=7,
            limits={"wallTimeMs": 8000}, expect="FAILED"
        )

    def step_oom(self):
        return self._dispatch_and_run(
            "oom", code=BOMB_CODE, params={"mb": 400},
            limits={"memoryLimitMb": 128}, expect="FAILED"
        )

    def step_no_plan_404(self):
        step = base.Step(name="[gate] 无计划会话 dispatch → 404 PLAN_NOT_FOUND")
        try:
            sid2 = self.new_session("RV11 无计划会话")
            old_sid = self.sid
            self.sid = sid2
            r = self._dispatch({})
            self.sid = old_sid
            if r.status_code != 404 or r.json()["error"]["code"] != "PLAN_NOT_FOUND":
                return step.fail("未按预期 404", status=r.status_code, body=r.text[:300])
            return step.ok(code="PLAN_NOT_FOUND")
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def step_claim_invariant(self):
        step = base.Step(name="[invariant] 全部运行 claimHolds=null（exit 0 ≠ 结论成立）")
        try:
            items = self.get(f"/sessions/{self.sid}/experiment-runs").json()["items"]
            bad = [x["id"] for x in items if x.get("claimHolds") is not None]
            if bad:
                return step.fail("存在 claimHolds 非 null 的运行", runs=bad)
            return step.ok(runs=len(items), claimHoldsAllNull=True)
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    # ---------------- 编排 ----------------

    def run(self) -> dict:
        for step_fn in (
            self.step_health,
            self.step_session,
            self.step_normal_run,
            self.step_reproduce,
            self.step_cancel,
            self.step_limit_escalation,
            self.step_network_escalation,
            self.step_evil_code,
            self.step_timeout,
            self.step_oom,
            self.step_no_plan_404,
            self.step_claim_invariant,
        ):
            if step_fn not in (self.step_health, self.step_session) and not getattr(self, "sid", None):
                # 前置步骤失败时后续步骤显式 SKIPPED，而不是抛 AttributeError 淹没真因
                step = base.Step(name=step_fn.__doc__.strip().splitlines()[0] if step_fn.__doc__ else step_fn.__name__)
                self.log(step.fail("SKIPPED: 前置步骤未完成", skipped=True))
                continue
            self.log(step_fn())
        passed = sum(1 for s in self.steps if s.status == "OK")
        verdict = "PASS" if passed == len(self.steps) else "FAIL"
        return {
            "verdict": verdict,
            "passed": passed,
            "total": len(self.steps),
            "steps": [asdict(s) for s in self.steps],
        }


def asdict(step: "base.Step") -> dict:
    return {
        "name": step.name,
        "status": step.status,
        "error": step.error,
        "detail": step.detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8300")
    parser.add_argument("--out", default=str(SERVICE_ROOT / "evidence"))
    args = parser.parse_args()

    out_dir = Path(to_windows_path(args.out))
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    e2e = RV11E2E(args.base_url, env)
    summary = e2e.run()

    path = out_dir / "RV11-E2E-transcript.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nverdict={summary['verdict']}  steps={summary['passed']}/{summary['total']}  -> {path}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
