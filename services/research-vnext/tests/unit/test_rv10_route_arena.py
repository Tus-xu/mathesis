"""RV10 · RouteArena —— 2–3 条**实质不同**的建模路线 + 服务端结构化 rubric。

任务书钉死的行为（每条都有对应测试）：
1. 少于两条真差异路线 ⇒ 拒（数量门 / 可区分门 / kind 多样性门）；
2. 每条路线必须有：模型/方法、假设、变量/参数、数据、可识别性、指标、
   证伪/反例、资源、风险、证据覆盖 —— 缺一即该路线被拒；
3. 假引用（evidenceRefs 指向不可解析证据）⇒ 该路线因证据不足被拒
   （集成测试覆盖 DB 侧；本文件钉纯结构行为）；
4. 资源超预算 ⇒ 该路线因预算被拒（仍保留在 arena 里展示被拒原因）；
5. 模型不得给"总分/胜者/排名"——出现即整轮拒绝；
6. rubric 由**服务端**从结构推导（STRONG/MIXED/WEAK/UNKNOWN），无总分；
7. trade-off 与 unknown 原样保留，不得被比较逻辑吞掉；
8. 逐项编辑：改文本可以，改 origin/id/kind/status 不行；编辑后必须整体重校验。
"""

from __future__ import annotations

import pytest

from research_vnext.core.domain import route_arena as RA


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


def _route(rid: str = "r1", *, kind: str = "THEORETICAL", label: str | None = None, **over) -> dict:
    base = {
        "id": rid,
        "label": label or f"路线{rid}",
        "kind": kind,
        "method": "排队论 M/M/1 解析推导",
        "hypotheses": [{"text": "高峰期等待时间随到达率超线性增长", "origin": "INFERENCE"}],
        "variables": [
            {"name": "到达率 λ", "role": "INDEPENDENT", "text": "每小时到达人数"},
            {"name": "等待时间 W", "role": "DEPENDENT", "text": "平均等待分钟"},
            {"name": "服务率 μ", "role": "PARAMETER", "text": "由窗口数决定"},
        ],
        "data": [{"text": "食堂午高峰实测计数（若可获权）", "kind": "DATASET", "origin": "INFERENCE"}],
        "identifiability": {"text": "λ、μ 可由到达/服务间隔矩估计识别", "status": "IDENTIFIABLE", "origin": "INFERENCE"},
        "metrics": [{"name": "平均等待误差", "direction": "MINIMIZE", "origin": "INFERENCE"}],
        "falsification": [{"text": "闭式解与仿真偏差 > 20% 即证伪", "origin": "INFERENCE"}],
        "resources": {"computeHours": 4, "dataVolume": "<10MB 计数表", "origin": "INFERENCE"},
        "risks": [{"text": "服务时间非指数", "severity": "HIGH", "mitigation": "改用 M/G/1 近似", "origin": "INFERENCE"}],
        "evidenceRefs": [],
        "tradeOffs": [{"text": "快但依赖分布假设", "origin": "INFERENCE"}],
        "unknowns": [{"text": "真实到达分布形状未知", "origin": "INFERENCE"}],
    }
    base.update(over)
    return base


def _arena(routes: list[dict], **extra) -> dict:
    payload = {"routes": routes}
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------
# 1. 数量与可区分性
# --------------------------------------------------------------------------


class TestRouteCountAndDistinguishability:
    def test_single_route_is_rejected(self):
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.normalize(_arena([_route("r1")]))
        assert e.value.code == "ROUTE_ARENA_ROUTE_COUNT"

    def test_four_routes_is_rejected(self):
        routes = [
            _route("r1", kind="THEORETICAL"),
            _route("r2", kind="COMPUTATIONAL"),
            _route("r3", kind="EMPIRICAL"),
            _route("r4", kind="LITERATURE"),
        ]
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.normalize(_arena(routes))
        assert e.value.code == "ROUTE_ARENA_ROUTE_COUNT"

    def test_two_routes_is_accepted(self):
        out = RA.normalize(_arena([_route("r1", kind="THEORETICAL"), _route("r2", kind="COMPUTATIONAL")]))
        assert len(out.content["routes"]) == 2
        assert all(r["status"] == "CANDIDATE" for r in out.content["routes"])

    def test_identical_labels_are_not_distinguishable(self):
        routes = [_route("r1", label="仿真路线"), _route("r2", label="仿真 路线", kind="COMPUTATIONAL")]
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.normalize(_arena(routes))
        assert e.value.code == "ROUTE_NOT_DISTINGUISHABLE"

    def test_single_kind_is_rejected(self):
        routes = [_route("r1", kind="THEORETICAL"), _route("r2", kind="THEORETICAL")]
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.normalize(_arena(routes))
        assert e.value.code == "ROUTE_SINGLE_LENS"


