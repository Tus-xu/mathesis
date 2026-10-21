"""research-vnext（8300）FastAPI 应用工厂。

**生产启动不做 runtime DDL**：只校验 schema 已由 Alembic 建好，
缺失即启动失败（而不是 `create_all` 兜底）。

**单一启动路径**：只有 `lifespan` 一个启动钩子。此处**刻意不使用**
`@app.on_event(...)` 风格的启动装饰器 —— 它与 `lifespan` 同时存在时是重复注册，
且该 API 在新版 Starlette/FastAPI 已废弃；历史上正是这种"第二个启动钩子静默不触发"
让 DDL 生产门禁变成空操作。任何启动期校验都必须写在 `lifespan` 里，
并有测试守卫（见 `tests/contracts/test_dual_prefix_and_startup.py`）。

**多前缀挂载**：同一组路由同时挂在 `api_prefixes()` 返回的每个前缀下
（默认 `/api/research-v1` + `/api/research-vnext`），后者是前端实际调用的前缀。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..config import SERVICE_NAME, SERVICE_PORT, api_prefixes, db_schema, is_test
from ..db.engine import assert_no_runtime_ddl_in_prod
from ..domain.errors import DomainError
from ..observability.logging_setup import configure_logging
from ..observability.metrics import incr, snapshot
from .routes import router
from .rv09_routes import router as rv09_router
from .asset_literature_routes import asset_router, literature_router
from .parser_routes import parser_router
from ..assets import models as _asset_models  # noqa: F401  register tables
from ..literature import models as _lit_models  # noqa: F401
from ..parser import models as _parser_models  # noqa: F401
from ..federation import models as _federation_models  # noqa: F401
from ..federation.routes import federation_router
from ..evidence import models as _evidence_models  # noqa: F401  register tables
from ..evidence.routes import evidence_router
from ..citation import models as _citation_models  # noqa: F401
from ..citation.routes import citation_router
from ..export import models as _export_models  # noqa: F401  RV14 register tables
from ..export.routes import export_router
from ..project import models as _project_models  # noqa: F401  RV16 register tables
from ..stream import models as _stream_models  # noqa: F401  RV16 register tables
from ..research_workspace import models as _ws_models  # noqa: F401  RV17 register tables
from .project_routes import router as project_router
from .stream_routes import router as stream_router
from .workspace_routes import router as workspace_router


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    # 生产启动只**校验**迁移状态，不做 runtime DDL。唯一的启动期守卫之一。
    if not is_test():
        assert_no_runtime_ddl_in_prod()
        # LLM (账号, 模型) 配对校验：本平台模型授权**按账号隔离**（实测 403），
        # 配错账号是一个可操作的配置错误。让它在这里**响亮失败**，
        # 而不是等第一个用户发消息时收到一个裸 403。
        from ..llm import gateway as llm

        report = llm.assert_ready_or_degrade()
        app.state.llm_pairing = report.to_dict()
    yield


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Research VNext Core",
        version="rv02",
        description="ResearchSession 领域事件模型 · revision · idempotency · 刷新恢复",
        lifespan=lifespan,
    )

    @app.exception_handler(DomainError)
    def _domain_error_handler(request, exc: DomainError):  # noqa: ANN001,ANN202
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})

    # 同一组路由挂载到每个配置前缀（默认 v1 + vnext）。前缀来自配置，可覆盖。
    for prefix in api_prefixes():
        app.include_router(router, prefix=prefix)
        app.include_router(asset_router, prefix=prefix)
        app.include_router(literature_router, prefix=prefix)
        app.include_router(parser_router, prefix=prefix)
        app.include_router(federation_router, prefix=prefix)
        app.include_router(evidence_router, prefix=prefix)
        app.include_router(citation_router, prefix=prefix)
        # RV09 · 地图 / 修订审计 / 记忆 / prompt 发布
        app.include_router(rv09_router, prefix=prefix)
        # RV11 · ExperimentRun（隔离沙箱执行）
        from .experiment_routes import router as experiment_router

        app.include_router(experiment_router, prefix=prefix)
        # RV12 · Claim / Verification（ClaimLedger + Verifier）
        from .claim_routes import router as claim_router

        app.include_router(claim_router, prefix=prefix)
        # RV13 · PaperSpec（结构化编辑 / 撤销恢复 / 绑定健康）
        from .paper_routes import router as paper_router

        app.include_router(paper_router, prefix=prefix)
        # RV14 · Paper export（Markdown/LaTeX/PDF 确定性导出 + 下载令牌）
        app.include_router(export_router, prefix=prefix)
        # RV15 · Review Finding（Review Board / 阻断门 / 豁免）
        from .review_routes import router as review_router

        app.include_router(review_router, prefix=prefix)
        # RV16 · ResearchProject 容器 + 欢迎页聚合（K1 无假按钮）
        app.include_router(project_router, prefix=prefix)
        # RV16 · token 流 SSE 通道（X7：与 /events 职责分离，不含 mutation）
        app.include_router(stream_router, prefix=prefix)
        # RV17 · 科研工作区（文献/笔记/文件树/编译/知识检索/Agent 任务）
        app.include_router(workspace_router, prefix=prefix)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "service": SERVICE_NAME, "port": SERVICE_PORT, "schema": db_schema()}

    @app.get("/readyz")
    def readyz():
        try:
            if not is_test():
                assert_no_runtime_ddl_in_prod()
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"status": "not-ready", "error": str(e)})
        return {"status": "ready"}

    @app.get("/llmz")
    def llmz():
        """LLM 网关状态（**不含任何密钥值**，只有指纹）。

        存在的理由：单一供应商 + 账号隔离意味着"为什么模型不可用"这个问题
        必须在**不读日志**的情况下可回答。这里给出模型、账号、key 指纹、
        是否使用完整端点、以及（配对不合法时的）具体该换哪把 key。
        """
        from ..llm import gateway as llm

        try:
            report = llm.validate_pairing()
            payload = {"status": "ok" if report.ok else "degraded", **report.to_dict()}
            if not report.configured:
                # 无凭据是**合法但需告警**的状态：给出机器可读的 code，
                # 否则运维只能从 hint 文本里猜。
                payload["code"] = "LLM_NOT_CONFIGURED"
            return payload
        except llm.ModelAccountMismatch as e:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "model-account-mismatch",
                    "code": e.code,
                    "model": e.model,
                    "account": e.account,
                    "key_fingerprint": llm.key_fingerprint(),
                    "hint": e.hint,
                },
            )
        except llm.LlmGateError as e:
            return JSONResponse(
                status_code=200,
                content={"status": "degraded", "code": e.code, "hint": e.message,
                         "configured": False, "ok": False},
            )

    @app.get("/metrics")
    def metrics():
        return snapshot()

    @app.middleware("http")
    async def _trace_mw(request, call_next):
        from ..observability.trace import parse_traceparent

        tp = request.headers.get("traceparent")
        ctx = parse_traceparent(tp)
        incr("http_requests_total", 1.0, method=request.method, path=request.url.path)
        response = await call_next(request)
        if ctx:
            response.headers["X-Trace-Id"] = ctx.trace_id
        incr(
            "http_responses_total",
            1.0,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        )
        return response

    return app


app = create_app()
