"""RV06 integration · 固定评测集上的检索指标（Recall@k / nDCG@k / locator 可回源率）。

评测集来自 `tests/fixtures/federation/eval/queries.jsonl`（一次性由
`scripts/export_federation_eval.py` 从**真实** Teacher direct-chunk 与真实 Crossref
记录生成）。本文件只读它 —— 指标因此可复现、可跨轮次比较。

三条设计纪律：

1. **不硬编码查询特例**：评分只做集合运算；检索路径（`scoring` / `dedupe` /
   orchestrator）里没有任何按查询字面量分支的代码，由静态守卫测试断言。
2. **可回源率单独计量**：一条结果"排得高"不等于"点得开"。这里对每条返回的
   locator 真的回源查一次（Teacher chunk / research DB 行），算比例。
3. 指标是**记录**，不是可以靠放宽判据刷高分的 KPI：判据（`relevant`）固定在 fixture 里。
"""

from __future__ import annotations

import inspect
import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text

from research_vnext.core.api.deps import AccessContext
from research_vnext.core.assets import models as asset_models
from research_vnext.core.db.engine import session_scope
from research_vnext.core.federation import dedupe as dedupe_mod
from research_vnext.core.federation import orchestrator as orch_mod
from research_vnext.core.federation import scoring as scoring_mod
from research_vnext.core.federation.contracts import Budget, FederatedQuery
from research_vnext.core.federation.orchestrator import federated_search
from research_vnext.core.federation.providers.foundational_math import (
    build_readonly_engine,
)
from research_vnext.core.federation.providers.registry import build_providers
from research_vnext.core.federation.scope import resolve_scope
from research_vnext.core.federation.types import EvidenceType, WarnCode
from research_vnext.core.literature.models import LiteratureRecordRow

SERVICE_ROOT = Path(__file__).resolve().parents[2]
EVAL_PATH = SERVICE_ROOT / "tests" / "fixtures" / "federation" / "eval" / "queries.jsonl"

TOP_K = 10

#: 与 fixture 判据一致的种子数据（真实 Crossref 记录）。
SEED_LITERATURE = [
    {
        "doi": "10.1038/nature14539",
        "title": "Deep learning",
        "venue": "Nature",
        "fingerprint": "1" * 64,
    },
    {
        "doi": "10.48550/arxiv.1706.03762",
        "title": "Attention is all you need",
        "venue": "NeurIPS",
        "fingerprint": "2" * 64,
    },
]
SEED_ASSETS = ["函数与导数专题.pdf", "立体几何复习讲义.pdf"]


def _locator_unit(discriminator: str) -> str:
    """把判据串折叠到**定位单元**。

    教材：`textbook:<src>:<sec>:<chunk>` → `textbook:<src>:<sec>`（section 级）。
    文献/资产：判据本就唯一，原样返回。
    """
    if discriminator.startswith("textbook:"):
        return ":".join(discriminator.split(":")[:3])
    return discriminator


