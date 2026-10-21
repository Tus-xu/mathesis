# research-vnext (8300) 生产镜像 —— r7-deploy 阶段一
#
# 构建方式（在 services/research-vnext 目录下）：
#   docker build -f deploy/Dockerfile -t math-arena-research:latest .
#
# 关键点：
#   - 基础镜像 python:3.11-slim（主理人指定）
#   - docker CLI 必须在镜像内：DockerTexCompiler 通过 `docker run` 派生
#     xelatex 编译容器（core/export/compiler.py:_docker_runner），
#     运行期需挂载宿主 /var/run/docker.sock
#   - torch/torchvision 先从 CPU 专用索引装（PyPI linux 轮子会拖 nvidia CUDA
#     全家桶，多出 ~3GB 且编译容器用不到 GPU）
#   - 依赖清单来源：本地 .venv pip freeze（pyproject.toml 未声明依赖）。
#     ⚠ freeze 来自 Python 3.13 venv，numpy==2.5.3 等 Requires-Python>=3.12，
#     故基镜像必须 3.12（3.11 会在 pip resolve 阶段失败）。
FROM python:3.12-slim

ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        docker.io \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) torch 全家桶：CPU 轮子先行（避免从 PyPI 拉 nvidia/cuda 依赖链）
COPY deploy/requirements.torch.txt /tmp/requirements.torch.txt
RUN pip install --index-url "${TORCH_INDEX}" -r /tmp/requirements.torch.txt

# 2) 其余依赖（含 docling/litellm/boto3 等；torch 已在环境中，被满足）
COPY deploy/requirements.lock /tmp/requirements.lock
RUN pip install --index-url "${PIP_INDEX}" -r /tmp/requirements.lock

# 3) 应用代码（alembic.ini 的 script_location=migrations 为相对路径，cwd=/app）
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY research_vnext ./research_vnext

EXPOSE 8300

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8300/healthz', timeout=4).read()" || exit 1

# 入口：uvicorn（迁移由部署侧显式执行 `alembic upgrade head`，不在启动路径里）
CMD ["uvicorn", "research_vnext.core.api.app:app", "--host", "0.0.0.0", "--port", "8300"]
