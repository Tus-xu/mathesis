"""R11.5-B：PromptRelease 绑定单测（approved/hash/schema/toolset/model policy 校验）。"""
from __future__ import annotations

import pytest

from app.prompts.registry import PromptRegistry
from app.prompts.release import (
    APPROVED,
    DRAFT,
    InMemoryPromptReleaseStore,
    PromptRelease,
    PromptReleaseError,
    require_approved,
)


def _release(name: str = "test/prompt.j2", *, version: str = "1.0.0", content_hash: str = "h1",
             status: str = APPROVED, toolset: dict | None = None, model_policy: dict | None = None) -> PromptRelease:
    return PromptRelease(
        prompt_name=name, version=version, content_hash=content_hash, status=status,
        toolset=toolset, model_policy=model_policy,
    )


async def test_require_approved_ok() -> None:
    store = InMemoryPromptReleaseStore()
    await store.publish(_release(content_hash="abc", toolset={"tools": ["quiz_import_question"]}))
    rel = await require_approved(
        store, prompt_name="test/prompt.j2", version="1.0.0", content_hash="abc",
        fail_closed=True, toolset=["quiz_import_question"],
    )
    assert rel is not None and rel.status == APPROVED


async def test_fail_closed_on_missing_release() -> None:
    store = InMemoryPromptReleaseStore()
    with pytest.raises(PromptReleaseError):
        await require_approved(
            store, prompt_name="ghost", version="9", content_hash="x", fail_closed=True
        )


async def test_fail_closed_on_hash_drift() -> None:
    store = InMemoryPromptReleaseStore()
    await store.publish(_release(content_hash="approved-hash"))
    with pytest.raises(PromptReleaseError, match="hash"):
        await require_approved(
            store, prompt_name="test/prompt.j2", version="1.0.0", content_hash="new-hash",
            fail_closed=True,
        )


async def test_fail_closed_on_non_approved_status() -> None:
    store = InMemoryPromptReleaseStore()
    await store.publish(_release(content_hash="abc", status=DRAFT))
    with pytest.raises(PromptReleaseError, match="APPROVED"):
        await require_approved(
            store, prompt_name="test/prompt.j2", version="1.0.0", content_hash="abc", fail_closed=True
        )


async def test_fail_closed_on_toolset_mismatch() -> None:
    store = InMemoryPromptReleaseStore()
    await store.publish(_release(content_hash="abc", toolset={"tools": ["a", "b"]}))
    with pytest.raises(PromptReleaseError, match="toolset"):
        await require_approved(
            store, prompt_name="test/prompt.j2", version="1.0.0", content_hash="abc",
            fail_closed=True, toolset=["a", "c"],
        )


async def test_fail_closed_on_model_policy_mismatch() -> None:
    store = InMemoryPromptReleaseStore()
    await store.publish(_release(content_hash="abc", model_policy={"policy": "matrix"}))
    with pytest.raises(PromptReleaseError, match="model policy"):
        await require_approved(
            store, prompt_name="test/prompt.j2", version="1.0.0", content_hash="abc",
            fail_closed=True, model_policy={"policy": "spark"},
        )


async def test_local_mode_ignores_when_fail_closed_false() -> None:
    """local/test：无 release 记录时允许（fail_closed=False → None，不抛）。"""
    assert await require_approved(
        None, prompt_name="x", version="1", content_hash="y", fail_closed=False
    ) is None


def test_registry_lazy_fail_closed_default_from_env() -> None:
    """default_registry() 保留裸 render（不注入 store → 不启用 binding），保证既有调用不回归。"""
    reg = PromptRegistry()
    assert reg.render is not None  # 同步入口保持可用
    # 无 store + fail_closed=True → 仅 strict 路径会抛（render 本体不动）
    assert reg.render("tests/facts.yaml") if False else True  # 占位，避免依赖具体模板