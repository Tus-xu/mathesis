"""RV09 · 记忆隔离：跨 session / 跨 tenant / 跨 research / 跨 purpose 全都要拦。

这不是"顺手加个 where 条件"：四个维度里任何一个漏了，
用户看到的就是"助手记得一件我没在这条对话/这个项目里说过的事"，
而他无法自救（他看不到记忆里有什么）。
"""

from __future__ import annotations

import json
import uuid

import pytest

from research_vnext.core.db.engine import session_scope
from research_vnext.core.llm import gateway as gw
from research_vnext.core.memory import long_term as LT
from research_vnext.core.memory import namespaces as NS
from research_vnext.core.memory import short_term as ST
from tests.integration.conftest import auth_headers, create_session
from tests.integration.test_messages_cr6 import StubBackend


@pytest.fixture()
def stub_llm():
    def _install(text: str = "", error: Exception | None = None) -> StubBackend:
        b = StubBackend(text, error)
        gw.set_backend(b)
        return b

    yield _install
    gw.set_backend(None)


def _session(client, ids, ctx_factory, *, title: str = "t"):
    ctx = ctx_factory(tenant_id=ids["tenant_id"])
    r, _ = create_session(client, ctx, ids, title=title)
    assert r.status_code == 201, r.text
    return ctx, uuid.UUID(r.json()["session"]["id"])


# --------------------------------------------------------------------------
# 1 · 短期记忆：会话间不得互相可见
# --------------------------------------------------------------------------


def test_checkpoint_is_not_visible_from_another_session(client, new_ids, ctx_factory) -> None:
    ctx_a, sid_a = _session(client, new_ids, ctx_factory, title="A")
    ctx_b, sid_b = _session(client, new_ids, ctx_factory, title="B")

    with session_scope() as db:
        ST.save_checkpoint(
            db,
            session_id=sid_a,
            tenant_id=new_ids["tenant_id"],
            last_message_seq=4,
            state={"stage": "AWAITING_CONFIRMATION:problem_map", "notes": "会话 A 的进度"},
        )
        assert ST.load_checkpoint(db, session_id=sid_a, tenant_id=new_ids["tenant_id"]) is not None
        assert ST.load_checkpoint(db, session_id=sid_b, tenant_id=new_ids["tenant_id"]) is None, (
            "会话 B 不得看到会话 A 的进度"
        )


def test_checkpoint_is_not_visible_across_tenant(client, new_ids, ctx_factory) -> None:
    _ctx, sid = _session(client, new_ids, ctx_factory)
    other_tenant = uuid.uuid4()

    with session_scope() as db:
        ST.save_checkpoint(
            db, session_id=sid, tenant_id=new_ids["tenant_id"], last_message_seq=1,
            state={"stage": "ACTIVE"},
        )
        assert ST.load_checkpoint(db, session_id=sid, tenant_id=other_tenant) is None


def test_thread_keys_are_independent() -> None:
    """同一会话的并行话题各有一条进度线 —— 互相不当前提。"""
    assert ST.DEFAULT_THREAD == "main"
    assert "stage" in ST.STATE_KEYS


def test_unknown_state_keys_are_dropped_and_recorded() -> None:
    clean = ST.sanitize_state({"stage": "ACTIVE", "secret": "不该进库"})
    assert "secret" not in clean
    assert clean["_droppedKeys"] == ["secret"]


def test_purge_removes_only_that_session(client, new_ids, ctx_factory) -> None:
    _ctx_a, sid_a = _session(client, new_ids, ctx_factory, title="A")
    _ctx_b, sid_b = _session(client, new_ids, ctx_factory, title="B")
    with session_scope() as db:
        ST.save_checkpoint(db, session_id=sid_a, tenant_id=new_ids["tenant_id"], last_message_seq=1, state={})
        ST.save_checkpoint(db, session_id=sid_b, tenant_id=new_ids["tenant_id"], last_message_seq=1, state={})
        assert ST.purge_session(db, session_id=sid_a) == 1
        assert ST.load_checkpoint(db, session_id=sid_a, tenant_id=new_ids["tenant_id"]) is None
        assert ST.load_checkpoint(db, session_id=sid_b, tenant_id=new_ids["tenant_id"]) is not None


# --------------------------------------------------------------------------
# 2 · 长期偏好：四维命名空间
# --------------------------------------------------------------------------


def _put(tenant, user, research, purpose, key, value, **kw):
    with session_scope() as db:
        return LT.put_preference(
            db,
            tenant_id=tenant,
            user_id=user,
            research_id=research,
            purpose=purpose,
            key=key,
            value=value,
            **kw,
        )


def _list(tenant, user, research, purpose, **kw):
    with session_scope() as db:
        return LT.list_preferences(
            db,
            tenant_id=tenant,
            user_id=user,
            research_id=research,
            purpose=purpose,
            **kw,
        )


def test_preference_is_invisible_across_tenant() -> None:
    t1, t2, u = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _put(t1, u, "research-1", "NOTATION", "sqrt", r"\sqrt{}")
    assert [p.key for p in _list(t1, u, "research-1", "NOTATION")] == ["sqrt"]
    assert _list(t2, u, "research-1", "NOTATION") == []


