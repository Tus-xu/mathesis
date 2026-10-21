# 教师端 V3 平台 · API 镜像（FastAPI + uvicorn）
# 基础镜像：uv 官方 python3.12 运行时镜像（ghcr.io，国内可走镜像代理）
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# 国内 PyPI 镜像（与 services/api、services/research-vnext 的 Dockerfile 同约定）；
# 不设它会直连 pypi.org，境内构建实测极慢甚至挂死。
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_DEFAULT_INDEX=${PIP_INDEX} \
    # 大 wheel（botocore/litellm 等）经国内镜像下载经常超 30s，2026-10-05 实测
    # 「Failed to download botocore … network timeout」→ 放宽到 180s
    UV_HTTP_TIMEOUT=180 \
    PYTHONUNBUFFERED=1

# 先装依赖（利用层缓存）
COPY pyproject.toml uv.lock ./
# uv.lock 把 775 个 wheel URL 钉死在 files.pythonhosted.org（--frozen 不吃
# UV_DEFAULT_INDEX 换源）；2026-10-10 晚该域国际路由中断 → 构建全挂。
# 清华镜像 /packages/... 路径与 pythonhosted 完全同构、字节一致（sha256 校验
# 仍然把关），改写锁内 URL 即可恢复构建；pythonhosted 恢复后本步依旧正确。
RUN sed -i 's|https://files.pythonhosted.org/|https://pypi.tuna.tsinghua.edu.cn/|g' uv.lock \
    && uv sync --frozen --no-dev --no-install-project

# 拷贝源码
COPY . .

EXPOSE 8100

# 启动前先跑迁移（幂等：alembic upgrade head）
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn main:app --host 0.0.0.0 --port 8100"]