# --------------------------------------------------------------------------
# 2. 每条路线的必备元素（缺一 ⇒ 该路线被拒，并保留原因）
# --------------------------------------------------------------------------


class TestRouteRequiredElements:
    @pytest.mark.parametrize(
        ("drop", "code"),
        [
            ("method", "ROUTE_NO_METHOD"),
            ("hypotheses", "ROUTE_NO_HYPOTHESIS"),
            ("variables", "ROUTE_NO_VARIABLES"),
            ("data", None),  # 数据允许为空但必须登记缺口
            ("metrics", "ROUTE_NO_METRICS"),
            ("falsification", "ROUTE_NO_FALSIFICATION"),
            ("resources", "ROUTE_NO_RESOURCES"),
        ],
    )
    def test_missing_element_marks_route_rejected(self, drop, code):
        bad = _route("r1", kind="THEORETICAL")
        bad.pop(drop)
        good_a = _route("r2", kind="COMPUTATIONAL")
        good_b = _route("r3", kind="EMPIRICAL")
        out = RA.normalize(_arena([bad, good_a, good_b]))
        r1 = next(r for r in out.content["routes"] if r["id"] == "r1")
        if code is None:
            # 数据为空 = 缺口登记，不是拒绝
            assert r1["status"] == "CANDIDATE"
            assert any(i["routeId"] == "r1" for i in out.insufficiencies)
        else:
            assert r1["status"] == "REJECTED"
            assert r1["rejection"]["code"] == code
            # 被拒路线保留在 arena 里（原因可见），但不算有效路线
            assert len([r for r in out.content["routes"] if r["status"] == "CANDIDATE"]) == 2

    def test_unfalsifiable_hypothesis_needs_falsification(self):
        """『不可反驳假设』：有假设但没有证伪判据 ⇒ 路线被拒。"""
        bad = _route("r1", kind="THEORETICAL", falsification=[])
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.normalize(_arena([bad, _route("r2", kind="COMPUTATIONAL")]))
        assert e.value.code == "ROUTE_ARENA_ROUTE_COUNT"

    def test_rejected_route_keeps_reason_and_does_not_count(self):
        bad = _route("r1", kind="THEORETICAL")
        bad.pop("metrics")
        good_a = _route("r2", kind="COMPUTATIONAL")
        good_b = _route("r3", kind="EMPIRICAL")
        out = RA.normalize(_arena([bad, good_a, good_b]))
        statuses = {r["id"]: r["status"] for r in out.content["routes"]}
        assert statuses == {"r1": "REJECTED", "r2": "CANDIDATE", "r3": "CANDIDATE"}
        rejected = next(r for r in out.content["routes"] if r["id"] == "r1")
        assert rejected["rejection"]["code"] == "ROUTE_NO_METRICS"
        assert rejected["rejection"]["message"]

    def test_rejection_leaves_fewer_than_two_valid(self):
        bad = _route("r1", kind="THEORETICAL")
        bad.pop("method")
        half = _route("r2", kind="COMPUTATIONAL")
        half.pop("metrics")
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.normalize(_arena([bad, half]))
        assert e.value.code == "ROUTE_ARENA_ROUTE_COUNT"


# --------------------------------------------------------------------------
# 3. 证据门与预算门（路线级拒绝）
# --------------------------------------------------------------------------


