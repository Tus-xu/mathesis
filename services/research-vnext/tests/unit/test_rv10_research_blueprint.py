"""RV10 · ResearchBlueprint —— 绑定 + 选定路线 + 实验计划/证据要求/验证门/预算截止/停止条件。

钉死的行为：
1. 绑定由**服务端**解析（最新已确认 ProblemMap / ResearchMap / RouteArena），
   模型**不**提供 revision 号 —— 消灭"幻觉修订号"这一整类缺陷；
   缺任一前置 ⇒ 可解释的拒绝码（集成测试覆盖 DB 侧）；
2. routeId 必须指向**已确认 arena 里的 CANDIDATE 路线**；
3. verificationGates / stopConditions / evidenceRequirements / budget 至少各一条；
4. 模型输出里的 deadline 必须是可解析 ISO 日期；
5. blueprint 是 proposal：**不得**使用 SOLVED/proven/已证实 等结论性词表
   （结论由 RV12 claim 域裁决）；
6. modelSpec 由服务端从选定路线生成（evidenceCoverage 只含可解析证据，
   缺口如实进 gaps）。
"""

from __future__ import annotations

import pytest

from research_vnext.core.domain import research_blueprint as RB
from research_vnext.core.domain import route_arena as RA


def _route(rid: str = "r1", *, kind: str = "THEORETICAL", **over) -> dict:
    base = {
        "id": rid,
        "label": f"路线{rid}",
        "kind": kind,
        "method": "排队论 M/M/1 解析推导",
        "hypotheses": [{"text": "等待时间随到达率超线性增长", "origin": "INFERENCE"}],
        "variables": [{"name": "λ", "role": "INDEPENDENT", "text": "到达率"}],
        "data": [{"text": "午高峰计数", "kind": "DATASET", "origin": "INFERENCE"}],
        "identifiability": {"text": "矩估计可识别", "status": "IDENTIFIABLE", "origin": "INFERENCE"},
        "metrics": [{"name": "等待误差", "direction": "MINIMIZE", "origin": "INFERENCE"}],
        "falsification": [{"text": "偏差>20% 证伪", "origin": "INFERENCE"}],
        "resources": {"computeHours": 4, "origin": "INFERENCE"},
        "risks": [{"text": "非指数服务", "severity": "HIGH", "mitigation": "M/G/1", "origin": "INFERENCE"}],
        "evidenceRefs": [],
        "tradeOffs": [{"text": "快但依赖分布假设", "origin": "INFERENCE"}],
        "unknowns": [{"text": "到达分布未知", "origin": "INFERENCE"}],
        "status": "CANDIDATE",
    }
    base.update(over)
    return base


def _arena_content(routes: list[dict] | None = None) -> dict:
    payload = {"routes": [r for r in (routes or [_route("r1"), _route("r2", kind="COMPUTATIONAL")]) if "status" not in r or r["status"] != "REJECTED"]}
    return RA.normalize(payload).content


def _bp_payload(**over) -> dict:
    base = {
        "objective": "比较三种建模路线对食堂等待时间的解释力，并选定一条进入实验",
        "routeId": "r1",
        "experimentPlan": {
            "objective": "用午高峰计数拟合 M/M/1 并与仿真对照",
            "datasetRef": "canteen-lunch-counts（待入库）",
            "metrics": ["平均等待误差"],
            "stages": [{"text": "取数", "origin": "INFERENCE"}, {"text": "拟合", "origin": "INFERENCE"}],
        },
        "evidenceRequirements": [{"text": "午高峰到达/服务间隔计数表", "kind": "DATASET"}],
        "verificationGates": [{"text": "闭式解与仿真偏差 ≤ 5%", "criterion": "REL_ERROR_LE_5PCT"}],
        "budget": {"computeHours": 8, "notes": "本地笔记本", "origin": "USER"},
        "deadline": {"at": "2026-10-01", "notes": "赛前两周冻结", "origin": "USER"},
        "stopConditions": [{"text": "偏差连续两轮 > 20% 即停并换路线", "origin": "INFERENCE"}],
        "assumptions": [{"text": "到达过程平稳", "origin": "INFERENCE"}],
        "risks": [{"text": "数据拿不到", "severity": "HIGH", "mitigation": "退化到文献参数", "origin": "INFERENCE"}],
    }
    base.update(over)
    return base


