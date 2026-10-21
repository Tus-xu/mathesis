"""教师端 V3 平台 · 应用工厂（B3 拥有）。

lifespan：日志 → MinIO bucket 初始化 → 事件发布器就绪 → 关闭时清理（优雅停机）。
中间件栈：Idempotency(G8) → RequestContext(request_id/PII)。异常统一转 fail 信封（契约壳）。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Windows + httpx/LiteLLM/asyncpg：默认 ProactorEventLoop 在部分栈下会触发
# provider_unavailable / 连接异常；与 services/api 一致改用 Selector。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.api.routes import register_routers
from app.core.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.idempotency import IdempotencyMiddleware
from app.core.logging import setup_logging
from app.core.middleware import RequestContextMiddleware
from app.core.response import fail
from app.db.session import close_engine
from app.storage.minio import get_storage

# .env 注入 os.environ（LlmSettings 由 pydantic-settings 自行加载；博查等 os.getenv 读取方依赖此步）
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("teacher.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("teacher-platform starting", extra={"version": "0.1.0", "environment": settings.environment})

    # R11.5-B（DECISIONS §5）：生产启动 fail-closed——正式迁移接管表 preflight；
    # 缺表/缺关键列 → 启动失败（禁止运行期隐式 DDL / 内存降级）。local/test 跳过。
    if not settings.is_dev:
        from app.db.ownership import RuntimeOwnershipError, preflight_owned_tables

        try:
            await preflight_owned_tables(lambda: __import__("app.db.session", fromlist=["session_factory"]).session_factory())
        except RuntimeOwnershipError as exc:
            logger.error("生产启动失败：运行时表未由正式迁移接管（需执行 alembic upgrade head）", extra={"error": str(exc)})
            raise

    # 基础设施初始化（失败不阻塞启动——ValkeyCompatibilityGate/集成环境才要求全绿）
    try:
        await get_storage().ensure_buckets()
        logger.info("minio buckets ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning("minio init deferred", extra={"error": str(exc)})

    yield

    # 优雅停机：释放连接
    await close_engine()
    from app.core.events import get_publisher

    await get_publisher().close()
    logger.info("teacher-platform stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Teacher Platform V3",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # 中间件（后加的在外层）：RequestContext 最外（request_id 贯穿全局）
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(IdempotencyMiddleware)

    register_routers(app)

    # ---- 全局异常 → 契约 fail 信封 ----
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        body = fail(
            exc.code,
            exc.message,
            request_id=getattr(request.state, "request_id", ""),
            error_key=exc.code.name.lower(),  # 契约：role_denied（小写，对齐 mock）
        )
        # 409 冲突必须带回服务端上下文，否则客户端只能猜（平台契约 command.py）
        if exc.data is not None:
            body["data"] = exc.data
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        msg = "请求参数不合法"
        try:
            first = exc.errors()[0]
            loc = ".".join(str(x) for x in first.get("loc", []))
            msg = f"{msg}: {loc} {first.get('msg', '')}"
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse(
            status_code=422,
            content=fail(ErrorCode.VALIDATION, msg, request_id=getattr(request.state, "request_id", ""), error_key="validation"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.BAD_REQUEST if exc.status_code < 500 else ErrorCode.INTERNAL
        if exc.status_code == 404:
            code = ErrorCode.NOT_FOUND
        return JSONResponse(
            status_code=exc.status_code,
            content=fail(code, str(exc.detail), request_id=getattr(request.state, "request_id", "")),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log = structlog.get_logger("teacher.unhandled")
        log.exception("unhandled", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=fail(
                ErrorCode.INTERNAL,
                "服务器内部错误",
                request_id=getattr(request.state, "request_id", ""),
                error_key="internal",
            ),
        )

    # ---- 健康检查（B3 §4.3：DB/Valkey/MinIO/sidecar 探活）----
    @app.get("/healthz")
    async def healthz(request: Request) -> dict:
        checks: dict[str, str] = {}
        from app.db.session import engine
        from sqlalchemy import text

        try:
            async with engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["db"] = "up"
        except Exception as exc:  # noqa: BLE001
            checks["db"] = f"down({type(exc).__name__})"

        from app.core.events import get_publisher

        try:
            r = await get_publisher().redis()
            await r.ping()
            checks["valkey"] = "up"
        except Exception as exc:  # noqa: BLE001
            checks["valkey"] = f"down({type(exc).__name__})"

        try:
            checks["minio"] = "up" if await get_storage().health() else "down"
        except Exception as exc:  # noqa: BLE001
            checks["minio"] = f"down({type(exc).__name__})"

        # sidecar（B1/B4 提供；未就绪 = dependency_missing，不阻塞骨架）
        for name, url in (
            ("pix2text", get_settings().pix2text_url),
            ("funasr", get_settings().funasr_url),
            ("katex", get_settings().katex_url),
        ):
            checks[name] = "dependency_missing" if "teacher-" in url else "configured"

        all_up = checks.get("db") == "up" and checks.get("valkey") == "up"
        payload = {"status": "ok" if all_up else "degraded", "checks": checks}
        return JSONResponse(status_code=200 if all_up else 503, content=payload)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("main:app", host=settings.teacher_api_host, port=settings.teacher_api_port, reload=False)