class TestEvidenceAndBudgetGates:
    def test_budget_exceeded_rejects_route_but_keeps_reason(self):
        over = _route("r1", kind="THEORETICAL", resources={"computeHours": 500, "dataVolume": "集群", "origin": "INFERENCE"})
        out = RA.normalize(
            _arena(
                [over, _route("r2", kind="COMPUTATIONAL"), _route("r3", kind="EMPIRICAL")],
                budget={"maxComputeHours": 40},
            )
        )
        r1 = next(r for r in out.content["routes"] if r["id"] == "r1")
        assert r1["status"] == "REJECTED"
        assert r1["rejection"]["code"] == "ROUTE_BUDGET_EXCEEDED"
        assert len([r for r in out.content["routes"] if r["status"] == "CANDIDATE"]) == 2

    def test_budget_missing_compute_hours_is_unknown_not_rejected(self):
        vag = _route("r1", kind="THEORETICAL", resources={"computeHours": None, "notes": "未估", "origin": "INFERENCE"})
        out = RA.normalize(_arena([vag, _route("r2", kind="COMPUTATIONAL")], budget={"maxComputeHours": 40}))
        r1 = next(r for r in out.content["routes"] if r["id"] == "r1")
        assert r1["status"] == "CANDIDATE"
        assert any(i["routeId"] == "r1" for i in out.insufficiencies)


# --------------------------------------------------------------------------
# 4. 模型不得裁决：总分 / 胜者 / 排名
# --------------------------------------------------------------------------


class TestVerdictForbidden:
    @pytest.mark.parametrize(
        "key", ["winner", "recommendation", "recommended", "ranking", "totalScore", "total_score", "bestRoute", "overallScore"]
    )
    def test_verdict_keys_are_rejected(self, key):
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.normalize(_arena([_route("r1", kind="THEORETICAL"), _route("r2", kind="COMPUTATIONAL")], **{key: "r1"}))
        assert e.value.code == "ROUTE_ARENA_VERDICT_FORBIDDEN"

    def test_unknown_top_level_key_rejected(self):
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.normalize(_arena([_route("r1", kind="THEORETICAL"), _route("r2", kind="COMPUTATIONAL")], steps=[1, 2]))
        assert e.value.code == "ROUTE_ARENA_UNKNOWN_KEY"

    def test_rubric_has_no_total_and_carries_disclaimer(self):
        out = RA.normalize(_arena([_route("r1", kind="THEORETICAL"), _route("r2", kind="COMPUTATIONAL")]))
        comparison = out.content["comparison"]
        assert "rubric" in comparison
        assert comparison.get("hasTotalScore") is False
        assert "总分" in comparison["rubricDisclaimer"]
        # 每条路线每个维度只有 STRONG/MIXED/WEAK/UNKNOWN，没有数字分
        allowed = {"STRONG", "MIXED", "WEAK", "UNKNOWN"}
        for rid, dims in comparison["rubric"].items():
            for dim, entry in dims.items():
                assert entry["level"] in allowed, (rid, dim, entry)


# --------------------------------------------------------------------------
# 5. rubric 由服务端推导（确定性 + 反映结构）
# --------------------------------------------------------------------------


class TestServerRubric:
    def test_rubric_is_deterministic(self):
        payload = _arena([_route("r1", kind="THEORETICAL"), _route("r2", kind="COMPUTATIONAL")])
        a = RA.normalize(payload).content["comparison"]["rubric"]
        b = RA.normalize(payload).content["comparison"]["rubric"]
        assert a == b

    def test_no_evidence_refs_yields_unknown_evidence_dimension(self):
        out = RA.normalize(_arena([_route("r1", kind="THEORETICAL"), _route("r2", kind="COMPUTATIONAL")]))
        dims = out.content["comparison"]["rubric"]["r1"]
        assert dims["evidence"]["level"] == "UNKNOWN"

    def test_resource_over_budget_reflects_in_rubric(self):
        over = _route("r1", kind="THEORETICAL", resources={"computeHours": 500, "origin": "INFERENCE"})
        out = RA.normalize(
            _arena(
                [over, _route("r2", kind="COMPUTATIONAL"), _route("r3", kind="EMPIRICAL")],
                budget={"maxComputeHours": 40},
            )
        )
        assert out.content["comparison"]["rubric"]["r1"]["resourceFit"]["level"] == "WEAK"

    def test_trade_offs_and_unknowns_survive_normalization(self):
        out = RA.normalize(_arena([_route("r1", kind="THEORETICAL"), _route("r2", kind="COMPUTATIONAL")]))
        r1 = next(r for r in out.content["routes"] if r["id"] == "r1")
        assert r1["tradeOffs"] and r1["unknowns"]
        assert r1["tradeOffs"][0]["text"] == "快但依赖分布假设"


