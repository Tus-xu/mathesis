#!/usr/bin/env bash
# ============================================================
# Math Arena 云服务器一键部署脚本（Ubuntu 22.04 / Debian 12）
# 功能：安装 Docker → 校验目录 → 重建 API 镜像 → 启动全家桶 → 健康检查 → 输出访问 IP
# 用法：把后端与前端目录传到服务器后，执行：sudo bash remote-setup.sh
# 目录约定：
#   /opt/math-arena                 后端 monorepo（含 .env 与 deploy/）
#   /opt/math-arena-test-frontend   前端仓库（需已构建 dist/）
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/math-arena}"
FRONT_DIR="${FRONT_DIR:-/opt/math-arena-test-frontend}"
COMPOSE_FILE="$APP_DIR/deploy/docker-compose.server.yml"

echo "==> [1/5] 检查 Docker"

if ! command -v docker >/dev/null 2>&1; then
  echo "    未检测到 Docker，开始安装（官方脚本）..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
else
  echo "    Docker 已安装: $(docker --version)"
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "    未检测到 docker compose 插件，正在安装..."
  apt-get update
  apt-get install -y docker-compose-plugin || apt-get install -y docker-compose-v2
fi

echo "==> [2/5] 校验目录结构"

[ -d "$APP_DIR/services/api" ]    || { echo "    缺少 $APP_DIR/services/api（后端仓库）"; exit 1; }
[ -d "$FRONT_DIR/dist" ]          || { echo "    缺少 $FRONT_DIR/dist（前端未构建，请先 npm run build）"; exit 1; }
[ -f "$APP_DIR/.env" ]            || { echo "    缺少 $APP_DIR/.env（模型密钥，请从本机上传）"; exit 1; }
[ -f "$COMPOSE_FILE" ]            || { echo "    缺少 $COMPOSE_FILE"; exit 1; }
echo "    目录校验通过"

echo "==> [3/5] 重建 API 镜像（含最新 alembic 迁移）"

docker build -t math-arena-api:latest "$APP_DIR/services/api"

echo "==> [4/5] 启动全家桶（postgres + redis + api + web）"

cd "$APP_DIR/deploy"
docker compose -f docker-compose.server.yml up -d --remove-orphans

echo "==> [5/5] 等待健康检查（最长 60s）"

for i in $(seq 1 30); do
  if curl -sf http://localhost/api/health >/dev/null 2>&1; then
    echo "    API 健康检查通过 ✓"
    break
  fi
  if [ "$i" = "30" ]; then
    echo "    部署超时！请排查日志："
    echo "      docker compose -f $COMPOSE_FILE logs api"
    exit 1
  fi
  sleep 2
done

# 尽力探测公网 IP（失败则回退到内网 IP）
IP="$(curl -sf --max-time 5 https://ifconfig.me 2>/dev/null || curl -sf --max-time 5 https://ipinfo.io/ip 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"

echo ""
echo "================================================================"
echo "  部署完成！"
echo "  访问地址 : http://${IP}"
echo "  API 健康 : http://${IP}/api/health"
echo "  登录验证 : 浏览器打开上方地址，用手机号 + 验证码登录"
echo "--------------------------------------------------------------"
echo "  常用命令（在 $APP_DIR/deploy 下执行）："
echo "    查看状态 : docker compose -f docker-compose.server.yml ps"
echo "    查看日志 : docker compose -f docker-compose.server.yml logs -f api"
echo "    停止服务 : docker compose -f docker-compose.server.yml down"
echo "================================================================"