def load_cases() -> list[dict]:
    assert EVAL_PATH.exists(), f"评测集缺失: {EVAL_PATH}"
    return [
        json.loads(line)
        for line in EVAL_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --------------------------------------------------------------------------
# 指标
# --------------------------------------------------------------------------


def dedupe_units(items: list[str]) -> list[str]:
    """按定位单元去重，保留首次出现位置。

    为什么必须去重：折叠到 section 级后，同一节的多个 chunk 会映射到同一个单元。
    `nDCG` 若对重复单元重复计分，会出现 **>1.0 的不可能值**（首轮实测 fm-02
    得到 4.54）；`precision` 同理会被重复项污染。
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    ranked = dedupe_units(retrieved)[:k]
    dcg = sum(1.0 / math.log2(i + 2) for i, item in enumerate(ranked) if item in relevant)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / ideal if ideal else 0.0


def _discriminator(r) -> str:  # noqa: ANN001
    """把结果映射回 fixture 的判据串（维度不同，判据形态不同）。"""
    if r.evidence_type is EvidenceType.FOUNDATIONAL_MATH:
        loc = r.locator
        return f"textbook:{loc.source_id}:{loc.section_id}:{loc.chunk_id}"
    if r.evidence_type is EvidenceType.LITERATURE:
        return f"doi:{r.locator.doi}"
    if r.evidence_type is EvidenceType.PROJECT_ASSET:
        # 资产判据用文件名：AssetLocator 只承载 id/hash，文件名在 snippet 里
        return f"filename:{r.snippet.split('（')[0].strip()}"
    return f"other:{r.result_id}"


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def eval_seed(app_env, foundational_dsn):  # noqa: ARG001
    """在当前 schema 内种入与评测判据一致的文献/资产行。"""
    from tests.conftest import make_access_context

    project_id = uuid.uuid4()
    # 携带签名 project scope：收窄后 project 维度只能来自 AccessContext
    c = make_access_context(
        resource_scopes=[{"kind": "project", "id": str(project_id), "role": "owner"}]
    )
    ctx = AccessContext(
        actor_id=uuid.UUID(c["actor_id"]),
        tenant_id=uuid.UUID(c["tenant_id"]),
        workspace="research",
        domain="research-vnext",
        capabilities=list(c["capabilities"]),
        resource_scopes=list(c["resource_scopes"]),
    )
    with session_scope() as db:
        for i, lit in enumerate(SEED_LITERATURE):
            db.add(
                LiteratureRecordRow(
                    id=uuid.uuid4(),
                    tenant_id=ctx.tenant_id,
                    project_id=project_id,
                    owner_id=ctx.actor_id,
                    provider="crossref",
                    provider_version="v1",
                    provider_record_id=lit["doi"],
                    request_id=f"rv06-eval-{i}",
                    query=lit["title"],
                    doi=lit["doi"],
                    title=lit["title"],
                    authors=[{"name": "eval-seed"}],
                    published_date="2015-01-01",
                    venue=lit["venue"],
                    abstract_available="YES",
                    fulltext_available="UNKNOWN",
                    rights_status="UNKNOWN",
                    rights_detail={},
                    source_url=f"https://doi.org/{lit['doi']}",
                    retrieved_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
                    raw_response_hash="9" * 64,
                    fingerprint=lit["fingerprint"],
                    record_kind="METADATA_ONLY",
                )
            )
        for i, name in enumerate(SEED_ASSETS):
            db.add(
                asset_models.ResearchAssetRow(
                    id=uuid.uuid4(),
                    tenant_id=ctx.tenant_id,
                    project_id=project_id,
                    owner_id=ctx.actor_id,
                    version=1,
                    content_hash=f"{i + 3:064d}",
                    media_type="application/pdf",
                    claimed_media_type="application/pdf",
                    size_bytes=10240,
                    original_filename=name,
                    storage_backend="minio",
                    storage_key=f"rvs/{ctx.tenant_id}/{name}/v1",
                    acl={"owner": str(ctx.actor_id), "readers": [], "public": False},
                    lineage={"upload_source": "rv06-eval"},
                    rights={"status": "PUBLIC", "license": "MIT", "notes": "eval seed"},
                    retention={"policy": "default", "expires_at": None},
                    scan_status="CLEAN",
                    scan_detail={},
                    created_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
                )
            )
    return {"ctx": ctx, "project_id": project_id}


def _scope(ctx: AccessContext, project_id, allow):
    return resolve_scope(
        actor_id=str(ctx.actor_id),
        tenant_id=str(ctx.tenant_id),
        project_ids={str(project_id)},
        session_id=None,
        school_id=None,
        capabilities=ctx.capabilities,
        provider_allowlist=allow,
    )


# --------------------------------------------------------------------------
# 用例：评测集完整性
# --------------------------------------------------------------------------


def test_eval_set_is_frozen_and_dimension_tagged():
    cases = load_cases()
    assert len(cases) >= 10
    dims = {c["dimension"] for c in cases}
    assert dims == {"FOUNDATIONAL_MATH", "LITERATURE", "PROJECT_ASSET"}
    for c in cases:
        assert c["query_text"].strip()
        assert c["relevant"], f"{c['query_id']} 没有判据"
        assert all(isinstance(x, str) and x for x in c["relevant"])
    # 判据不得是"万能通配"
    assert not any("*" in x for c in cases for x in c["relevant"])


# --------------------------------------------------------------------------
# 用例：指标
# --------------------------------------------------------------------------


def test_retrieval_eval_metrics_on_fixed_set(eval_seed, foundational_dsn):  # noqa: ARG001
    cases = load_cases()
    rows: list[dict] = []

    dsn = os.environ.get("RESEARCH_FOUNDATIONAL_DATABASE_URL")
    resolvable_total = 0
    resolvable_ok = 0
    injection_marked = 0
    scope_violations = 0

    with session_scope() as db:
        assets = {
            row.original_filename
            for row in db.execute(select(asset_models.ResearchAssetRow)).scalars()
        }
        lit_dois = {
            row.doi
            for row in db.execute(select(LiteratureRecordRow)).scalars()
            if row.doi
        }

        teacher = build_readonly_engine(dsn) if dsn else None
        chunk_ids: set[str] = set()
        if teacher is not None:
            with teacher.connect() as conn:
                chunk_ids = {
                    str(r[0])
                    for r in conn.execute(text("SELECT id FROM textbook_chunks"))
                }

        for case in cases:
            allow = {
                "FOUNDATIONAL_MATH": ("foundational_math",),
                "LITERATURE": ("literature",),
                "PROJECT_ASSET": ("project_asset",),
            }[case["dimension"]]
            ctx = eval_seed["ctx"]
            project_id = eval_seed["project_id"]
            query = FederatedQuery(
                actor_id=str(ctx.actor_id),
                tenant_id=str(ctx.tenant_id),
                project_id=str(project_id),
                session_id=None,
                purpose="retrieval_eval",
                provider_allowlist=allow,
                filters={},
                budget=Budget(max_results=TOP_K, max_provider_calls=2, cost_ceiling=0.0),
                deadline_ms=30_000,
                trace_id=uuid.uuid4().hex[:32],
                query_text=case["query_text"],
                strategy="federated_hybrid_v1",
            )
            resp = federated_search(
                query,
                scope=_scope(ctx, project_id, allow),
                providers=build_providers(db, foundational_database_url=dsn or ""),
            )
            retrieved_disc = [_discriminator(r) for r in resp.results]
            # 相关性判据在**定位单元**上比较：教材的定位单元是 section
            # （用户要找的是"这一节"），chunk 级精度由 locator 可回源率与
            # precision 分项单独计量。若按 chunk 级算 Recall@10，一节有 41 个
            # chunk 时理论上限就只有 10/41，指标会失真成"章节大小"的函数。
            retrieved = dedupe_units([_locator_unit(d) for d in retrieved_disc])
            relevant = {_locator_unit(x) for x in case["relevant"]}
            precision = (
                sum(1 for d in retrieved if d in relevant) / len(retrieved)
                if retrieved
                else 0.0
            )

            # 可回源率：逐条 locator 真的回源验证
            for r in resp.results:
                resolvable_total += 1
                ok = False
                if r.evidence_type is EvidenceType.FOUNDATIONAL_MATH:
                    ok = r.locator.chunk_id in chunk_ids
                elif r.evidence_type is EvidenceType.LITERATURE:
                    ok = r.locator.doi in lit_dois
                elif r.evidence_type is EvidenceType.PROJECT_ASSET:
                    name = r.snippet.split("（")[0].strip()
                    ok = name in assets
                resolvable_ok += int(ok)
                if r.has_warning(WarnCode.INJECTION_PATTERN_DETECTED):
                    injection_marked += 1
                if r.has_warning(WarnCode.SCOPE_VIOLATION):
                    scope_violations += 1

            rows.append(
                {
                    "query_id": case["query_id"],
                    "dimension": case["dimension"],
                    "query_text": case["query_text"],
                    "recall_at_10": round(recall_at_k(retrieved, relevant, TOP_K), 4),
                    "ndcg_at_10": round(ndcg_at_k(retrieved, relevant, TOP_K), 4),
                    "precision_at_10": round(precision, 4),
                    "returned": len(retrieved),
                    "status": resp.status.value,
                }
            )

    by_dim: dict[str, list[dict]] = {}
    for row in rows:
        by_dim.setdefault(row["dimension"], []).append(row)

    summary = {
        "top_k": TOP_K,
        "cases": len(rows),
        "locator_resolvable_rate": round(resolvable_ok / resolvable_total, 4)
        if resolvable_total
        else 0.0,
        "locator_total": resolvable_total,
        # 真实数据上是否出现被标记为注入的结果 —— INJECTION-RESULTS.md 的声称
        # 必须由这个计数支撑，而不是靠推断。
        "results_marked_injection": injection_marked,
        "results_marked_scope_violation": scope_violations,
        "by_dimension": {
            dim: {
                "n": len(items),
                "mean_recall_at_10": round(
                    sum(i["recall_at_10"] for i in items) / len(items), 4
                ),
                "mean_ndcg_at_10": round(
                    sum(i["ndcg_at_10"] for i in items) / len(items), 4
                ),
                "mean_precision_at_10": round(
                    sum(i["precision_at_10"] for i in items) / len(items), 4
                ),
            }
            for dim, items in sorted(by_dim.items())
        },
        "cases_detail": rows,
    }
    # 评测结果如实打印，供 RETRIEVAL-EVAL.md 引用（不做美化）
    print("RV06-RETRIEVAL-EVAL " + json.dumps(summary, ensure_ascii=False, sort_keys=True))

    # 断言：真实下限（不是"看起来不错"的口号）。
    # 口径已折叠到定位单元（教材 = section），因此这些下限衡量的是
    # "能否定位到正确的节/文献/资产"，而不是"能否覆盖整节全部 chunk"。
    # 阈值取在"明显退化即失败"的位置；实际值一律记入 RETRIEVAL-EVAL.md。
    fm = summary["by_dimension"]["FOUNDATIONAL_MATH"]
    assert fm["mean_recall_at_10"] >= 0.5, f"教材维度 Recall@10 过低: {fm}"
    assert fm["mean_ndcg_at_10"] >= 0.4, f"教材维度 nDCG@10 过低: {fm}"
    for dim in ("LITERATURE", "PROJECT_ASSET"):
        assert summary["by_dimension"][dim]["mean_recall_at_10"] == 1.0, (
            f"{dim} 判据唯一，必须完全命中: {summary['by_dimension'][dim]}"
        )
    assert summary["locator_resolvable_rate"] == 1.0, (
        f"存在无法回源的 locator: {summary['locator_resolvable_rate']}"
    )
    # 真实语料上不应出现注入标记：出现即说明要么数据被污染，要么检测误杀。
    # 两种情况都需要人看，不能默默放过。
    assert summary["results_marked_injection"] == 0, (
        f"真实语料出现注入标记: {summary['results_marked_injection']}"
    )
    assert summary["results_marked_scope_violation"] == 0, (
        f"评测结果出现越界标记: {summary['results_marked_scope_violation']}"
    )
    assert all(r["status"] == "OK" for r in rows), "评测中出现非 OK 状态"


# --------------------------------------------------------------------------
# 用例：禁止查询特例
# --------------------------------------------------------------------------


def test_retrieval_path_has_no_query_specific_special_casing():
    """静态守卫：检索/评分/重排路径不得出现按查询字面量特判的代码。"""
    banned = (
        "deep learning",
        "transformer",
        "向量的数乘",
        "余弦定理",
        "if query ==",
        "if query_text ==",
        "hardcode",
        "SpecialCase",
    )
    for mod in (scoring_mod, dedupe_mod, orch_mod):
        src = inspect.getsource(mod)
        for token in banned:
            assert token not in src, f"{mod.__name__} 出现查询特例痕迹: {token!r}"

    eval_src = Path(__file__).read_text(encoding="utf-8")
    # 评测实现自身也不得针对某个 query_id / 某个查询文本走不同分支。
    # 被检 token 必须**拼接**构造，否则守卫自己的列表就会命中自己。
    qid = "query" + "_id"
    dash = "-"
    suspicious = [f'case["{qid}"] ==', f'"{qid}"] ==']
    suspicious += [f'== "{prefix}{dash}' for prefix in ("fm", "lit", "pa")]
    for token in suspicious:
        assert token not in eval_src, f"评测实现出现按查询特判的痕迹: {token!r}"
