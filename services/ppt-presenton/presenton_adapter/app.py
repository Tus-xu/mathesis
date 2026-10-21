"""8201 Presenton adapter 应用工厂（loopback-only）."""
from __future__ import annotations

from fastapi import FastAPI

from presenton_adapter.api import router
from presenton_adapter.config import get_settings
from presenton_adapter.security import ServiceTokenMiddleware


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_binding()  # 启动即拒绝非回环绑定
    app = FastAPI(title="Presenton Adapter (8201)", version="0.1.0", docs_url=None, openapi_url=None)
    app.add_middleware(ServiceTokenMiddleware)
    app.include_router(router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    # 强制 loopback：写死 127.0.0.1（不读任意绑定），浏览器/前端无法直连
    uvicorn.run("presenton_adapter.app:app", host=s.host, port=s.port, reload=False)
