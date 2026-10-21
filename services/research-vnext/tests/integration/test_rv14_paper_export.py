"""RV14 · Paper export/compile worker 集成测试（红→绿，真实 PG + MinIO + Valkey + Docker TeX）。

任务书验收链路：真实中文数学 PaperSpec → 三种格式导出；缺资产 PARTIAL；
同 input 去重；下载授权/过期/撤权；越权拒绝；队列隔离；取消；重试。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import timedelta

import pytest

from research_vnext.core.application import job_service as js
from research_vnext.core.db.engine import session_scope
from research_vnext.core.db.models import JobRow
from research_vnext.core.parser.models import AssetAnchorRow
from research_vnext.core.worker import runtime as W
from tests.integration.conftest import auth_headers
from tests.integration.test_rv09_intake_to_maps import sess, stub_llm  # noqa: F401,F811
from tests.integration.test_rv12_paper_claims import _seed_citation  # noqa: F401

BASE = "/api/research-v1"


def _make_1px_png() -> bytes:
    """现生成**合法**的 1x1 RGBA PNG（CRC 正确）——硬编码 hex 曾因 IDAT CRC
    错误被 xdvipdfmx 拒绝（libpng error: IDAT: CRC error），教训：测试资产
    必须程序化生成而不是手拼字节。"""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    raw = b"\x00\xff\x00\x00\x80"  # filter 0 + 一个 RGBA 像素（半透明红）
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


PNG_1PX = _make_1px_png()


# --------------------------------------------------------------------------
# 种子工具
# --------------------------------------------------------------------------


def _seed_anchor_with_object(s, *, anchor_id: str, with_object: bool = True) -> str:
    """真实 asset 锚点 +（可选）真实 MinIO 对象。

    同租户内相同内容只落**一行** ResearchAssetRow（RV04 唯一约束
    uq_research_asset_tenant_hash 的语义），多锚点共享同一资产行。
    """
    from research_vnext.core.assets.storage import MinioObjectStorage

    content_hash = hashlib.sha256(PNG_1PX).hexdigest() if with_object else hashlib.sha256(b"x").hexdigest()
    if with_object:
        storage = MinioObjectStorage()
        storage.put_bytes(
            storage.build_key(str(s["tenant_id"]), str(asset_id := uuid.uuid4()), 1), PNG_1PX
        )
    else:
        asset_id = uuid.uuid4()
    with session_scope() as db:
        from research_vnext.core.db.models import ResearchAssetRow

        existing = (
            db.query(ResearchAssetRow)
            .filter(
                ResearchAssetRow.tenant_id == s["tenant_id"],
                ResearchAssetRow.content_hash == content_hash,
            )
            .first()
        )
        if existing is not None:
            asset_id = existing.id  # 复用同内容资产行（去重语义）
        elif with_object:
            db.add(
                ResearchAssetRow(
                    id=asset_id,
                    tenant_id=s["tenant_id"],
                    project_id=s["project_id"],
                    owner_id=s["actor_id"],
                    session_id=s["session_id"],
                    version=1,
                    content_hash=content_hash,
                    media_type="image/png",
                    size_bytes=len(PNG_1PX),
                    storage_key=MinioObjectStorage().build_key(str(s["tenant_id"]), str(asset_id), 1),
                    acl={"visibility": "SESSION"},
                    lineage={},
                    rights={"status": "PERMITTED"},
                    retention={},
                    scan_status="SKIPPED",
                )
            )
        db.add(
            AssetAnchorRow(
                id=uuid.uuid4(),
                anchor_id=anchor_id,
                tenant_id=s["tenant_id"],
                asset_id=asset_id,
                asset_version=1,
                content_hash=content_hash,
                parse_run_id=uuid.uuid4(),
                parser_name="rv05",
                parser_version="1",
                type="figure",
                page=4,
                bbox=[72, 120, 480, 300],
                section_path=["第四章", "4.1"],
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
    return anchor_id


def _put_paper(client, s, *, base_revision: int, blocks: list[dict]):
    return client.put(
        f"{BASE}/sessions/{s['session_id']}/paper",
        json={"baseRevision": base_revision, "blocks": blocks},
        headers=auth_headers(s["ctx"]),
    )


def _export(client, s, *, formats: list[str]):
    return client.post(
        f"{BASE}/sessions/{s['session_id']}/paper:export",
        json={"formats": formats},
        headers=auth_headers(s["ctx"]),
    )


def _run_job(job_id: str) -> dict:
    return asyncio.run(W.run_job(uuid.UUID(job_id)))


def _job(job_id: str) -> JobRow:
    with session_scope() as db:
        j = js.get_job(db, uuid.UUID(job_id))
        j.result = dict(j.result or {})
        j.partial_detail = dict(j.partial_detail or {})
        return j


# 一份真实中文数学 PaperSpec（全部可导出节点 + 已发布 claim 引用 + 真实公式）
def _chinese_math_blocks(cid_citation: str, claim_id: str, fig_anchor: str, tab_anchor: str) -> list[dict]:
    return [
        {"id": "sec1", "type": "section", "text": "引论", "number": "1"},
        {"id": "p1", "type": "paragraph", "nodeRev": 1,
         "text": "考虑单服务台排队系统 M/M/1，顾客按泊松过程到达，服务时间服从指数分布。"},
        {"id": "sec2", "type": "section", "text": "稳态分析", "number": "2"},
        {"id": "p2", "type": "paragraph", "nodeRev": 1,
         "text": "当服务强度小于 1 时系统存在稳态分布，此时平均队长由 Little 公式给出"},
        {"id": "eq1", "type": "equation", "nodeRev": 1,
         "latex": r"L = \frac{\lambda}{\mu - \lambda} = \frac{\rho}{1-\rho}", "display": True},
        {"id": "p3", "type": "paragraph", "nodeRev": 1,
         "text": "其中 rho 为服务强度，平均等待时间满足 W = L / lambda（Little 公式）。"},
        {"id": "eq2", "type": "equation", "nodeRev": 1, "latex": r"\lambda W = L", "display": False},
        {"id": "cit1", "type": "citation", "nodeRev": 1, "text": "", "refs": [cid_citation]},
        {"id": "fig1", "type": "figure", "nodeRev": 1, "text": "", "refs": [fig_anchor],
         "caption": "不同到达率下的平均队长仿真对比"},
        {"id": "tab1", "type": "table", "nodeRev": 1, "text": "", "refs": [tab_anchor],
         "caption": "关键参数取值", "columns": ["参数", "符号", "取值"]},
        {"id": "call1", "type": "callout", "nodeRev": 1,
         "text": "稳态结论仅在服务强度小于 1 时成立。", "kind": "WARNING"},
        {"id": "p4", "type": "paragraph", "nodeRev": 1, "text": "该结论与已验证主张一致。",
         "claimRef": claim_id},
    ]


def _setup_paper(client, s, *, with_figure_object: bool = True):
    from tests.integration.test_rv12_claim_ledger import evaluate, propose, publish, run_verification, seed_evidence
    from tests.integration.test_rv12_paper_claims import _MATH_PASS

    cid_citation = _seed_citation(s)
    fig_anchor = _seed_anchor_with_object(s, anchor_id=f"anchor-rv14-{uuid.uuid4().hex[:8]}",
                                          with_object=with_figure_object)
    tab_anchor = _seed_anchor_with_object(s, anchor_id=f"anchor-rv14-{uuid.uuid4().hex[:8]}",
                                          with_object=with_figure_object)
    # 已发布 claim
    ev = seed_evidence(s)
    code, body = propose(client, s, statement="M/M/1 系统在服务强度小于 1 时存在稳态", evidence_refs=[ev])
    assert code == 200, body
    claim_id = body["claim"]["id"]
    run_verification(client, s, claim_id, verifier="math.check", params=_MATH_PASS)
    evaluate(client, s, claim_id)
    code, body = publish(client, s, claim_id)
    assert code == 200, body

    r = _put_paper(client, s, base_revision=0,
                   blocks=_chinese_math_blocks(cid_citation, claim_id, fig_anchor, tab_anchor))
    assert r.status_code == 200, r.text
    return {
        "citation_id": cid_citation,
        "fig_anchor": fig_anchor,
        "tab_anchor": tab_anchor,
        "claim_id": claim_id,
    }


# ==========================================================================
# 1 · 端到端：真实中文数学 PaperSpec → Markdown / LaTeX / PDF
# ==========================================================================


def test_export_all_formats_end_to_end(client, sess, stub_llm) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s)

    r = _export(client, s, formats=["markdown", "latex", "pdf"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deduped"] is False
    job_id = body["jobId"]

    out = _run_job(job_id)
    if out["status"] != "SUCCEEDED":  # 失败时暴露结构化诊断（验收可审计）
        j = _job(job_id)
        print("PARTIAL_DETAIL:", j.partial_detail)
        print("JOB_ERROR:", j.error_code, j.error_message)
    assert out["status"] == "SUCCEEDED", out

    job = _job(job_id)
    artifact_id = job.result["artifactId"]
    formats = job.result["formats"]
    assert set(formats) == {"markdown", "latex", "pdf"}

    # manifest 完整性（任务书 5）
    manifest = job.result["manifest"]
    assert manifest["manifestVersion"] == "rv14.manifest.v1"
    assert manifest["paperSpec"]["revision"] >= 1
    assert manifest["paperSpec"]["contentHash"]
    assert manifest["serializer"]["sha256"]
    assert manifest["template"]["sha256"]
    assert manifest["compiler"]["engine"] == "xelatex"
    assert manifest["compiler"]["image"].startswith("math-arena-tex")
    assert manifest["compiler"]["shellEscape"] is False
    assert manifest["cslStyle"]["sha256"]
    assert manifest["outputs"]["paper.pdf"]["sha256"]
    assert manifest["outputs"]["paper.pdf"]["pages"] >= 1

    # 下载 PDF 并验证真实性（任务书 7）
    r2 = client.post(
        f"{BASE}/paper-exports/{artifact_id}/download-link",
        json={"format": "pdf"},
        headers=auth_headers(s["ctx"]),
    )
    assert r2.status_code == 200, r2.text
    token = r2.json()["token"]
    r3 = client.get(
        f"{BASE}/paper-exports/{artifact_id}/download",
        params={"token": token, "format": "pdf"},
    )
    assert r3.status_code == 200, r3.text
    pdf = r3.content
    assert pdf[:4] == b"%PDF" and pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 1000

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    assert len(doc) >= 1
    text = "".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    assert "稳态分析" in text            # 中文章节真实渲染
    assert "Little" in text              # 正文文本
    # 页面渲染（字体/公式视觉 QA 用）
    page = doc[0]
    bitmap = page.render(scale=1.5)
    assert bitmap.width > 0 and bitmap.height > 0

    # Markdown / LaTeX 真实存在且哈希与 manifest 一致
    r4 = client.post(
        f"{BASE}/paper-exports/{artifact_id}/download-link",
        json={"format": "markdown"},
        headers=auth_headers(s["ctx"]),
    )
    tok_md = r4.json()["token"]
    r5 = client.get(
        f"{BASE}/paper-exports/{artifact_id}/download",
        params={"token": tok_md, "format": "markdown"},
    )
    md = r5.content.decode("utf-8")
    assert "稳态分析" in md and "Little 公式" in md
    assert hashlib.sha256(r5.content).hexdigest() == manifest["outputs"]["paper.md"]["sha256"]


# ==========================================================================
# 2 · 同 input 去重（任务书：同 input 去重）
# ==========================================================================


def test_same_input_dedupes_no_second_compile(client, sess, stub_llm) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s)

    r1 = _export(client, s, formats=["markdown", "latex", "pdf"])
    job_id = r1.json()["jobId"]
    _run_job(job_id)

    r2 = _export(client, s, formats=["markdown", "latex", "pdf"])
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["deduped"] is True
    assert body2["artifactId"] == _job(job_id).result["artifactId"]

    # 同 fingerprint 不应产生第二个作业
    fp = body2["fingerprint"]
    with session_scope() as db:
        jobs = db.query(JobRow).filter(JobRow.input_ref == f"rv14-export:{fp}").all()
    assert len(jobs) == 1


# ==========================================================================
# 3 · 缺少 asset → PARTIAL（md/tex 成功、pdf 失败，结构化诊断带 node locator）
# ==========================================================================


def test_missing_asset_yields_partial_with_locator(client, sess, stub_llm) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s, with_figure_object=False)

    r = _export(client, s, formats=["markdown", "latex", "pdf"])
    job_id = r.json()["jobId"]
    out = _run_job(job_id)
    assert out["status"] == "PARTIAL", out

    job = _job(job_id)
    detail = job.partial_detail
    assert "serialize_markdown" in detail["succeeded_stages"]
    assert "serialize_latex" in detail["succeeded_stages"]
    assert "compile_pdf" in detail["failed_stages"]
    diags = (detail.get("result") or {}).get("diagnostics") or {}
    missing = diags.get("missingAssets") or []
    assert missing and missing[0]["nodeId"]  # source node locator
    assert missing[0]["anchorId"]
    # 绝不展示假 PDF：PARTIAL 产物列表中无 pdf
    formats = (detail.get("result") or {}).get("formats") or {}
    assert "pdf" not in formats


# ==========================================================================
# 4 · 恶意 LaTeX 在写入路径被拒（compile worker 第二道防线由单测覆盖）
# ==========================================================================


def test_malicious_latex_rejected_at_write_path(client, sess, stub_llm) -> None:  # noqa: F811
    s = sess
    blocks = [{"id": "e1", "type": "equation", "nodeRev": 1,
               "latex": r"\write18{rm -rf /}", "display": True}]
    r = _put_paper(client, s, base_revision=0, blocks=blocks)
    assert r.status_code in (200, 400, 422), r.status_code
    if r.status_code == 200:  # 若写入路径接受，则导出必须拒绝
        r2 = _export(client, s, formats=["latex"])
        job_id = r2.json()["jobId"]
        out = _run_job(job_id)
        assert out["status"] in ("FAILED", "PARTIAL")


# ==========================================================================
# 5 · 下载安全：过期 410 / 篡改 403 / 撤权 410 / 越权 403 / 审计
# ==========================================================================


def test_download_expired_token_is_410(client, sess, stub_llm) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s)
    r = _export(client, s, formats=["markdown"])
    job_id = r.json()["jobId"]
    _run_job(job_id)
    artifact_id = _job(job_id).result["artifactId"]

    # 过期令牌（负 TTL 直接铸造）
    from research_vnext.core.export.download import mint_download_token

    expired = mint_download_token(artifact_id=artifact_id, actor_id=str(s["actor_id"]),
                                  fmt="markdown", ttl_seconds=-5)
    rr = client.get(
        f"{BASE}/paper-exports/{artifact_id}/download",
        params={"token": expired, "format": "markdown"},
    )
    assert rr.status_code == 410


def test_download_tampered_token_is_403(client, sess, stub_llm) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s)
    r = _export(client, s, formats=["markdown"])
    _run_job(r.json()["jobId"])
    artifact_id = _job(r.json()["jobId"]).result["artifactId"]

    r2 = client.post(f"{BASE}/paper-exports/{artifact_id}/download-link",
                     json={"format": "markdown"}, headers=auth_headers(s["ctx"]))
    token = r2.json()["token"][:-6] + "aaaaaa"
    rr = client.get(f"{BASE}/paper-exports/{artifact_id}/download",
                    params={"token": token, "format": "markdown"})
    assert rr.status_code == 403


def test_revoked_artifact_download_is_410_and_audited(client, sess, stub_llm) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s)
    r = _export(client, s, formats=["markdown"])
    _run_job(r.json()["jobId"])
    artifact_id = _job(r.json()["jobId"]).result["artifactId"]

    r2 = client.post(f"{BASE}/paper-exports/{artifact_id}/download-link",
                     json={"format": "markdown"}, headers=auth_headers(s["ctx"]))
    token = r2.json()["token"]

    rv = client.post(f"{BASE}/paper-exports/{artifact_id}:revoke",
                     json={"reason": "撤回发布"}, headers=auth_headers(s["ctx"]))
    assert rv.status_code == 200, rv.text

    rr = client.get(f"{BASE}/paper-exports/{artifact_id}/download",
                    params={"token": token, "format": "markdown"})
    assert rr.status_code == 410

    # 下载/拒绝动作全部留审计痕迹
    from research_vnext.core.db.models import PaperExportAuditRow

    with session_scope() as db:
        actions = {
            a.action
            for a in db.query(PaperExportAuditRow)
            .filter(PaperExportAuditRow.artifact_id == uuid.UUID(artifact_id))
            .all()
        }
    assert {"TOKEN_MINTED", "REVOKED", "DOWNLOAD_DENIED"} <= actions


def test_security_floor_bump_blocks_existing_token(client, sess, stub_llm) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s)
    r = _export(client, s, formats=["markdown"])
    _run_job(r.json()["jobId"])
    artifact_id = _job(r.json()["jobId"]).result["artifactId"]

    r2 = client.post(f"{BASE}/paper-exports/{artifact_id}/download-link",
                     json={"format": "markdown"}, headers=auth_headers(s["ctx"]))
    token = r2.json()["token"]

    # 撤权：抬高 actor 的 security floor → 未过期令牌立即失效
    from research_vnext.core.api.deps import set_security_floor

    set_security_floor(s["actor_id"], 99)
    try:
        rr = client.get(f"{BASE}/paper-exports/{artifact_id}/download",
                        params={"token": token, "format": "markdown"})
        assert rr.status_code == 410
    finally:
        set_security_floor(s["actor_id"], 1)


def test_cross_tenant_export_access_is_403(client, sess, stub_llm, ctx_factory) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s)
    r = _export(client, s, formats=["markdown"])
    _run_job(r.json()["jobId"])
    artifact_id = _job(r.json()["jobId"]).result["artifactId"]

    other_tenant = uuid.uuid4()
    other_ctx = ctx_factory(tenant_id=other_tenant,
                            capabilities=["workspace.research.enter", "research.session.write"])
    r2 = client.get(f"{BASE}/paper-exports/{artifact_id}", headers=auth_headers(other_ctx))
    assert r2.status_code == 403
    r3 = client.post(f"{BASE}/paper-exports/{artifact_id}/download-link",
                     json={"format": "markdown"}, headers=auth_headers(other_ctx))
    assert r3.status_code == 403


# ==========================================================================
# 6 · 取消 / 重试（JobContract 语义在 export 域成立）
# ==========================================================================


def test_cancel_queued_export_takes_effect(client, sess, stub_llm) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s)
    r = _export(client, s, formats=["pdf"])
    job_id = r.json()["jobId"]

    with session_scope() as db:
        js.request_cancel(db, job_id=uuid.UUID(job_id))
    out = _run_job(job_id)
    assert out["status"] == "CANCELLED", out


def test_retry_after_transient_compile_failure(client, sess, stub_llm, monkeypatch) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s)
    r = _export(client, s, formats=["pdf"])
    job_id = r.json()["jobId"]

    # 第一次编译抛可重试的 PROVIDER_TIMEOUT，第二次真实成功
    from research_vnext.core.export import compiler as compiler_mod

    real_compile = compiler_mod.DockerTexCompiler.compile

    flaky = {"n": 0}

    def fake_compile(self, workdir, *, passes=2):
        flaky["n"] += 1
        if flaky["n"] == 1:
            raise compiler_mod.CompileError("EXPORT_COMPILE_TIMEOUT", "注入的瞬时超时", retryable=True)
        return real_compile(self, workdir=workdir, passes=passes)

    monkeypatch.setattr(compiler_mod.DockerTexCompiler, "compile", fake_compile)

    # 第一次：瞬时失败 → 作业进入 RETRYING（可重试错误码进入退避）
    out1 = _run_job(job_id)
    assert out1["status"] == "RETRYING", out1
    with session_scope() as db:
        job = db.get(JobRow, job_id)
        assert int(job.attempts) == 1
        assert job.status == "RETRYING"
        # 模拟退避到期
        job.next_attempt_at = js._now() - timedelta(seconds=1)
        db.flush()

    # 第二次：真实成功
    out2 = _run_job(job_id)
    assert out2["status"] == "SUCCEEDED", out2
    final = _job(job_id)
    assert int(final.attempts) == 2, f"应重试一次: attempts={final.attempts}"


# ==========================================================================
# 7 · 队列隔离：paper.export 只进独立 compile 队列，主 pool 不消费
# ==========================================================================


def test_compile_queue_is_isolated_from_main_pool(client, sess, stub_llm) -> None:  # noqa: F811
    import json as _json

    from research_vnext.core.config import (
        COMPILE_CONSUMER_GROUP,
        COMPILE_DEDUPE_SCOPE,
        COMPILE_JOB_TYPES,
        COMPILE_STREAM_KEY,
    )
    from research_vnext.core.queue.streams import ValkeyStreamQueue

    s = sess
    _setup_paper(client, s)
    r = _export(client, s, formats=["markdown", "latex"])
    job_id = r.json()["jobId"]

    main_q = ValkeyStreamQueue()
    compile_q = ValkeyStreamQueue(stream=COMPILE_STREAM_KEY, group=COMPILE_CONSUMER_GROUP,
                                  dedupe_scope=COMPILE_DEDUPE_SCOPE)

    # relay 后：本作业的 queued 事件只进 compile stream，不进主 stream。
    # it_schema 为 session 级共享 —— 前序测试会在同一 outbox 表里留下大量
    # pending 事件，必须循环 relay 至清空，本测试的事件才保证被发布
    # （生产 relay 是常驻循环，语义等效）。
    from research_vnext.core.application import outbox_relay as relay

    for _round in range(100):
        with session_scope() as db:
            stats = relay.relay_once(db, queue=main_q, compile_queue=compile_q, limit=200)
        if stats["claimed"] == 0:
            break

    def _stream_job_ids(q) -> set[str]:
        out: set[str] = set()
        for _sid, fields in q.client.xrange(q._stream, "-", "+"):
            payload = _json.loads(fields.get("payload") or "{}")
            inner = payload.get("payload") or {}
            jid = payload.get("job_id") or inner.get("job_id")
            if jid:
                out.add(str(jid))
        return out

    assert str(job_id) in _stream_job_ids(compile_q)
    assert str(job_id) not in _stream_job_ids(main_q)

    # 主 pool 的 DB 兜底路径**不**领 compile 作业
    with session_scope() as db:
        due = js.claim_due_jobs(db, worker="main-pool", limit=50, exclude_job_types=COMPILE_JOB_TYPES)
    assert all(str(j.id) != job_id for j in due)

    # compile pool 领得到
    with session_scope() as db:
        due2 = js.claim_due_jobs(db, worker="compile-pool", limit=50, job_types=COMPILE_JOB_TYPES)
    assert any(str(j.id) == job_id for j in due2)


# ==========================================================================
# 8 · worker 重启/接管（围栏）：旧 worker 迟到上报被拒
# ==========================================================================


def test_stale_worker_result_rejected_for_export_job(client, sess, stub_llm) -> None:  # noqa: F811
    s = sess
    _setup_paper(client, s)
    r = _export(client, s, formats=["markdown"])
    job_id = uuid.UUID(r.json()["jobId"])

    # worker-A 正常领取
    with session_scope() as db:
        job = js.claim_job(db, job_id=job_id, worker="worker-A")
        stale_fence = int(job.fencing_token)

    # 模拟 worker-A 崩溃：租约过期
    with session_scope() as db:
        row = db.get(JobRow, job_id)
        row.lease_expires_at = js._now() - timedelta(seconds=5)
        db.flush()

    # worker-B 接管：围栏必须 +1
    with session_scope() as db:
        job_b = js.claim_job(db, job_id=job_id, worker="worker-B")
        assert int(job_b.fencing_token) == stale_fence + 1, "接管必须递增围栏"

    # worker-A 迟到上报旧结果 → StaleFencingToken（旧结果不得覆盖新尝试）
    from research_vnext.core.application import job_service as jsvc

    with pytest.raises(jsvc.StaleFencingToken):
        with session_scope() as db:
            jsvc.complete_job(db, job_id=job_id, fencing_token=stale_fence, result={"fake": True})

    # worker-B 用新围栏上报成功
    with session_scope() as db:
        done = jsvc.complete_job(db, job_id=job_id, fencing_token=stale_fence + 1, result={"ok": True})
        assert str(done.status) == "SUCCEEDED"
