# PPT Studio V2 (8200) 生产镜像
#
# 构建（在 services/ppt-studio-v2 目录下）：
#   docker build -f Dockerfile -t math-arena-ppt-studio-v2:latest .
#
# 为什么需要它：本服务此前只有本地 `uv run uvicorn main:app` 的裸跑方式，
# 没有镜像 → 云服务器无法部署，导致教师端 `/api/ppt-studio-v2/*` 前缀 404。
#
# 与 teacher-platform/Dockerfile 保持同一基础镜像（层可共享）与同一构建风格。
# 依赖走阿里云 PyPI 镜像（与 services/api、services/research-vnext 的 Dockerfile 同约定）。
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=${PIP_INDEX} \
    PYTHONUNBUFFERED=1

# 依赖层（利用缓存）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 应用代码
COPY . .

EXPOSE 8200

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8200/healthz', timeout=4).read()" || exit 1

# 启动前先跑迁移（幂等）—— alembic/env.py 以 DATABASE_URL 环境变量覆盖 ini 里的本地回退值
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn main:app --host 0.0.0.0 --port 8200"]
