"""PPT Studio V2 API（8200）应用工厂——独立进程/配置/DB/队列（R15）。

与 8100 教师平台完全独立：不 import 8100 任何业务模块（由 import denylist 测试守卫）。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import router
from app.config import get_settings
from app.db.session import close_engine
from app.errors import ApiError, ErrorCode
from app.logging import setup_logging
from app.response import fail
from app.trace import TraceMiddleware

logger = logging.getLogger("ps2.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("ppt-studio-v2(8200) starting", extra={"version": "0.1.0", "environment": settings.environment})
    yield
    await close_engine()
    logger.info("ppt-studio-v2(8200) stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="PPT Studio V2 (8200)",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(TraceMiddleware)
    app.include_router(router)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=fail(
                exc.code,
                exc.message,
                request_id=getattr(request.state, "request_id", ""),
                error_key=exc.error_key,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        msg = "请求参数不合法"
        try:
            first = exc.errors()[0]
            msg = f"{msg}: {'.'.join(str(x) for x in first.get('loc', []))} {first.get('msg', '')}"
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse(
            status_code=422,
            content=fail(ErrorCode.VALIDATION, msg, request_id=getattr(request.state, "request_id", "")),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.BAD_REQUEST if hasattr(ErrorCode, "BAD_REQUEST") and exc.status_code < 500 else ErrorCode.INTERNAL
        if exc.status_code == 404:
            code = ErrorCode.NOT_FOUND
        return JSONResponse(
            status_code=exc.status_code,
            content=fail(code, str(exc.detail), request_id=getattr(request.state, "request_id", "")),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        structlog.get_logger("ps2.unhandled").exception("unhandled", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=fail(
                ErrorCode.INTERNAL,
                "服务器内部错误",
                request_id=getattr(request.state, "request_id", ""),
            ),
        )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
