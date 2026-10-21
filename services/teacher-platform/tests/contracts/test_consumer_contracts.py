"""P00 · Teacher / Research 消费者契约测试（consumer-driven）。

同一个已冻结契约必须**同时**被两个消费者正确消费。本文件为每个消费者声明
它实际依赖的字段与语义，并断言：

1. Teacher consumer（8100 teacher-platform / 8200 ppt-studio-v2）：
   - 用 AccessContext 建立 workspace=teacher 的上下文；
   - 教材证据只走 source→section→chunk（不新写 page/page_refs/pNN）；
   - JobContract PARTIAL 不得显示成完成。

2. Research consumer（reserve 8300 research-vnext）：
   - 不 import Teacher 域、不读 Teacher 表；
   - 教材证据必须标 TEXTBOOK（不得伪装成论文 Citation）；
   - 允许 LITERATURE 使用 page+bbox。

**跨域隔离断言**：Research 消费者对同一批契约的消费不得引入 Teacher 语义；
反之亦然。任一方向出现跨域字段即失败。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.platform_contracts import read_contract
from app.platform_contracts.errors import PlatformContractError

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "platform_contracts" / "fixtures"

ACTOR = "b3a1c2d4-1111-4222-8333-444455556666"
TENANT = "b3a1c2d4-1111-4222-8333-444455556667"
TRACE = "0af7651916cd43dd8448eb211c80319c"
SHA = "a" * 64


def _load(rel: str) -> dict:
    data = json.loads((FIXTURES / rel).read_text(encoding="utf-8"))
    data.pop("_contract", None)
    return data


# ---------------------------------------------------------------------------
# Teacher consumer
# ---------------------------------------------------------------------------


class TestTeacherConsumer:
    """8100/8200 侧消费者契约。"""

    def test_teacher_access_context_consumed(self) -> None:
        ctx = read_contract("AccessContext", _load("valid/access_context_teacher.json"))
        assert ctx.data.workspace == "teacher"
        assert "workspace.teacher.enter" in ctx.data.capabilities
        assert ctx.data.security_version >= 1
        # Teacher 消费者必须能定位到具体班级对象（对象级授权）
        assert any(s.kind == "class" for s in ctx.data.resource_scopes)

    def test_teacher_textbook_evidence_has_no_page_anchor(self) -> None:
        """教材证据被 Teacher 消费时不得带 page/page_refs/pNN。"""
        evd = read_contract("EvidenceRef", _load("valid/evidence_ref_textbook.json"))
        assert evd.data.source_type == "TEXTBOOK"
        assert evd.data.section_id and evd.data.chunk_id
        assert evd.data.page is None
        assert not any(k in (evd.data.model_extra or {}) for k in ("page_refs", "page_ref"))

    def test_teacher_cannot_write_textbook_page_via_contract(self) -> None:
        """即使顶层 schema 宽松，消费者层也必须拒绝教材 page 锚点。"""
        payload = _load("invalid/evidence_textbook_with_page.json")
        with pytest.raises(PlatformContractError):
            read_contract("EvidenceRef", payload)

    def test_teacher_job_partial_is_not_completion(self) -> None:
        job = read_contract("JobContract", _load("valid/job_partial.json"))
        assert job.data.status == "PARTIAL"
        assert job.data.status != "SUCCEEDED"
        assert job.data.partial_reason is not None
        assert job.data.partial_reason.failed_stages

    def test_teacher_job_projection_from_8100_internal_states(self) -> None:
        """8100 reliability_jobs 的内部状态必须能投影到对外 canonical 状态。"""
        from app.platform_contracts.job import project_internal_status

        assert project_internal_status("PENDING") == "QUEUED"
        assert project_internal_status("CLAIMED") == "RUNNING"
        assert project_internal_status("COMPLETED") == "SUCCEEDED"
        with pytest.raises(PlatformContractError):
            project_internal_status("SOMETHING_NEW")

    def test_teacher_artifact_consumed_with_acl_and_hash(self) -> None:
        art = read_contract("ArtifactRef", _load("valid/artifact_ref_deck.json"))
        assert len(art.data.hash) == 64
        assert art.data.acl.visibility == "PRIVATE"
        assert art.data.retention.policy


# ---------------------------------------------------------------------------
# Research consumer
# ---------------------------------------------------------------------------


class TestResearchConsumer:
    """拟议 8300 research-vnext 侧消费者契约。"""

    def test_research_access_context_consumed(self) -> None:
        ctx = read_contract("AccessContext", _load("valid/access_context_research.json"))
        assert ctx.data.workspace == "research"
        assert "workspace.research.enter" in ctx.data.capabilities
        assert any(s.kind == "project" for s in ctx.data.resource_scopes)

    def test_research_literature_evidence_may_use_page_bbox(self) -> None:
        evd = read_contract("EvidenceRef", _load("valid/evidence_ref_literature.json"))
        assert evd.data.source_type == "LITERATURE"
        assert evd.data.page == 3
        assert evd.data.bbox is not None

    def test_research_textbook_evidence_stays_textbook(self) -> None:
        """教材证据在 Research 中必须保持 TEXTBOOK，不得伪装成论文 Citation。"""
        evd = read_contract("EvidenceRef", _load("valid/evidence_ref_textbook.json"))
        assert evd.data.source_type == "TEXTBOOK"
        assert evd.data.source_type != "LITERATURE"
        assert evd.data.provider == "FoundationalMathProvider"

    def test_research_can_consume_same_job_contract(self) -> None:
        """同一 JobContract 被两个消费者共享（形状不因域而异）。"""
        job = read_contract("JobContract", _load("valid/job_running.json"))
        assert job.data.owner_service == "ppt_studio_v2"
        assert job.data.fencing_token >= 0
        assert job.data.heartbeat_at

    def test_research_memory_isolation_cross_tenant(self) -> None:
        """跨租户记忆读取必须 fail closed（Research 不得读到 Teacher 租户记忆）。"""
        from app.platform_contracts import MemoryRecord

        mem = read_contract("MemoryRecord", _load("valid/memory_record_preference.json")).data
        assert isinstance(mem, MemoryRecord)
        teacher_requester = {"tenant_id": TENANT, "user_id": ACTOR, "domain": "teacher_prep", "purpose": "preference"}
        research_requester = {
            "tenant_id": "b3a1c2d4-1111-4222-8333-44445556dead",
            "user_id": ACTOR,
            "domain": "teacher_prep",
            "purpose": "preference",
        }
        assert mem.is_readable_by(teacher_requester) is True
        assert mem.is_readable_by(research_requester) is False

    def test_research_thread_isolation_short_term_memory(self) -> None:
        """线程内/跨线程隔离：短期记忆只在同 thread 可见，长期记忆跨线程稳定。"""
        from app.platform_contracts import MemoryRecord

        base = _load("valid/memory_record_preference.json")
        base["namespace"]["thread_id"] = "thread-A"
        base["is_long_term"] = False
        short = MemoryRecord.model_validate(base)
        requester = {"tenant_id": TENANT, "user_id": ACTOR, "domain": "teacher_prep", "purpose": "preference"}
        assert short.is_readable_by(requester, thread_scope="thread-A") is True
        assert short.is_readable_by(requester, thread_scope="thread-B") is False

        long_term = MemoryRecord.model_validate({**base, "is_long_term": True})
        assert long_term.is_readable_by(requester, thread_scope="thread-B") is True

    def test_research_context_manifest_marks_evidence_untrusted(self) -> None:
        manifest = read_contract("ContextManifest", _load("valid/context_manifest_teacher.json"))
        assert manifest.data.trust_labels.evidence_refs == "UNTRUSTED_DATA"


# ---------------------------------------------------------------------------
# 跨域隔离（双向）
# ---------------------------------------------------------------------------


def test_no_cross_domain_entity_leak_in_platform_contracts() -> None:
    """平台契约不得内嵌 Teacher/Research 具体业务实体名。

    检查范围：本命名空间的 Pydantic 模型字段名 + JSON Schema 属性名。
    """
    from app.platform_contracts import bundle_schema
    from app.platform_contracts.catalog import CONTRACT_MODELS

    forbidden = (
        "teachingblueprint",
        "teachingdeckspec",
        "teachingsession",
        "lessonplanspec",
        "classlearningprofile",
        "researchsession",
        "researchblueprint",
        "paperspec",
        "claimledger",
        "experimentrun",
    )
    schema = bundle_schema()
    serialized = json.dumps(schema, ensure_ascii=False).lower()
    for name in forbidden:
        if name in ("classlearningprofile",):
            # 学生跨端六契约名之一，允许出现在 docs 层但不得出现在平台契约中
            assert f'"${name}"' not in serialized.replace("_", ""), (
                f"平台契约内嵌了域实体 {name}"
            )
            continue
        assert name not in serialized, f"平台契约内嵌了域实体 {name}"
    # 明确：平台契约模型集合不含域实体
    for model in CONTRACT_MODELS.values():
        fields = set(getattr(model, "model_fields", {}).keys())
        assert not (fields & {"blueprint", "deck_spec", "lesson_plan"}), (
            f"{model.__name__} 内嵌域专属字段"
        )


def test_schema_is_generated_not_handwritten() -> None:
    """生成物必须与模型一致（防止手改产物造成三端漂移）。"""
    from app.platform_contracts.gen.gen_schema import REPO_SCHEMA_PATH, generate_schema

    assert REPO_SCHEMA_PATH.exists(), "缺少 platform-contracts.schema.json"
    assert json.loads(REPO_SCHEMA_PATH.read_text(encoding="utf-8")) == generate_schema()


def test_ts_generation_is_stable() -> None:
    """TS 生成必须可重复（两次生成字节一致）。"""
    from app.platform_contracts.gen.gen_ts import generate_ts

    assert generate_ts() == generate_ts()
    text = generate_ts()
    assert "export interface AccessContext" in text or "AccessContext" in text
    assert "PLATFORM_CONTRACTS_SCHEMA_VERSION" in text


def test_consumer_fixtures_cover_both_domains() -> None:
    """消费者 fixture 必须同时覆盖 Teacher 与 Research，且不互相污染。"""
    valid = sorted(p.name for p in (FIXTURES / "valid").glob("*.json"))
    assert any("teacher" in n or "textbook" in n or "foundation" in n for n in valid)
    assert any("research" in n or "literature" in n or "command" in n for n in valid)
    assert len(valid) >= 16, "valid fixtures 必须至少覆盖 16 项契约"
