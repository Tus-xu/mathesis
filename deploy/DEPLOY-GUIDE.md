# Math Arena 云服务器部署指南

> 适用场景：把前后端整套搬到云服务器，给队友一个**快且稳定**的公网访问地址（免穿透、免域名、IP 直访免备案）。
> 预估耗时：买服务器 10 分钟 + 部署 15 分钟。成本：腾讯云轻量 2C4G 约 50~70 元/月（新用户/学生优惠更低）。

---

## 0. 为什么这样部署

```
队友浏览器 ──> http://服务器IP ──> nginx(前端 dist + /api 反代) ──> FastAPI ──> PostgreSQL/Redis/模型API
```

- 前后端都在**同一台云服务器**，带宽独立、7×24 在线，彻底告别"穿透慢"（根因是家用宽带上行带宽小）
- 只对外开 80 端口，8000 不出网，安全性更好

---

## 1. 服务器选购（腾讯云轻量应用服务器）

| 项目 | 建议 | 说明 |
|---|---|---|
| 地域 | 离队友近的（如广州/上海） | 影响访问延迟 |
| 系统 | **Ubuntu 22.04 LTS** | Docker 支持最好，脚本按此编写 |
| 规格 | **2C4G**（4G 内存） | PG + Redis + FastAPI 实测内存 ~1.5G，2G 会紧 |
| 带宽 | 3~5Mbps 起步 | 聊天流式输出够用 |
| 镜像 | 选"应用镜像"或"系统镜像"均可 | 脚本会自动装 Docker |

购买入口：腾讯云官网 → 轻量应用服务器（Lighthouse）。新用户常有 1~3 个月低价。

> ⚠️ **安全组**：控制台放行 **TCP 80 端口**（HTTP）和 22（SSH，默认已开）。8000 不用开。

---

## 2. 本机准备（一次即可）

### 2.1 确认前端已构建

```bash
cd /d/math-arena-test-frontend
npm run build   # 生成 dist/
```

### 2.2 确认 .env 就位

根目录 `D:\math-arena\.env` 已含模型密钥（星火/DeepSeek/JWT）。**公网暴露前建议更换 JWT_SECRET**（`app/config.py` 有校验，development 模式不强制但强烈建议）。

### 2.3 上传两个目录到服务器

把整个目录传上去（**.env 必须一起传，注意别传到 git 仓库**）：

```bash
# Windows 下用 scp（Git Bash 自带）：
scp -r /d/math-arena ubuntu@服务器IP:/opt/
scp -r /d/math-arena-test-frontend ubuntu@服务器IP:/opt/

# 或使用 WinSCP / MobaXterm 图形化上传，效果相同
```

上传后服务器上应有：
- `/opt/math-arena/.env`
- `/opt/math-arena/deploy/docker-compose.server.yml`
- `/opt/math-arena/deploy/remote-setup.sh`
- `/opt/math-arena-test-frontend/dist/`

> 💡 建议上传前删除大文件加快速度：`node_modules/`、`pgvector-*`、`*.zip`、`services/api/.venv`、`services/**/__pycache__`、`logs/`。

---

## 3. 一键部署（服务器上执行）

```bash
sudo bash /opt/math-arena/deploy/remote-setup.sh
```

脚本自动完成：装 Docker → 校验目录 → 构建 API 镜像（**含最新 m2_008 迁移**，解决旧镜像迁移失败）→ 启动全家桶 → 健康检查 → 打印访问地址。

看到 `部署完成！` 即成功。

---

## 4. 验证清单

| 检查项 | 方法 | 预期 |
|---|---|---|
| 前端可访问 | 浏览器打开 `http://服务器IP` | 看到登录页 |
| 后端健康 | `curl http://服务器IP/api/health` | 返回 `{"code":0,...}` |
| 登录 | 手机号 + 验证码（dev 固定码 `123456`） | 登录成功 |
| 聊天流式 | 发一条消息 | 逐字输出（SSE） |
| 班级/题库 | 教师/学生视角各测一遍 | 正常 |

把 `http://服务器IP` 发给队友即可开测。

---

## 5. 常见问题

### 5.1 alembic 迁移失败（旧镜像遗留）
- 表现：`Can't locate revision identified by 'm2_008_question_bank'`
- 原因：`math-arena-api` 镜像是 8月8日 之前构建的，不含新增迁移文件
- 解决：`docker build -t math-arena-api:latest /opt/math-arena/services/api`（脚本第 3 步已自动做）

### 5.2 内存不足（2G 机型）
- 表现：容器反复重启 / OOM
- 解决：加 swap：`fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`；或升配 4G

### 5.3 RAG 引用能力降级
- 原因：compose 未部署本地 BGE-M3 Embedding 服务（config 默认 `localhost:8080`）
- 影响：**聊天主链路、题库、班级不受影响**，仅知识库引用检索不可用
- 后续：如需完整 RAG，需单独部署 embedding 服务并改 `EMBEDDING_BASE_URL`

### 5.4 文件上传不可用
- 原因：对象存储（默认 COS）未配置
- 影响：上传类功能报错；聊天/题库/班级正常
- 后续：配置 `STORAGE_*` 环境变量或改用 MinIO

### 5.5 想用 HTTPS / 域名
- 国内服务器绑域名需**备案**（约 1~2 周）；测试阶段用 `http://IP` 即可
- 后续可：备案后绑定域名 + 腾讯云免费 SSL 证书，nginx.conf 加 443 监听

### 5.6 数据备份
- 数据在 Docker volume：`docker compose -f docker-compose.server.yml down` 不会删数据
- 备份：`docker exec math-arena-postgres pg_dump -U postgres math_arena > backup.sql`

---

## 6. 停止 / 卸载

```bash
cd /opt/math-arena/deploy
docker compose -f docker-compose.server.yml down        # 停服务（保留数据）
docker compose -f docker-compose.server.yml down -v     # 停服务并清空数据
```