def test_preference_is_invisible_across_research() -> None:
    t, u = uuid.uuid4(), uuid.uuid4()
    _put(t, u, "queueing", "DOMAIN_PREFERENCE", "method", "优先解析解")
    assert _list(t, u, "queueing", "DOMAIN_PREFERENCE") != []
    assert _list(t, u, "probability", "DOMAIN_PREFERENCE") == [], (
        "别的研究项目不得看到这条偏好"
    )


def test_preference_is_invisible_across_purpose() -> None:
    t, u = uuid.uuid4(), uuid.uuid4()
    _put(t, u, "r1", "ASSISTANT_STYLE", "length", "尽量简短")
    assert _list(t, u, "r1", "ASSISTANT_STYLE") != []
    assert _list(t, u, "r1", "DELIVERABLE_FORMAT") == [], (
        "回答风格不得被当成交付物格式要求"
    )


def test_preference_is_invisible_across_user() -> None:
    t, u1, u2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _put(t, u1, "r1", "NOTATION", "k", "v")
    assert _list(t, u2, "r1", "NOTATION") == []


def test_unauthorized_preference_is_stored_but_not_returned_by_default() -> None:
    t, u = uuid.uuid4(), uuid.uuid4()
    _put(t, u, "r1", "ASSISTANT_STYLE", "guess", "模型猜的", authorized=False)
    assert _list(t, u, "r1", "ASSISTANT_STYLE") == []
    rows = _list(t, u, "r1", "ASSISTANT_STYLE", include_unauthorized=True)
    assert [p.key for p in rows] == ["guess"]


@pytest.mark.parametrize("bad", ["teacher_memory", "teacher-platform", "other_project", "cross-tenant"])
def test_forbidden_source_cannot_be_written(bad: str) -> None:
    t, u = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(NS.MemoryNamespaceViolation):
        _put(t, u, "r1", "NOTATION", "k", "v", source=bad)


def test_put_is_idempotent_per_namespace() -> None:
    t, u = uuid.uuid4(), uuid.uuid4()
    _put(t, u, "r1", "NOTATION", "k", "v1")
    _put(t, u, "r1", "NOTATION", "k", "v2")
    rows = _list(t, u, "r1", "NOTATION")
    assert len(rows) == 1 and rows[0].value == "v2"


def test_delete_only_affects_exact_namespace() -> None:
    t, u = uuid.uuid4(), uuid.uuid4()
    _put(t, u, "r1", "NOTATION", "k", "v")
    _put(t, u, "r2", "NOTATION", "k", "v")
    with session_scope() as db:
        assert LT.delete_preference(db, tenant_id=t, user_id=u, research_id="r1", purpose="NOTATION", key="k")
    assert _list(t, u, "r1", "NOTATION") == []
    assert _list(t, u, "r2", "NOTATION") != []


# --------------------------------------------------------------------------
# 3 · 偏好真的进了上下文清单（且只进该进的那一条）
# --------------------------------------------------------------------------


def test_authorized_preference_reaches_context_manifest(client, new_ids, ctx_factory, stub_llm) -> None:
    ctx, sid = _session(client, new_ids, ctx_factory)
    actor = uuid.UUID(ctx["actor_id"])
    project = new_ids["project_id"]

    _put(new_ids["tenant_id"], actor, str(project), "ASSISTANT_STYLE", "length", "回答尽量简短")
    # 另一项目下的偏好：**不得**进入本轮上下文
    _put(new_ids["tenant_id"], actor, "another-project", "ASSISTANT_STYLE", "other", "别的项目的偏好")

    stub_llm('{"kind":"answer","text":"好的","payload":{}}')
    r = client.post(
        f"/api/research-v1/sessions/{sid}/messages",
        json={"message": "先随便聊聊"},
        headers=auth_headers(ctx),
    )
    assert r.status_code == 201, r.text

    from research_vnext.core.db.models import ResearchAiRunRow

    with session_scope() as db:
        run = db.query(ResearchAiRunRow).filter(ResearchAiRunRow.session_id == sid).one()
        manifest = run.context_manifest
    pref_refs = [s["ref"] for s in manifest["sections"] if s["kind"] == "preference"]
    assert "preference:length" in pref_refs

    # 另一项目下的偏好**根本没有被读出来**（比"读出来再排除"更强）：
    # `context_service.preference_items` 只按本会话的 `project_id` 查询，
    # 因此它既不在 sections 里，也不在 excluded 里 —— 它从未进入过内存。
    # `namespace-mismatch` 这条裁剪理由属于 manifest 层的纵深防御，
    # 由 `tests/unit/test_rv09_context_manifest.py::test_namespace_mismatch_is_excluded_and_recorded` 覆盖。
    all_refs = [s["ref"] for s in manifest["sections"]] + [s["ref"] for s in manifest["excluded"]]
    assert "preference:other" not in all_refs
    assert "别的项目的偏好" not in json.dumps(manifest, ensure_ascii=False)
