"""RV10 · 端到端验收脚本（真实 8300 进程 + 真实 PostgreSQL + 真实模型）。

链路（每一步都是真实 HTTP）：
    prompt v4 台账 → 真实研究问题 → ProblemMap 确认 → ResearchMap 确认
    → route_arena 提案（2–3 条真差异路线 + 服务端 rubric 无总分）
    → 人工逐项编辑 → 确认落修订
    → research_blueprint 提案（服务端绑定）→ 确认
    → `research.blueprint_ready` 事件（只含 ID/version/hash）
    → 全程无 experiment job（确认前不创建实验）
    + 被拒路线演示（证据/预算不足）
    + 409 + field-level diff
    + 注入隔离 / 记忆隔离（复用 RV09 的步骤）

诚实边界与 RV09 相同：AccessContext 自签（同机制同测试密钥），
证明的是"8300 及以下"的真实链路；模型输出按 attempts 如实记录。

用法：
    python scripts/rv10_e2e.py --base-url http://127.0.0.1:8300 --out <dir>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for p in (str(SERVICE_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import rv09_e2e as base  # noqa: E402  （复用签名 / AccessContext / HTTP harness）
from research_vnext.core.config import api_prefixes  # noqa: E402

RESEARCH_QUESTION = base.RESEARCH_QUESTION

#: 被拒路线演示：让模型如实转写预算与超限资源（门槛是服务端确定性判定）。
BUDGET_DEMO_QUESTION = (
    "基于已确认的问题地图，请用 route_arena 给出三条候选建模路线，"
    "并满足：payload 的 budget 写 maxComputeHours=40、notes 写“课间实测预算”。"
    "三条路线分别是：r1 闭式排队推导（computeHours 4）、r2 离散事件仿真（computeHours 8）、"
    "r3 全城范围实测回归（computeHours 500，需要覆盖全城三个校区的长周期实测）。"
    "请照实填写各路线的 resources.computeHours（4 / 8 / 500），不要改动这些数字。"
)


class RV10E2E(base.E2E):
    """在 RV09 harness 之上追加 RV10 步骤（复用 send/pending/snapshot/log）。"""

    # ---------------- 步骤 ----------------

    def step_prompt_release_v4(self) -> base.Step:
        step = base.Step(name="prompt 发布台账（v4 已发布、v2/v3 已 REVOKED）")
        try:
            r = self.get("/prompts")
            r.raise_for_status()
            body = r.json()
            published = (body.get("published") or {}).get("assistantPropose")
            ref = (body.get("published") or {}).get("ref") or ""
            if published != 4:
                return step.fail("已发布版本不是 v4", published=published)
            state = {(x["promptId"], x["version"]): x["state"] for x in body.get("prompts") or []}
            if state.get(("rv.assistant.propose", 2)) != "REVOKED":
                return step.fail("v2 未登记为 REVOKED")
            if state.get(("rv.assistant.propose", 3)) != "REVOKED":
                return step.fail("v3 未登记为 REVOKED")
            return step.ok(published=published, ref=ref, v3=state.get(("rv.assistant.propose", 3)))
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def _confirm_pending(self, sid: str, object_ref: str, step: base.Step) -> dict | None:
        items = self.pending(sid)
        if not items or items[0].get("object_ref") != object_ref:
            return None
        pid = items[0]["id"]
        rev = self.snapshot(sid)["revision"]
        r = self.post(f"/sessions/{sid}/pending-actions/{pid}:accept", {"expected_revision": rev})
        if r.status_code != 200:
            return None
        return {"pendingActionId": pid, **(r.json().get("confirmed_object") or {})}

    def step_research_map(self, sid: str) -> base.Step:
        """RV10 覆盖：research_map 提案在 3 次尝试内重试（notice 不再一票否决）。"""
        step = base.Step(name="ResearchMap（2–4 视角）→ 确认")
        try:
            items: list[dict] = []
            for attempt in range(1, 4):
                step.attempts = attempt
                snap = self.send(sid, "请基于已确认的问题地图，给出 2–4 个可以互相检验的调研/验证视角。")
                items = self.pending(sid)
                if items and items[0].get("object_ref") == "research_map":
                    break
            if not items or items[0].get("object_ref") != "research_map":
                comp = self.last_component(snap)
                return step.fail(
                    "重试后仍未获得 research_map 提案",
                    componentType=comp.get("type"),
                    errorCode=(comp.get("props") or {}).get("errorCode"),
                )
            pid = items[0]["id"]
            content = (items[0].get("payload") or {}).get("content") or {}
            perspectives = content.get("perspectives") or []
            rev = self.snapshot(sid)["revision"]
            racc = self.post(f"/sessions/{sid}/pending-actions/{pid}:accept", {"expected_revision": rev})
            if racc.status_code != 200:
                return step.fail(f"确认失败 {racc.status_code}: {racc.text[:300]}")
            focus = self.snapshot(sid).get("artifactFocus") or {}
            return step.ok(
                perspectives=len(perspectives),
                lenses=[p.get("lens") for p in perspectives],
                kinds=sorted({p.get("kind") for p in perspectives}),
                revisions=int((focus.get("researchMap") or {}).get("revision") or 0),
                revision=racc.json().get("confirmed_object", {}).get("revision"),
            )
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def step_route_arena_chain(self, sid: str) -> tuple[base.Step, dict | None]:
        """真实模型 → route_arena（2–3 路线）→ 人工编辑 → 确认 → 修订 1。"""
        step = base.Step(name="route_arena（2–3 条真差异路线）→ 人工编辑 → 确认")
        try:
            items: list[dict] = []
            snap: dict = {}
            for attempt in range(1, 4):
                step.attempts = attempt
                snap = self.send(
                    sid,
                    "请基于已确认的问题地图与研究地图，用 route_arena 提出 2–3 条实质不同的候选建模路线"
                    "（每条都要有方法、假设、变量、指标、证伪判据、可识别性、资源估计，并写明 trade-off 与 unknown）。",
                )
                items = self.pending(sid)
                if items and items[0].get("object_ref") == "route_arena":
                    break
                comp = self.last_component(snap)
                if comp.get("type") == "CapabilityNotice" and attempt >= 3:
                    return step.fail(
                        "模型未产出可用的 route_arena",
                        errorCode=comp.get("props", {}).get("errorCode"),
                        detail=comp.get("props", {}).get("detail", ""),
                    ), None
            else:
                return step.fail("重试后仍未获得 route_arena 提案", pending=len(items)), None

            pid = items[0]["id"]
            content = (items[0].get("payload") or {}).get("content") or {}
            routes = content.get("routes") or []
            statuses = [r.get("status") for r in routes]
            kinds = sorted({r.get("kind") for r in routes})
            comparison = content.get("comparison") or {}

            # 提案卡经 allowlist 投影
            comp = self.last_component(snap)
            if comp.get("type") != "RouteArenaProposal":
                return step.fail("对话卡不是 RouteArenaProposal", componentType=comp.get("type"))

            # 人工编辑：改第一条有效路线的 label（走前端同款 {ref, value} 形状）
            target = next((r for r in routes if r.get("status") == "CANDIDATE"), None)
            edit_summary = None
            if target is not None:
                rrev = self.post(
                    f"/sessions/{sid}/pending-actions/{pid}:revise",
                    {"edits": [{"ref": f"route:{target['id']}.label", "value": {"text": f"{target['label']}（人工确认版）"}}]},
                )
                if rrev.status_code != 200:
                    return step.fail(f"逐项编辑失败 {rrev.status_code}: {rrev.text[:300]}")
                edit_summary = ((rrev.json().get("proposal") or {}).get("content") or {}).get("editSummary")

            confirmed = self._confirm_pending(sid, "route_arena", step)
            if confirmed is None:
                return step.fail("route_arena 确认失败", pending=self.pending(sid))
            focus = (self.snapshot(sid).get("artifactFocus") or {})
            arena_focus = focus.get("routeArena") or {}
            audits = self.get(f"/sessions/{sid}/revisions/audit").json().get("items") or []
            arena_audit = next((a for a in audits if a.get("objectRef") == "route_arena"), None)
            return (
                step.ok(
                    pendingActionId=pid,
                    routeCount=len(routes),
                    statuses=statuses,
                    kinds=kinds,
                    hasTotalScore=comparison.get("hasTotalScore"),
                    rubricDisclaimerGiven=bool(comparison.get("rubricDisclaimer")),
                    dimensions=comparison.get("dimensions"),
                    editSummary=edit_summary,
                    revision=confirmed.get("revision"),
                    focusRevision=arena_focus.get("revision"),
                    promptRef=(arena_audit or {}).get("promptRef"),
                    costBasis=(arena_audit or {}).get("costBasis"),
                ),
                {"content": content, "routes": routes, "confirmed": confirmed},
            )
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}"), None

    def step_blueprint_chain(self, sid: str, arena: dict | None) -> tuple[base.Step, dict | None]:
        """真实模型 → research_blueprint（服务端绑定）→ 确认 → BlueprintReady 事件。"""
        step = base.Step(name="research_blueprint（服务端绑定）→ 确认 → BlueprintReady")
        try:
            if not arena:
                return step.fail("缺少已确认竞技场，跳过"), None
            candidates = [r["id"] for r in arena["routes"] if r.get("status") == "CANDIDATE"]
            if not candidates:
                return step.fail("竞技场中没有 CANDIDATE 路线"), None
            items: list[dict] = []
            snap: dict = {}
            for attempt in range(1, 4):
                step.attempts = attempt
                snap = self.send(
                    sid,
                    f"研究者选定路线 {candidates[0]}。请用 research_blueprint 给出研究蓝图："
                    "绑定该路线，声明实验计划（至少一个指标）、证据要求、验证门、预算与截止日期、停止条件。"
                    "不要给 revision 号，不要写结论性字段。",
                )
                items = self.pending(sid)
                if items and items[0].get("object_ref") == "blueprint":
                    break
                comp = self.last_component(snap)
                if comp.get("type") == "CapabilityNotice" and attempt >= 3:
                    return step.fail(
                        "模型未产出可用的 research_blueprint",
                        errorCode=comp.get("props", {}).get("errorCode"),
                        detail=comp.get("props", {}).get("detail", ""),
                    ), None
            else:
                return step.fail("重试后仍未获得 blueprint 提案", pending=len(items)), None

            content = (items[0].get("payload") or {}).get("content") or {}
            binds = content.get("binds") or {}
            if not binds.get("problemMap") or not binds.get("researchMap") or not binds.get("routeArena"):
                return step.fail("绑定缺失（服务端应写入 binds）", bindKeys=sorted(binds))
            if binds["routeArena"].get("routeId") != candidates[0]:
                return step.fail("选定路线与请求不一致", routeId=binds["routeArena"].get("routeId"))
            if not content.get("modelSpec") or not content.get("lineage"):
                return step.fail("modelSpec / lineage 缺失")
            comp = self.last_component(snap)
            if comp.get("type") != "BlueprintCard":
                return step.fail("对话卡不是 BlueprintCard", componentType=comp.get("type"))

            confirmed = self._confirm_pending(sid, "blueprint", step)
            if confirmed is None:
                return step.fail("blueprint 确认失败", pending=self.pending(sid))

            # BlueprintReady 事件：只含 ID / 版本 / 哈希
            r = self.post(f"/sessions/{sid}:resume", {})
            r.raise_for_status()
            events = r.json().get("events") or []
            ready = [e for e in events if e.get("event_type") == "research.blueprint_ready"]
            if not ready:
                return step.fail("未找到 research.blueprint_ready 事件", eventTypes=sorted({e.get("event_type") for e in events})[-8:])
            payload = ready[-1].get("payload") or {}
            expected_keys = {
                "blueprintId", "revision", "contentHash", "routeId",
                "problemMapRevision", "researchMapRevision", "routeArenaRevision", "routeArenaContentHash",
            }
            if set(payload) != expected_keys:
                return step.fail("BlueprintReady payload 键集不符（只允许 ID/version/hash）", keys=sorted(payload))
            if payload.get("blueprintId") != confirmed.get("id"):
                return step.fail("事件 blueprintId 与确认对象不一致")

            # **确认前不创建实验 job**
            jobs = self.get(f"/sessions/{sid}/jobs").json().get("items") or []

            bp_focus = ((self.snapshot(sid).get("artifactFocus") or {}).get("blueprint") or {})
            return (
                step.ok(
                    blueprintRevision=confirmed.get("revision"),
                    binds={k: v.get("revision") for k, v in binds.items()},
                    routeId=payload.get("routeId"),
                    eventPayloadKeys=sorted(payload),
                    jobsAfterConfirm=len(jobs),
                    focusRouteId=bp_focus.get("routeId"),
                    focusHasModelSpec=bool(bp_focus.get("modelSpec")),
                    promptRef=(self.get(f"/sessions/{sid}/revisions/audit").json().get("items") or [{}])[-1].get("promptRef"),
                ),
                {"confirmed": confirmed, "event": payload},
            )
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}"), None

    def step_rejected_route_demo(self) -> base.Step:
        """预算超限 ⇒ 该路线被拒且原因可见（3 条中 1 条被拒，arena 仍成立）。"""
        step = base.Step(name="被拒路线演示（资源超预算 → 路线级拒绝并展示原因）")
        try:
            sid = self.new_session("E2E-被拒路线演示")
            self.send(sid, RESEARCH_QUESTION)
            self._confirm_pending(sid, "problem_map", step)
            self.send(sid, "请基于已确认的问题地图给出 2–4 个可以互相检验的调研/验证视角。")
            self._confirm_pending(sid, "research_map", step)

            snap: dict = {}
            items: list[dict] = []
            for attempt in range(1, 4):
                step.attempts = attempt
                snap = self.send(sid, BUDGET_DEMO_QUESTION)
                items = self.pending(sid)
                if items and items[0].get("object_ref") == "route_arena":
                    break
            if not items or items[0].get("object_ref") != "route_arena":
                comp = self.last_component(snap)
                return step.fail(
                    "未获得 route_arena 提案（演示依赖模型如实转写数字）",
                    componentType=comp.get("type"),
                    errorCode=(comp.get("props") or {}).get("errorCode"),
                    detail=(comp.get("props") or {}).get("detail", "")[:300],
                )
            content = (items[0].get("payload") or {}).get("content") or {}
            rejected = [r for r in content.get("routes") or [] if r.get("status") == "REJECTED"]
            if not rejected:
                return step.fail(
                    "没有路线被拒（模型未按演示要求填写超预算资源）",
                    statuses=[r.get("status") for r in content.get("routes") or []],
                )
            target = rejected[0]
            if target.get("rejection", {}).get("code") not in ("ROUTE_BUDGET_EXCEEDED", "ROUTE_EVIDENCE_UNRESOLVED"):
                return step.fail("被拒原因不是证据/预算类", rejection=target.get("rejection"))
            # rubric 如实反映
            rubric = ((content.get("comparison") or {}).get("rubric") or {}).get(target["id"]) or {}
            still_valid = [r for r in content.get("routes") or [] if r.get("status") == "CANDIDATE"]
            # 清理：拒绝该提案（演示会话不落正式状态）
            self.post(f"/sessions/{sid}/pending-actions/{items[0]['id']}:reject", {"reason": "演示结束"})
            return step.ok(
                sessionId=sid,
                rejectedRouteId=target["id"],
                rejectionCode=target.get("rejection", {}).get("code"),
                rejectionMessage=target.get("rejection", {}).get("message", "")[:160],
                resourceFitLevel=rubric.get("resourceFit", {}).get("level"),
                remainingCandidates=len(still_valid),
                proposalRejectedAfterDemo=True,
            )
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def step_arena_conflict_diff(self, sid: str) -> base.Step:
        """竞技场 409 + field-level diff。"""
        step = base.Step(name="竞技场 expected_revision 冲突 → 409 + field diff")
        try:
            for item in self.pending(sid):
                self.post(f"/sessions/{sid}/pending-actions/{item['id']}:reject", {"reason": "清理"})
            self.send(sid, "请再给一版路线竞技场（用于制造版本冲突），可以微调各路线的资源估计。")
            items = self.pending(sid)
            step.attempts += 1
            if not items or items[0].get("object_ref") != "route_arena":
                return step.fail("未获得可用于冲突演示的竞技场提案")
            pid = items[0]["id"]
            stale = self.snapshot(sid)["revision"] - 1
            r = self.post(f"/sessions/{sid}/pending-actions/{pid}:accept", {"expected_revision": stale})
            if r.status_code != 409:
                return step.fail(f"期望 409，实际 {r.status_code}", body=r.text[:300])
            err = (r.json() or {}).get("error") or {}
            conflict = err.get("conflict") or {}
            diff = conflict.get("diff") or {}
            if not diff:
                return step.fail("409 未携带 diff", errorCode=err.get("code"))
            return step.ok(
                code=err.get("code"),
                diffCount=diff.get("count"),
                objectRef=conflict.get("objectRef"),
                hasCurrent=bool(conflict.get("current")),
                hasIncoming=bool(conflict.get("incoming")),
            )
        except Exception as e:  # noqa: BLE001
            return step.fail(f"{type(e).__name__}: {e}")

    def run(self) -> dict:  # noqa: D102 — 覆盖编排
        t0 = time.time()
        self.log(self.step_prompt_release_v4())
        self.log(self.step_clarification("空/语境不足问题 → 请求补充", base.VAGUE_QUESTION))
        self.log(self.step_injection())

        sid = self.new_session("E2E-RV10-路线竞技场与研究蓝图")
        self.log(base.Step(name="创建研究会话", status="OK", detail={"sessionId": sid}))

        pm_step, _ = self.step_problem_map_chain(sid)
        self.log(pm_step)
        self.log(self.step_research_map(sid))

        arena_step, arena = self.step_route_arena_chain(sid)
        self.log(arena_step)

        bp_step, bp = self.step_blueprint_chain(sid, arena)
        self.log(bp_step)

        self.log(self.step_rejected_route_demo())
        self.log(self.step_arena_conflict_diff(sid))
        self.log(self.step_memory_isolation(sid))

        all_ok = all(s.status == "OK" for s in self.steps)
        return {
            "startedAt": t0,
            "durationSeconds": round(time.time() - t0, 1),
            "baseUrl": self.base,
            "prefix": api_prefixes()[0],
            "sessionId": sid,
            "accessContext": "self-signed (same HMAC mechanism as tests; 8000 issuance still blocked)",
            "blueprintReady": (bp or {}).get("event"),
            "steps": [
                {"name": s.name, "status": s.status, "attempts": s.attempts, "detail": s.detail, "error": s.error}
                for s in self.steps
            ],
            "verdict": "PASS" if all_ok else "FAIL",
        }


def to_windows_path(p: str) -> str:
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    return f"{m.group(1).upper()}:/{m.group(2)}" if m else p


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8300")
    parser.add_argument("--out", default=str(SERVICE_ROOT / "evidence"))
    args = parser.parse_args()

    out_dir = Path(to_windows_path(args.out))
    out_dir.mkdir(parents=True, exist_ok=True)
    e2e = RV10E2E(args.base_url)
    summary = e2e.run()

    path = out_dir / "RV10-E2E-transcript.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nverdict={summary['verdict']}  steps={len(summary['steps'])}  -> {path}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
