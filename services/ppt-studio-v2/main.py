"""uvicorn 入口：PPT Studio V2 API（8200）——工厂与 `app` 定义位于 app/main.py。"""
from app.main import app, create_app  # noqa: F401

if __name__ == "__main__":
    import uvicorn

    from app.config import get_settings

    settings = get_settings()
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