# --------------------------------------------------------------------------
# 6. 逐项编辑（revise 流的纯函数部分）
# --------------------------------------------------------------------------


class TestApplyCorrections:
    def _content(self, routes: list[dict] | None = None) -> dict:
        if routes is None:
            routes = [_route("r1", kind="THEORETICAL"), _route("r2", kind="COMPUTATIONAL")]
        return RA.normalize(_arena(routes)).content

    def _three(self) -> dict:
        return self._content(
            [_route("r1", kind="THEORETICAL"), _route("r2", kind="COMPUTATIONAL"), _route("r3", kind="EMPIRICAL")]
        )

    def test_edit_label_text_allowed(self):
        content = RA.apply_corrections(
            self._content(), accept=[], reject=[], edits=[{"target": "route:r1.label", "text": "闭式推导（改）"}]
        )
        r1 = next(r for r in content["routes"] if r["id"] == "r1")
        assert r1["label"] == "闭式推导（改）"

    def test_edit_origin_is_forbidden(self):
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.apply_corrections(
                self._content(),
                accept=[],
                reject=[],
                edits=[{"target": "route:r1.hypotheses#0.origin", "text": "USER"}],
            )
        assert e.value.code == "ROUTE_ARENA_EDIT_FORBIDDEN_FIELD"

    def test_edit_status_and_id_are_forbidden(self):
        for target in ("route:r1.status", "route:r1.id", "route:r1.kind", "route:r1.evidenceRefs"):
            with pytest.raises(RA.RouteArenaRejected) as e:
                RA.apply_corrections(self._content(), accept=[], reject=[], edits=[{"target": target, "text": "x"}])
            assert e.value.code == "ROUTE_ARENA_EDIT_FORBIDDEN_FIELD"

    def test_reject_route_keeps_two_of_three(self):
        content = self._three()
        kept = RA.apply_corrections(content, accept=[], reject=["route:r3"], edits=[])
        assert sorted(r["id"] for r in kept["routes"]) == ["r1", "r2"]

    def test_reject_below_two_valid_is_rejected(self):
        content = self._content()
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.apply_corrections(content, accept=[], reject=["route:r1"], edits=[])
        assert e.value.code == "ROUTE_ARENA_ROUTE_COUNT"

    def test_edit_that_breaks_kind_diversity_is_rejected(self):
        content = self._content()
        # 把 r2 的 kind 改成与 r1 相同 ⇒ 整体重校验必须拦下
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.apply_corrections(
                content, accept=[], reject=[], edits=[{"target": "route:r2.kind", "text": "THEORETICAL"}]
            )
        assert e.value.code == "ROUTE_ARENA_EDIT_FORBIDDEN_FIELD"

    def test_edit_recomputes_rubric_deterministically(self):
        content = RA.apply_corrections(
            self._content(),
            accept=[],
            reject=[],
            edits=[{"target": "route:r1.resources.computeHours", "number": 500}],
        )
        # 声明了小时数但没有预算基准 ⇒ MIXED（UNKNOWN 只留给"连估计都没有"）
        assert content["comparison"]["rubric"]["r1"]["resourceFit"]["level"] == "MIXED"
        assert content["comparison"]["rubric"]["r1"]["resourceFit"]["reason"] == "NO_BUDGET_DECLARED"

    def test_edit_unknown_target_rejected(self):
        with pytest.raises(RA.RouteArenaRejected) as e:
            RA.apply_corrections(self._content(), accept=[], reject=[], edits=[{"target": "route:r9.label", "text": "x"}])
        assert e.value.code == "ROUTE_ARENA_TARGET_UNKNOWN"

    def test_accept_records_acknowledgement(self):
        content = RA.apply_corrections(self._content(), accept=["route:r1", "route:r2"], reject=[], edits=[])
        assert content["editSummary"]["accepted"] == 2
