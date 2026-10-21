# Math Arena API - Dockerfile
FROM python:3.12-slim

WORKDIR /app

# 大 wheel 经国内镜像下载经常超时（2026-10-05 实测）→ 放宽 pip 超时与重试
ENV PIP_DEFAULT_TIMEOUT=180

# 国内云服务器构建加速：apt/pip 默认走阿里云镜像（可用 --build-arg 覆盖）
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .
# `--progress-bar off` + 显式 timeout/retries：requirements 里 rapidocr 会拉
# opencv-python（manylinux wheel 实测 73MB），pip 默认进度条在 buildkit 下输出
# 大量 \r 进度行，实测出现过下载中途挂死（60s 无日志、无 socket）→ 关掉进度条并放大重试。
RUN pip install --no-cache-dir --progress-bar off --timeout 60 --retries 10 \
    -i "${PIP_INDEX}" -r requirements.txt

# 复制应用代码
COPY . .

EXPOSE 8000

# 启动时先运行数据库迁移，再启动服务
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
