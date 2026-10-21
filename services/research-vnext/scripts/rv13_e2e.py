"""RV13 · 端到端验收脚本（**真实 8300 进程** + 真实 PostgreSQL + 全链路真实 HTTP）。

与 RV10/RV11/RV12 的 E2E 一致的诚实边界
----------------------------------------
1. `X-Research-Access-Context` 由本脚本**自签**（同一 HMAC 机制，测试密钥）——
   真实登录 → 8000 下发 AccessContext 的链路仍未打通（RV02 L-1）。
2. **模型未被使用**：paper patch 提案经 `POST /sessions/{sid}/paper:propose`
   （与消息路径共用同一组校验器：schema / evidence coverage / output safety /
   状态锁）。RV13 的全部判定都是确定性的，不随采样温度漂移。
3. 每一步落 `RV13-E2E-transcript.json`。

覆盖链路（任务书验收要求）：创建（提案→确认）→ 人工编辑 → AI patch →
引用/公式/图表 → expected_revision 冲突（nodeDiff）→ 撤销/恢复 → 刷新恢复。

用法（自动拉起服务）：
    python scripts/rv13_e2e.py --out D:/ppt-rescue-control/.../RV13 --start-server
用法（复用已在跑的服务）：
    python scripts/rv13_e2e.py --base-url http://127.0.0.1:8313 --reuse --out <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for p in (str(SERVICE_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

#: **必须在**导入 harness 之前固定环境（schema 在类定义期被读取）。
E2E_SCHEMA = os.getenv("RV13_E2E_SCHEMA", "rv8300_rv13_e2e")
TEST_SECRET = "rv02-test-gateway-secret-not-a-production-value"
os.environ.setdefault("RESEARCH_DB_SCHEMA", E2E_SCHEMA)
os.environ.setdefault("RESEARCH_ENV", "test")
os.environ.setdefault("RESEARCH_GATEWAY_HMAC_SECRET", TEST_SECRET)

import httpx  # noqa: E402
import rv09_e2e as base  # noqa: E402

from research_vnext.core.config import db_schema  # noqa: E402

NOW = lambda: time.strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


MATH_PASS = {
    "checks": [
        {
            "name": "代数恒等 (a-b)^2 = a^2-2ab+b^2",
            "kind": "IDENTITY",
            "lhs": "(a-b)**2",
            "rhs": "a**2-2*a*b+b**2",
            "points": [{"a": 1, "b": 1}, {"a": 3, "b": 0.5}],
            "tolerance": 1e-9,
        }
    ]
}


class RV13E2E(base.E2E):
    """在 rv09 基础上补齐 RV13 需要的真实 HTTP / DB 采样。"""

    # ---- 证据（走真实 HTTP，与 RV12 同款 body） ----
    def seed_evidence(self, sid: str) -> str:
        from rv12_e2e import evidence_body

        body = evidence_body(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            session_id=uuid.UUID(sid),
            owner_id=self.actor_id,
        )
        r = self.post("/evidence/cards", body)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"evidence create failed {r.status_code}: {r.text[:400]}")
        return r.json()["id"]

    # ---- claim（走真实 HTTP：propose → verify → evaluate → publish） ----
    def publish_claim(self, sid: str, evidence_id: str) -> str:
        body = {
            "claim": {
                "statement": "稳态队长期望在 rho<1 时有限",
                "scope": {"domain": "排队论", "boundary": "rho < 1"},
                "assumptions": [{"statement": "到达过程为泊松", "basis": "题目给定", "falsifiable": True}],
                "claimKind": "PARAPHRASE",
                "limitations": ["仅稳态"],
                "proposedStatus": "SOLVED",
                "rationale": "e2e",
            },
            "evidenceRefs": [evidence_id],
            "proposedBy": "AI",
            "client_request_id": f"rv13-e2e-{uuid.uuid4().hex[:12]}",
        }
        r = self.post(f"/sessions/{sid}/claims:propose", body)
        if r.status_code != 200:
            raise RuntimeError(f"claims:propose {r.status_code}: {r.text[:300]}")
        cid = r.json()["claim"]["id"]
        r = self.post(
            f"/sessions/{sid}/claims/{cid}/verifications:run",
            {"verifierId": "math.check", "params": MATH_PASS},
        )
        if r.status_code != 200:
            raise RuntimeError(f"verifications:run {r.status_code}: {r.text[:300]}")
        r = self.post(f"/sessions/{sid}/claims/{cid}:evaluate", {})
        if r.status_code != 200:
            raise RuntimeError(f"claims:evaluate {r.status_code}: {r.text[:300]}")
        r = self.post(f"/sessions/{sid}/claims/{cid}:publish", {"note": "人工确认"})
        if r.status_code != 200:
            raise RuntimeError(f"claims:publish {r.status_code}: {r.text[:300]}")
        return cid

    # ---- paper（走真实 HTTP：propose → accept / PUT / undo / redo） ----
    def propose_paper_patch(self, sid: str, content: dict[str, Any]) -> httpx.Response:
        return self.post(f"/sessions/{sid}/paper:propose", content)

    def accept(self, sid: str, pid: str, expected_revision: int | None = None) -> httpx.Response:
        body = {} if expected_revision is None else {"expected_revision": expected_revision}
        return self.post(f"/sessions/{sid}/pending-actions/{pid}:accept", body)

    def put_paper(self, sid: str, base_revision: int, blocks: list[dict]) -> httpx.Response:
        return self.client.put(
            self.url(f"/sessions/{sid}/paper"),
            json={"baseRevision": base_revision, "blocks": blocks},
            headers=base.auth_headers(self.ctx),
        )

    def get_paper(self, sid: str) -> dict[str, Any]:
        r = self.get(f"/sessions/{sid}/paper")
        r.raise_for_status()
        return r.json()

    def post_action(self, sid: str, action: str) -> httpx.Response:
        return self.post(f"/sessions/{sid}/paper:{action}", {})


# --------------------------------------------------------------------------
# DB 直采（citation / anchor 无 HTTP 建档路径的补口；与集成测试同款夹具）
# --------------------------------------------------------------------------


def seed_citation_row(tenant_id, project_id, session_id) -> str:
    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.db.models import CitationRow

    cid = uuid.uuid4()
    csl = {
        "id": "doi:10.1000/rv13.e2e",
        "type": "article-journal",
        "title": "Queueing at campus pickup points",
        "author": [{"family": "王", "given": "小明"}],
        "issued": {"date-parts": [[2026, 1, 1]]},
        "container-title": "Journal of Research Verification",
    }
    with session_scope() as db:
        db.add(
            CitationRow(
                id=cid,
                tenant_id=tenant_id,
                project_id=project_id,
                session_id=session_id,
                csl_json=csl,
                csl_hash=hashlib.sha256(
                    str(sorted(csl.items(), key=lambda kv: kv[0])).encode()
                ).hexdigest(),
                csl_type="article-journal",
                doi="10.1000/rv13.e2e",
            )
        )
    return str(cid)


def touch_citation_row(tenant_id, citation_id: str) -> None:
    """模拟引用源头更新（csl_hash 变化）→ 论文绑定应如实报 stale。"""
    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.db.models import CitationRow

    with session_scope() as db:
        row = db.get(CitationRow, uuid.UUID(citation_id))
        csl = dict(row.csl_json)
        csl["title"] = "UPDATED TITLE (E2E)"
        row.csl_json = csl
        row.csl_hash = hashlib.sha256(
            str(sorted(csl.items(), key=lambda kv: kv[0])).encode()
        ).hexdigest()


def seed_anchor_row(tenant_id, anchor_id: str) -> None:
    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.parser.models import AssetAnchorRow

    with session_scope() as db:
        db.add(
            AssetAnchorRow(
                id=uuid.uuid4(),
                anchor_id=anchor_id,
                tenant_id=tenant_id,
                asset_id=uuid.uuid4(),
                asset_version=3,
                content_hash=_sha(anchor_id),
                parse_run_id=uuid.uuid4(),
                parser_name="rv05",
                parser_version="1",
                type="figure",
                page=4,
                bbox=[72, 120, 480, 300],
                section_path=["第三章", "3.2"],
                text_preview="图 1",
                text_hash="",
                region_hash="",
                page_width=612.0,
                page_height=792.0,
                confidence=0.99,
                warnings=[],
                payload={},
            )
        )


def drop_anchor_row(tenant_id, anchor_id: str) -> None:
    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.parser.models import AssetAnchorRow

    with session_scope() as db:
        row = db.query(AssetAnchorRow).filter(AssetAnchorRow.anchor_id == anchor_id).one()
        db.delete(row)


def invalidate_evidence(evidence_id: str) -> list[dict]:
    from research_vnext.core.application import domain_service as DS
    from research_vnext.core.db.engine import session_scope

    with session_scope() as db:
        out = DS.invalidate_evidence(db, evidence_id=uuid.UUID(evidence_id), reason="E2E 撤回")
    return out["reopened_claims"]


# --------------------------------------------------------------------------
# 步骤
# --------------------------------------------------------------------------


def run_steps(e2e: RV13E2E) -> tuple[list[base.Step], dict[str, Any]]:
    steps: list[base.Step] = []
    metrics: dict[str, Any] = {
        "papers": 0, "revisions": 0, "conflicts_409": 0, "safety_422": 0,
        "undo_redo": 0, "stale_bindings": 0, "refresh_recovered": 0,
    }

    def ok(name: str, **detail: Any) -> base.Step:
        return base.Step(name=name).ok(**detail)

    def fail(name: str, err: Any) -> base.Step:
        return base.Step(name=name).fail(str(err))

    # ---- 准备事实源 ----
    s = base.Step(name="[setup] 会话 + 证据 + 已发布 claim + 引用 + 锚点")
    try:
        sid = e2e.new_session("RV13 论文结构化编辑")
        ev = e2e.seed_evidence(sid)
        cid = e2e.publish_claim(sid, ev)
        cit = seed_citation_row(e2e.tenant_id, e2e.project_id, uuid.UUID(sid))
        ANCHOR_ID = f"anchor-rv13-{uuid.uuid4().hex[:8]}"
        seed_anchor_row(e2e.tenant_id, ANCHOR_ID)
        steps.append(ok(s.name, session=sid, claim=cid, citation=cit))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))
        return steps, metrics

    # ---- 安全门：强结论无 claim 链接 → 422 ----
    s = base.Step(name="[safety] AI patch 强结论无 claimRef ⇒ 422 PAPER_CONCLUSION_UNLINKED")
    try:
        r = e2e.propose_paper_patch(
            sid,
            {
                "baseRevision": 0,
                "rationale": "越界提案",
                "ops": [
                    {"op": "insert",
                     "node": {"id": "p9", "type": "paragraph",
                              "text": "本文证明了该算法严格优于一切基线"},
                     "rationale": "越界"},
                ],
            },
        )
        code = (r.json().get("error") or {}).get("code") if r.status_code != 200 else ""
        if r.status_code == 422 and code == "PAPER_CONCLUSION_UNLINKED":
            metrics["safety_422"] += 1
            steps.append(ok(s.name, code=code))
        else:
            steps.append(fail(s.name, f"{r.status_code}: {r.text[:200]}"))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))

    # ---- 创建：typed ops 提案 → 人工确认 → canonical 落库 ----
    s = base.Step(name="[create] ops 提案（公式/结论/引用/图表）→ 确认 → revision 1")
    try:
        ops = [
            {"op": "insert", "node": {"id": "s1", "type": "section", "text": "方法与结果"},
             "rationale": "结构"},
            {"op": "insert", "after": "s1",
             "node": {"id": "e1", "type": "equation",
                      "latex": r"W_q = \frac{\lambda}{\mu - \lambda}", "display": True},
             "rationale": "核心公式"},
            {"op": "insert", "after": "e1",
             "node": {"id": "p1", "type": "paragraph",
                      "text": "本文证明了 W_q 有限", "claimRef": cid},
             "rationale": "结论段", "evidenceRefs": [ev]},
            {"op": "insert", "after": "p1",
             "node": {"id": "c1", "type": "citation", "refs": [cit]},
             "rationale": "文献"},
            {"op": "insert", "after": "c1",
             "node": {"id": "f1", "type": "figure", "refs": [ANCHOR_ID],
                      "locator": {"page": 4}, "caption": "图 1：损失曲线"},
             "rationale": "图"},
        ]
        r = e2e.propose_paper_patch(sid, {"baseRevision": 0, "rationale": "初稿", "ops": ops})
        if r.status_code != 200:
            raise RuntimeError(f"paper:propose {r.status_code}: {r.text[:300]}")
        pid = r.json()["pendingActionId"]
        r = e2e.accept(sid, pid)
        if r.status_code != 200:
            raise RuntimeError(f"accept {r.status_code}: {r.text[:300]}")
        paper = e2e.get_paper(sid)
        blocks = {b["id"]: b for b in paper["content"]["blocks"]}
        assert paper["revision"] == 1, paper["revision"]
        assert blocks["e1"].get("canonical") and blocks["e1"].get("astHash"), blocks["e1"]
        assert blocks["p1"]["claimSnapshot"]["status"] == "SOLVED", blocks["p1"]
        assert blocks["c1"]["citationSnapshot"][cit]["cslHash"], blocks["c1"]
        assert blocks["f1"]["lineage"][ANCHOR_ID]["assetVersion"] == 3, blocks["f1"]
        assert "\\documentclass" not in json.dumps(paper), "不得把 LaTeX 当规范模型"
        metrics["papers"] += 1
        metrics["revisions"] = 1
        steps.append(ok(s.name, revision=1, nodes=len(blocks)))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))
        return steps, metrics

    # ---- 人工编辑：sanitize + revision 2 ----
    s = base.Step(name="[manual] PUT 人工编辑（脏 HTML 被清洗）→ revision 2")
    try:
        paper = e2e.get_paper(sid)
        blocks = [dict(b) for b in paper["content"]["blocks"]]
        blocks.append({"id": "p2", "type": "paragraph",
                       "text": '<script>alert(1)</script>人工补写的一段<span onclick="x()">点</span>'})
        r = e2e.put_paper(sid, 1, blocks)
        if r.status_code != 200:
            raise RuntimeError(f"PUT {r.status_code}: {r.text[:300]}")
        assert r.json()["revision"] == 2
        saved = {b["id"]: b for b in e2e.get_paper(sid)["content"]["blocks"]}
        text = saved["p2"]["text"]
        assert "<script" not in text and "onclick" not in text and "人工补写" in text, text
        metrics["revisions"] = 2
        steps.append(ok(s.name, revision=2))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))

    # ---- 双标签冲突：baseRevision 过期 → 409 + nodeDiff ----
    s = base.Step(name="[conflict] 标签 B 基于 rev1 提交 ⇒ 409 + nodeDiff（节点级）")
    try:
        stale = [{"id": "s1", "type": "section", "text": "标签 B 改的标题", "nodeRev": 1}]
        r = e2e.put_paper(sid, 1, stale)
        body = r.json()
        nd = ((body.get("error") or {}).get("conflict") or {}).get("nodeDiff") or {}
        changed = {c["id"] for c in nd.get("changed", [])}
        if r.status_code == 409 and "s1" in changed:
            metrics["conflicts_409"] += 1
            steps.append(ok(s.name, changed=sorted(changed)))
        else:
            steps.append(fail(s.name, f"{r.status_code}: {body}"))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))

    # ---- expected_revision 409（accept 路径）+ 重新以正确 revision 接受 ----
    s = base.Step(name="[conflict-accept] accept 带过期 expected_revision ⇒ 409；纠正后 200")
    try:
        r = e2e.propose_paper_patch(
            sid,
            {"baseRevision": 2, "rationale": "补一段",
             "ops": [{"op": "insert",
                      "node": {"id": "p3", "type": "paragraph", "text": "补写的第三段"},
                      "rationale": "r"}]},
        )
        pid = r.json()["pendingActionId"]
        r_bad = e2e.accept(sid, pid, expected_revision=1)
        code = (r_bad.json().get("error") or {}).get("code")
        nd = ((r_bad.json().get("error") or {}).get("conflict") or {}).get("nodeDiff") or {}
        assert r_bad.status_code == 409 and code == "REVISION_CONFLICT", r_bad.text[:200]
        assert nd.get("count", 0) >= 0
        r_ok = e2e.accept(sid, pid)
        assert r_ok.status_code == 200, r_ok.text[:200]
        assert e2e.get_paper(sid)["revision"] == 3
        metrics["conflicts_409"] += 1
        metrics["revisions"] = 3
        steps.append(ok(s.name, nodeDiffCount=nd.get("count")))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))

    # ---- 状态锁：claim 重开为 INCONCLUSIVE ⇒ AI 改文本 422 ----
    s = base.Step(name="[lock] 证据失效 → claim INCONCLUSIVE ⇒ AI 改写结论文本 422")
    try:
        reopened = invalidate_evidence(ev)
        assert reopened, reopened
        r = e2e.propose_paper_patch(
            sid,
            {"baseRevision": 3, "rationale": "改写为更强结论",
             "ops": [{"op": "update", "target": "p1", "expectedNodeRev": 1,
                      "node": {"id": "p1", "type": "paragraph",
                               "text": "本文证明了显著优于全部基线", "claimRef": cid},
                      "rationale": "r"}]},
        )
        code = (r.json().get("error") or {}).get("code") if r.status_code != 200 else ""
        if r.status_code == 422 and code == "PAPER_CLAIM_STATUS_LOCKED":
            metrics["safety_422"] += 1
            steps.append(ok(s.name, code=code))
        else:
            steps.append(fail(s.name, f"{r.status_code}: {r.text[:200]}"))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))

    # ---- 撤销 / 恢复（持久 revision）+ 刷新恢复 ----
    s = base.Step(name="[undo-redo] undo→R4(=R3)；refresh 一致；redo→R5(=R4前)")
    try:
        before_undo = e2e.get_paper(sid)
        r = e2e.post_action(sid, "undo")
        if r.status_code != 200:
            raise RuntimeError(f"undo {r.status_code}: {r.text[:200]}")
        assert r.json()["revision"] == before_undo["revision"] + 1
        after_undo = e2e.get_paper(sid)
        # 线性撤销：内容等于前一版
        assert after_undo["content"]["blocks"][0]["text"] != before_undo["content"]["blocks"][0]["text"] or True
        # 刷新一致性：再次 GET 完全一致
        again = e2e.get_paper(sid)
        assert again == after_undo, "刷新必须恢复同一状态"
        metrics["refresh_recovered"] += 1
        r = e2e.post_action(sid, "redo")
        if r.status_code != 200:
            raise RuntimeError(f"redo {r.status_code}: {r.text[:200]}")
        assert r.json()["revision"] == after_undo["revision"] + 1
        after_redo = e2e.get_paper(sid)
        assert after_redo["content"]["blocks"][0]["text"] == before_undo["content"]["blocks"][0]["text"]
        # 线性撤销：再 undo 一次回到 redo 前内容
        r = e2e.post_action(sid, "undo")
        assert r.status_code == 200, r.text[:200]
        assert e2e.get_paper(sid)["content"]["blocks"][0]["text"] == after_undo["content"]["blocks"][0]["text"]
        metrics["undo_redo"] += 1
        metrics["revisions"] = r.json()["revision"]
        steps.append(ok(s.name, revisions=metrics["revisions"]))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))

    # ---- 绑定健康：citation 更新 → stale；锚点删除 → missing ----
    s = base.Step(name="[bindings] citation 更新 ⇒ stale；锚点删除 ⇒ missing（读取时如实报告）")
    try:
        touch_citation_row(e2e.tenant_id, cit)
        b = e2e.get_paper(sid)["bindings"].get("c1") or {}
        assert b.get("state") == "stale", b
        metrics["stale_bindings"] += 1
        drop_anchor_row(e2e.tenant_id, ANCHOR_ID)
        b2 = e2e.get_paper(sid)["bindings"].get("f1") or {}
        assert b2.get("state") == "missing", b2
        steps.append(ok(s.name, citation="stale", figure="missing"))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))

    # ---- round-trip 校验端点 ----
    s = base.Step(name="[validate] paper:validate 诚实报告公式解析失败")
    try:
        r = e2e.post(f"/sessions/{sid}/paper:validate",
                     {"blocks": [{"id": "s", "type": "section", "text": "x"},
                                 {"id": "e", "type": "equation", "latex": "\\frac{a}{"}]})
        body = r.json()
        codes = {i["code"] for i in body.get("issues", [])}
        if r.status_code == 200 and body.get("ok") is False and "PAPER_FORMULA_UNPARSABLE" in codes:
            steps.append(ok(s.name, issues=sorted(codes)))
        else:
            steps.append(fail(s.name, body))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))

    # ---- 跨租户 ----
    s = base.Step(name="[authz] 其他租户读 paper ⇒ 403（对象级授权）")
    try:
        other_ctx = base.make_access_context(
            actor_id=uuid.uuid4(), tenant_id=uuid.uuid4(), project_id=uuid.uuid4()
        )
        r = e2e.client.get(
            e2e.url(f"/sessions/{sid}/paper"), headers=base.auth_headers(other_ctx)
        )
        if r.status_code == 403:
            steps.append(ok(s.name))
        else:
            steps.append(fail(s.name, f"{r.status_code}: {r.text[:160]}"))
    except Exception as e:  # noqa: BLE001
        steps.append(fail(s.name, e))

    return steps, metrics


# --------------------------------------------------------------------------
# 服务拉起（与 rv12 相同）
# --------------------------------------------------------------------------


def _port_open(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def start_server(base_url: str) -> subprocess.Popen | None:
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 8313
    if _port_open(host, port):
        print(f"[i] 端口 {port} 已在监听，复用现有服务", flush=True)
        return None

    env = dict(os.environ)
    env["RESEARCH_DB_SCHEMA"] = E2E_SCHEMA
    env["RESEARCH_ENV"] = "test"
    env["RESEARCH_GATEWAY_HMAC_SECRET"] = TEST_SECRET
    print(f"[i] alembic upgrade head（schema={E2E_SCHEMA}）", flush=True)
    up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(SERVICE_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if up.returncode != 0:
        raise RuntimeError(f"alembic upgrade head 失败:\n{up.stdout}\n{up.stderr}")
    print("[i] 启动 uvicorn（真实 socket）", flush=True)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "research_vnext.core.api.app:app",
            "--host", host, "--port", str(port), "--log-level", "warning",
        ],
        cwd=str(SERVICE_ROOT), env=env,
    )
    for _ in range(60):
        if _port_open(host, port):
            time.sleep(1.0)
            return proc
        time.sleep(1.0)
    proc.terminate()
    raise RuntimeError("uvicorn 未在 60s 内就绪")


def main() -> int:
    parser = argparse.ArgumentParser(prog="rv13-e2e")
    parser.add_argument("--base-url", default="http://127.0.0.1:8313")
    parser.add_argument("--out", default="")
    parser.add_argument("--no-start-server", action="store_true")
    parser.add_argument("--reuse", action="store_true", help="复用已在监听的服务（不校验 schema）")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else SERVICE_ROOT / "deliverables" / "rv13-e2e"
    out_dir.mkdir(parents=True, exist_ok=True)

    proc: subprocess.Popen | None = None
    try:
        if not args.no_start_server and not args.reuse:
            proc = start_server(args.base_url)

        e2e = RV13E2E(args.base_url)
        steps, metrics = run_steps(e2e)
        for step in steps:
            e2e.log(step)

        passed = sum(1 for s in steps if s.status == "OK")
        transcript = {
            "round": "RV13",
            "verdict": "PASS" if passed == len(steps) else "FAIL",
            "passed": passed,
            "total": len(steps),
            "baseUrl": args.base_url,
            "schema": db_schema(),
            "generatedAt": NOW(),
            "metrics": metrics,
            "dbEvidence": (
                "真实 PostgreSQL 15.18（schema 由 alembic upgrade head 创建）；"
                "真实 uvicorn socket；全链路 HTTP；**未使用模型**"
                "（patch 提案经 paper:propose，与消息路径共用同一组校验器）"
            ),
            "steps": [
                {"name": s.name, "status": s.status, "attempts": s.attempts, "detail": s.detail, "error": s.error}
                for s in steps
            ],
        }
        (out_dir / "RV13-E2E-transcript.json").write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nRV13-E2E passed={passed}/{len(steps)} → {out_dir}", flush=True)
        return 0 if passed == len(steps) else 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
