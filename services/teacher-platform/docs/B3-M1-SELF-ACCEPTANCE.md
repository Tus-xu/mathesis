# B3 · 阶段 0 + M1 自验收报告（Web 平台工程智能体）

> 日期：2026-09-07 · 对应 `agents/B3-WEB-PLATFORM.md §3（DAY1–D3）/ §5（自验收）`
> 运行环境：Docker Desktop（含 registry-mirror 配置）/ uv 0.12.3 / Python 3.12 / Valkey 9.1.2（pin digest）

## 基建状态

### compose 服务健康表（`docker compose ps`）

| 服务 | 容器 | 状态 |
|---|---|---|
| postgres（pgvector pg16） | teacher-postgres | Up (healthy)，库 `teacher_platform` |
| valkey（9.1.2-alpine@sha256:ccfa…920cd） | teacher-valkey | Up (healthy) |
| minio | teacher-minio | Up (healthy)，5 bucket 自动初始化 |
| teacher-api（FastAPI+uvicorn） | teacher-api | Up，`/healthz` 全绿 |
| teacher-worker（TaskIQ） | teacher-worker | Up，2 worker 进程 |
| pix2text / funasr / katex | 占位（B4/B1 待供真实镜像） | 不阻塞骨架 |

### 迁移版本

- `alembic upgrade head` → `e155e6a302bf`（users/classes/tasks/task_events/idempotency_records/export_jobs/classroom_events + pgvector 扩展）。

### 端点就绪清单（B3 平台端点，`/api/teacher-v3` 前缀）

| 端点 | 状态 |
|---|---|
| `POST /auth/login`（zhanglaoshi/teacher123，JWT HS256） | ✔ 验证通过 |
| `GET /tasks`（契约形状 `{items,running}`，真实 DB 数据） | ✔ 验证通过 |
| `POST /upload/presign`（MinIO presign PUT 直传，bucket 白名单） | ✔ 双路径验证 |
| `POST /platform/demo/sleep` + `GET /platform/demo/tasks/{id}/stream` | ✔ 全链验证 |
| `GET /classroom/sessions/{id}/stream`（RealtimeHub 传输层） | ✔ 骨架验证 |
| `GET /healthz`（DB/Valkey/MinIO/sidecar 探活） | ✔ 全绿 |
| 全局异常 → 契约 fail 信封（含 40301 role_denied / 40401/40402） | ✔ |

## 交付物

```
services/teacher-platform/
├── main.py / pyproject.toml / uv.lock / .env.example / .gitignore
├── docker-compose.yml（8 服务，TEACHER_API_PORT 可配置）/ Dockerfile / Dockerfile.worker
├── Makefile / scripts/{dev.ps1, demo_chain.py}
├── alembic/（env.py + 初始迁移 e155e6a302bf）
├── app/core/   config / logging(request_id+PII白名单) / response(ok/fail) / errors(错误码表)
│               security(JWT) / authz(assert_resource_owner) / deps / events(G4 双通道)
│               sse(Last-Event-ID 恢复) / idempotency(G8) / hub(RealtimeHub) / middleware
├── app/db/     base / session / models/platform.py（7 表 + classroom_events）
├── app/tasks/  broker(Valkey Stream) / registry / service(TaskService) / jobs/demo_sleep.py
├── app/storage/minio.py（presign/bucket 初始化）
├── app/api/    routes.py（路由聚合）/ platform.py / schemas.py
├── tests/unit/platform/    40 用例（hermetic）
├── tests/integration/valkey_gate/  ValkeyCompatibilityGate 5 用例
├── seeds/seed_dev.py（张老师 + 3 班级）
└── docs/handoff/  IFC-002-upload-presign.md / PORT-CONFLICT-NOTE.md / frontend-mock-ref/（G10 契约镜像）
```

## 关键验证证据（运行时）

| 项 | 证据 |
|---|---|
| **ValkeyCompatibilityGate（M1 出口硬门禁）** | `pytest tests/integration/valkey_gate -m gate` → **5 passed**：enqueue→consume→ACK→result / crash→XAUTOCLAIM 恢复 / 重启重连 / result expiry。`VALKEY_TASKIQ_COMPAT=VERIFIED` |
| **demo 全链（P9）** | `python -m scripts.demo_chain`：login → 提交 → **live 实时收进度**（status/progress 25/50/75/100/done）→ 断线 `Last-Event-ID` → **durable 重放零丢失** |
| **P10 worker 崩溃恢复** | `docker compose kill worker` → 重启 → **31s 内 XAUTOCLAIM 接管重执行，3 任务全部 done**（worker 日志 19:54:29 首执行 → 19:55:00 恢复重执行） |
| **presign 直传** | 容器内（minio:9000）：PUT 200 + MinIO stat 确认；浏览器视角（MINIO_ENDPOINT=127.0.0.1:9002）：URL host 正确 + PUT 200 + stat 确认 |
| **幂等（G8）** | 同 Idempotency-Key 两次提交 → 重放首结果（同 task_id） |
| **契约壳单测** | `pytest tests/unit/platform` → **40 passed**（响应壳/40301/40101/幂等窗口/SSE 解析/AuthZ 矩阵） |
| **契约对齐（B5 断言）** | `/tasks` 形状 `{items,running}`（task_id/status 枚举 queued/running/succeeded/failed）✔；40401/40402 错误码 ✔；JWT core ✔ |

## 性能 vs 预算表（G9，M1 相关行）

| 项 | SLO | 实测 | 状态 |
|---|---|---|---|
| P9 SSE 断线恢复 | 30s 内重连零丢失 | 重连即重放，seq 连续无缺 | ✔ |
| P10 Worker 崩溃恢复 | ≤60s | 31s（idle_timeout=30s 配置） | ✔ |
| 冷启动 → /healthz 全绿 | ≤3 分钟 | 镜像已就绪后 <1 分钟 | ✔ |

## IFC

- → **A0**：`IFC-002-upload-presign.md`（新端点 + MINIO_ENDPOINT 配置指引，presign URL host 不可改写）。
- → **B1/B4**：任务队列/双通道 SSE/RealtimeHub 传输层就绪，`TaskService.submit/publish_durable/publish_live/finish` 签名可用（架构 §8）。
- → **B5**：`tests/integration/valkey_gate` 即兼容性证据；契约测试中失败项全部为 B4/B1 域端点未实现（分类统计见下），B3 平台面无红。

## 契约测试失败分类（B5 套件，43 failed + 23 errors 全为域端点缺失）

quiz/decks/plans/grading/recognition/today/butler/classroom 域端点与 8 条 SSE 通道——属 B4/B1 M1 未完项；`api_inventory` 对账随之红。**B3 责任项零失败**。

## 阻塞 / 环境备注

1. **端口冲突**：`127.0.0.1:8100` 被学生端 API 占用（应 8000，见 `docs/handoff/PORT-CONFLICT-NOTE.md`）——教师平台用 `TEACHER_API_PORT=8101` 验证，建议学生端回 8000。
2. **Docker 拉取**：已配置 daemon registry-mirrors（docker.m.daocloud.io 等），Valkey pin digest 可拉。
3. 调试期反复重启产生的僵尸 worker 进程（含 multiprocessing 孤儿）会静默偷队列消息——已清理，后续避免在容器外起 taskiq worker。
