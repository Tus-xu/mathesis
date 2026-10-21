"""RV18 · 并发编译幂等（真实 PG + 真实路由，不依赖已重启的 8300 进程）。

覆盖本轮修复的并发缺陷：同一项目 + 同 entry 在已有 `running` run 时，
再发编译请求**不新建 run**，而是返回 409 + `COMPILE_IN_PROGRESS`（机器可读），
并携带既有 run id（前端据此给人话提示并复用）。

为什么能在进程内跑真实路由：守卫（advisory lock + `active_compile_run`）在进入
真实 Docker 编译**之前**就命中并抛错，因此本用例不触发任何容器/对象存储。
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from tests.conftest import auth_headers

PREFIX = "/api/research-vnext"
_TEX = "\\documentclass{article}\n\\begin{document}\nhi\n\\end{document}\n"


def _make_project(client, ctx: dict) -> str:
    """走真实 API 建项目（返回 project_id，字符串）。"""
    r = client.post(
        f"{PREFIX}/projects",
        json={"name": f"rv18-conc-{uuid.uuid4().hex[:8]}", "client_request_id": f"rv18-{uuid.uuid4().hex[:12]}"},
        headers=auth_headers(ctx),
    )
    assert r.status_code in (200, 201), r.text
    return str(r.json()["project"]["id"])


def _put_main(client, ctx: dict, project_id: str) -> None:
    r = client.post(
        f"{PREFIX}/projects/{project_id}/files",
        json={"path": "main.tex", "kind": "tex", "content": _TEX},
        headers=auth_headers(ctx),
    )
    assert r.status_code in (200, 201), r.text


def _insert_running_run(project_id: str, tenant_id: uuid.UUID, actor_id: uuid.UUID, entry: str) -> str:
    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.research_workspace.models import ResearchCompileRunRow, utcnow

    with session_scope() as db:
        run = ResearchCompileRunRow(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=uuid.UUID(project_id),
            actor_id=actor_id,
            engine="xelatex",
            entry=entry,
            status="running",
            created_at=utcnow(),
        )
        db.add(run)
        db.flush()
        return str(run.id)


def _count_runs(project_id: str) -> int:
    from research_vnext.core.db.engine import session_scope
    from research_vnext.core.research_workspace.models import ResearchCompileRunRow

    with session_scope() as db:
        return int(
            db.execute(
                select(func.count())
                .select_from(ResearchCompileRunRow)
                .where(ResearchCompileRunRow.project_id == uuid.UUID(project_id))
            ).scalar_one()
        )


def test_compile_rejects_when_active_run_exists(client, ctx_factory) -> None:
    """已有 running run ⇒ 409 + COMPILE_IN_PROGRESS，且**不新建 run**。"""
    ctx = ctx_factory()
    project_id = _make_project(client, ctx)
    _put_main(client, ctx, project_id)
    running_id = _insert_running_run(
        project_id, uuid.UUID(ctx["tenant_id"]), uuid.UUID(ctx["actor_id"]), "main.tex"
    )
    assert _count_runs(project_id) == 1

    r = client.post(
        f"{PREFIX}/projects/{project_id}/compile",
        json={"entry": "main.tex", "engine": "xelatex"},
        headers=auth_headers(ctx),
    )
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "COMPILE_IN_PROGRESS"
    assert err["run_id"] == running_id
    assert err["status"] == "running"
    # 机器可读码 + 人类可读中文（不暴露裸码/堆栈）
    assert "编译" in err["message"]
    # **核心断言**：第二次请求没有新建 run —— 报表里仍只有那一条 running。
    assert _count_runs(project_id) == 1


def test_compile_not_blocked_by_finished_run(client, ctx_factory) -> None:
    """已结束（failed/success）的旧 run 不该触发 409 —— 只有 queued|running 才算进行中。"""
    from research_vnext.core.research_workspace import compile as compile_svc
    from research_vnext.core.db.engine import session_scope

    ctx = ctx_factory()
    project_id = _make_project(client, ctx)
    _put_main(client, ctx, project_id)

    with session_scope() as db:
        from research_vnext.core.research_workspace.models import ResearchCompileRunRow, utcnow

        db.add(
            ResearchCompileRunRow(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(ctx["tenant_id"]),
                project_id=uuid.UUID(project_id),
                actor_id=uuid.UUID(ctx["actor_id"]),
                engine="xelatex",
                entry="main.tex",
                status="failed",
                created_at=utcnow(),
            )
        )
        db.flush()
        # 直接问"有没有进行中的 run"：finished 的旧 run 不算。
        assert (
            compile_svc.active_compile_run(
                db,
                tenant_id=uuid.UUID(ctx["tenant_id"]),
                project_id=uuid.UUID(project_id),
                entry="main.tex",
            )
            is None
        )