class TestPureNormalize:
    def test_valid_payload_passes(self):
        out = RB.normalize(_bp_payload())
        assert out.content["objective"]
        assert out.content["routeId"] == "r1"

    def test_no_objective_rejected(self):
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(_bp_payload(objective=""))
        assert e.value.code == "BLUEPRINT_NO_OBJECTIVE"

    def test_no_route_rejected(self):
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(_bp_payload(routeId=""))
        assert e.value.code == "BLUEPRINT_NO_ROUTE"

    def test_no_verification_gate_rejected(self):
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(_bp_payload(verificationGates=[]))
        assert e.value.code == "BLUEPRINT_NO_VERIFICATION_GATE"

    def test_no_stop_condition_rejected(self):
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(_bp_payload(stopConditions=[]))
        assert e.value.code == "BLUEPRINT_NO_STOP_CONDITION"

    def test_no_evidence_requirement_rejected(self):
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(_bp_payload(evidenceRequirements=[]))
        assert e.value.code == "BLUEPRINT_NO_EVIDENCE_REQUIREMENT"

    def test_no_budget_rejected(self):
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(_bp_payload(budget={"notes": "", "origin": "INFERENCE"}))
        assert e.value.code == "BLUEPRINT_NO_BUDGET"

    def test_bad_deadline_rejected(self):
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(_bp_payload(deadline={"at": "下个月", "origin": "INFERENCE"}))
        assert e.value.code == "BLUEPRINT_DEADLINE_INVALID"

    def test_unknown_top_level_key_rejected(self):
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(_bp_payload(extraField="x"))
        assert e.value.code == "BLUEPRINT_UNKNOWN_KEY"

    def test_conclusion_tells_rejected(self):
        """blueprint 只是 proposal：不得内置『已证实』类结论词。"""
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(_bp_payload(status="PROVEN"))
        assert e.value.code == "BLUEPRINT_CONCLUSION_TELL"

    def test_experiment_plan_without_any_metric_rejected(self):
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(_bp_payload(experimentPlan={"objective": "x", "metrics": []}))
        assert e.value.code == "BLUEPRINT_PLAN_NO_METRIC"


class TestServerBinding:
    """绑定解析（DB 依赖部分）——用假 rows 模拟已确认修订链。"""

    class _Row:
        def __init__(self, revision: int, content: dict):
            import uuid as _u

            self.id = _u.uuid4()
            self.revision = revision
            self.content = content
            self.content_hash = "h" * 64

    def _db_stub(self, pm=None, rm=None, arena=None):
        class Q:
            def __init__(self, val):
                self.val = val

            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def first(self):
                return self.val

        class DB:
            def __init__(self):
                self.rows = {"pm": pm, "rm": rm, "arena": arena}

            def query(self, table):
                name = table.__name__
                if name == "ProblemMapRow":
                    return Q(self.rows["pm"])
                if name == "ResearchMapRow":
                    return Q(self.rows["rm"])
                if name == "RouteArenaRow":
                    return Q(self.rows["arena"])
                return Q(None)

        return DB()

    def _arena_row(self):
        content = _arena_content()
        return self._Row(3, content)

    def test_full_binding_happens_server_side(self):
        db = self._db_stub(pm=self._Row(2, {}), rm=self._Row(1, {}), arena=self._arena_row())
        out = RB.bind_context(db, content=RB.normalize(_bp_payload()).content, session_id=None)  # type: ignore[arg-type]
        assert out.content["binds"]["problemMap"]["revision"] == 2
        assert out.content["binds"]["researchMap"]["revision"] == 1
        assert out.content["binds"]["routeArena"]["revision"] == 3
        assert out.content["binds"]["routeArena"]["routeId"] == "r1"
        # 选定路线内容被服务端复制进 blueprint（不依赖模型复述）
        assert out.content["route"]["id"] == "r1"
        assert out.content["route"]["method"]
        assert out.content["modelSpec"]["label"]

    def test_missing_route_arena_rejected(self):
        db = self._db_stub(pm=self._Row(2, {}), rm=self._Row(1, {}), arena=None)
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.bind_context(db, content=RB.normalize(_bp_payload()).content, session_id=None)  # type: ignore[arg-type]
        assert e.value.code == "BLUEPRINT_REQUIRES_ROUTE_ARENA"

    def test_unknown_route_rejected(self):
        db = self._db_stub(pm=self._Row(2, {}), rm=self._Row(1, {}), arena=self._arena_row())
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.bind_context(db, content=RB.normalize(_bp_payload(routeId="r9")).content, session_id=None)  # type: ignore[arg-type]
        assert e.value.code == "BLUEPRINT_ROUTE_UNKNOWN"

    def test_rejected_route_cannot_be_selected(self):
        over = _route("r1", resources={"computeHours": 500, "origin": "INFERENCE"})
        arena_content = RA.normalize(
            {
                "routes": [over, _route("r2", kind="COMPUTATIONAL"), _route("r3", kind="EMPIRICAL")],
                "budget": {"maxComputeHours": 40},
            }
        ).content
        db = self._db_stub(pm=self._Row(2, {}), rm=self._Row(1, {}), arena=self._Row(1, arena_content))
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.bind_context(db, content=RB.normalize(_bp_payload(routeId="r1")).content, session_id=None)  # type: ignore[arg-type]
        assert e.value.code == "BLUEPRINT_ROUTE_REJECTED"

    def test_model_cannot_supply_revisions(self):
        """绑定由服务端解析：模型连 revision 号都不许提供（消灭幻觉修订号）。"""
        payload = _bp_payload()
        payload["problemMapRevision"] = 99
        with pytest.raises(RB.BlueprintRejected) as e:
            RB.normalize(payload)
        assert e.value.code == "BLUEPRINT_UNKNOWN_KEY"
