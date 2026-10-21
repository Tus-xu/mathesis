# research-vnext（8300）· Research VNext Core

RV02 交付物：**ResearchSession 领域事件模型 / revision / idempotency / 刷新恢复**。

- 端口 **8300**（P00 registry 固化）｜schema **rv8300** ｜队列前缀 `strm:rv8300` ｜对象前缀 `rvs/`
- 事实权威：**PostgreSQL**；Valkey 仅协调（本轮未使用）
- 边界：**不 import** Teacher domain；**不复用** Teacher DTO/schema；**不读** Teacher 表；**不自建**账号库

---

## 目录

```
research_vnext/core/
  bootstrap_env.py  .env 装载器（显式环境变量优先；保证测试隔离不被覆盖）
  domain/        聚合根(ResearchSession) · 9 态状态机 · 领域事件 · 确定性 summary
  contracts/     P00 契约镜像 + 哈希锚定 schema 副本
  db/            8 张表模型 · engine/session_scope（单事务边界）
  application/   session_service（create/transition/resume/pending/summary）
  api/           deps(六层对象级鉴权) · routes(REST+SSE) · app(工厂)
  observability/ trace · redaction · metrics · logging
migrations/      Alembic 单链（0001_rv02，版本表 alembic_version_rv8300）
tests/           unit / contracts / migration / integration
scripts/         run_dev.ps1（启动）· smoke_8300.py（8300 真实 HTTP 冒烟）
                 e2e_real_api.py · trace_probe.py（真实进程证据采集）
docs/            ADR-RV-001（schema 决策）· GO-LIVE-EVIDENCE（上线证据）· CHANGE-REQUEST
```

---

## 运行

```bash
# 1) 依赖（本机无 uv，用隔离 venv）
PY=C:/python13/python.exe            # 任意 >=3.11 解释器即可；本机实测 3.13.5
$PY -m venv .venv
.venv/Scripts/python.exe -m pip install fastapi "uvicorn[standard]" "sqlalchemy>=2.0" \
    "psycopg[binary]>=3.1" alembic pytest httpx pytest-asyncio jsonschema

# 2) 环境变量：复制 .env.example 为 .env（.env 已被 gitignore）
#    .env 由 research_vnext.core.bootstrap_env 自动装载；
#    显式环境变量永远优先，因此仍可用环境变量临时覆盖。

# 3) 迁移（生产唯一建表途径）
#    schema 由迁移幂等自建（migrations/env.py 的 CREATE SCHEMA IF NOT EXISTS），
#    全新空库**只需这一条命令**即可到 head，无需手工 CREATE SCHEMA。
#    当前 head = 0002_rv02_idem_digest（0001 → 0002 为「服务端幂等摘要」增量迁移）
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic heads      # 必须是 0002_rv02_idem_digest (head)

# 4) 启动（推荐；脚本会做启动前自检且不打印任何密钥）
powershell -NoProfile -File scripts/run_dev.ps1 -Port 8300

# 4b) 或手工启动
$env:PYTHONPATH="."; .venv/Scripts/python.exe -m uvicorn research_vnext.core.api.app:app --host 127.0.0.1 --port 8300
```

> **`run_dev.ps1` 是前景开发脚本，不是服务管理器。**
> 它用 `& $py -m uvicorn ...` 把 uvicorn 作为**自身子进程**启动，因此：
> - 在交互终端里 Ctrl+C 会同时结束两者（符合预期）；
> - 但如果由**外部非交互调度**启动（例如某个 agent/CI 的 tool-call 会话结束后回收了它的父
>   shell），包装进程被回收时 uvicorn 会**一并退出**，端口随之关闭。
>
> **长驻运行必须用进程守护**（compose `restart: unless-stopped` / systemd `Restart=always` /
> supervisor）。仓库内目前**没有**任何守护策略 —— 这是已知 go-live 运维缺口，
> 记于 `docs/CHANGE-REQUEST.md` **CR-7**。需要长驻时请直接把
> `python -m uvicorn ...` 交给你的进程管理器，或 `Start-Process` 起独立进程。

> **生产启动不做 runtime DDL**：schema 为空时进程以退出码 3 失败
> （`Application startup failed. Exiting.`），不降级为 `create_all`。**已实测复现**。
>
> **启动路径唯一**：只有 `lifespan` 一个启动钩子，**没有** `@app.on_event("startup")`
> （重复注册会让 DDL 门禁静默失效）。契约测试守卫此点。

---

## API 前缀（两个同时可用）

| 前缀 | 说明 |
|---|---|
| `/api/research-v1` | P00 定稿契约，既有消费者使用 |
| `/api/research-vnext` | 前端 `frontend/src/features/research-vnext/api/researchApi.ts` 实际调用 |

同一组路由**同时挂载**在两个前缀下（前缀由 `config.api_prefixes()` 提供，
可由 `RESEARCH_API_PREFIXES` 覆盖）。两个前缀返回完全相同的结果（已用 SHA256 比对证明）。

---

## 测试

```bash
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests -q
```

| 类别 | 用例数 | 门槛 |
|---|---|---|
| `tests/unit` | 68 | 纯逻辑 + 幂等摘要规范式 |
| `tests/contracts` | 21 | P00 契约哈希锚定 + 四条硬约束 + 双前缀/单一启动路径守卫 |
| `tests/migration` | 6 | 真实 PG，up→down→up 往返 |
| `tests/integration` | 65 | 真实 PG；SSE/重启用真 uvicorn 进程；双前缀真实等价；幂等对抗用例；SSE 游标续传 |

**全量 160 passed**（2026-09-13 实测，Python 3.13.5 + 真实 PG）。
非 PostgreSQL 的 DB URL 会被 `assert_real_postgres` 主动拒绝
（禁止 SQLite / in-memory 冒充生产证据）。

> 幂等语义（FIX-A1，评审 P1-1）：冲突判定以**服务端重算摘要**为准
> （`command_type` + `payload` + `expected_revision`），客户端 `idempotency_fingerprint`
> 仅为契约兼容保留。见 `docs/ADR-RV-002-idempotency-and-sse-cursor.md`。
>
> **运维注意**：`command_record` 中在迁移 `0002` **之前**写入的行没有服务端摘要，
> 其 `client_request_id` **不能**再被重放（会得到 409 而非 REPLAYED），属刻意的 fail-closed。
> 补救方式：换一个新的 `client_request_id`。

> 依赖提示：`tests/contracts/test_p00_conformance.py` 需要 `jsonschema`。
> 本服务 `pyproject.toml` 目前**没有** dependencies 段，需手工安装 —— 见 `docs/CHANGE-REQUEST.md` CR-4。

---

## 关键端点

以下路径在两个前缀下均可用（示例用 `v1`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/research-v1/sessions:create` | 建会话（201） |
| GET | `/api/research-v1/sessions/{id}` | 快照 |
| POST | `/api/research-v1/sessions/{id}:transition` | 状态转换（乐观锁） |
| POST | `/api/research-v1/sessions/{id}:resume` | 断线续传 |
| GET | `/api/research-v1/sessions/{id}/events` | SSE（event id / 心跳 / Last-Event-ID） |
| GET | `/api/research-v1/sessions/{id}/summary` | 版本化确定性摘要 |
| GET | `/api/research-v1/sessions/{id}/pending-actions` | 待确认动作 |
| POST | `…/pending-actions/{pid}:accept\|:reject\|:cancel` | 处置（全审计） |
| GET | `/healthz` `/readyz` `/metrics` | 运维 |

响应头：`X-Idempotency-Status: EXECUTED\|REPLAYED`、`X-Trace-Id`。

---

## 错误码

| 码 | HTTP |
|---|---|
| `ACCESS_CONTEXT_MISSING` / `ACCESS_CONTEXT_INVALID_SIGNATURE` | 401 |
| `TENANT_MISMATCH` / `PROJECT_MISMATCH` / `OBJECT_SCOPE_MISSING` / `CAPABILITY_MISSING` / `SECURITY_VERSION_REVOKED` | 403 |
| `REVISION_CONFLICT` / `IDEMPOTENCY_CONFLICT` / `CONFIRMATION_REQUIRED` / `PARTIAL_NOT_COMPLETED` / `PARTIAL_REASON_REQUIRED` / `PENDING_ACTION_EXPIRED` | 409 |
| `UNKNOWN_EVENT_TYPE` / `UNSUPPORTED_SCHEMA_VERSION` | 422 |

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `RESEARCH_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:54329/research_vnext` | — |
| `RESEARCH_DB_SCHEMA` | `rv8300` | 生产 schema；隔离测试/ E2E 用环境变量覆盖成独立 schema（见 ADR-RV-001） |
| `RESEARCH_ENV` | `prod` | `test` 才允许 `ensure_schema()` |
| `RESEARCH_GATEWAY_HMAC_SECRET` | 空 | **空即 fail closed** |
| `RESEARCH_API_PREFIXES` | `/api/research-v1,/api/research-vnext` | 同时挂载的 REST/SSE 前缀（逗号分隔，可覆盖做灰度/回退） |
| `RESEARCH_SSE_HEARTBEAT_SECONDS` | `15` | — |
| `RESEARCH_SSE_RETENTION_EVENTS` | `1000` | 保留窗口（条数） |
| `RESEARCH_SSE_RETENTION_HOURS` | `24` | 保留窗口（时间） |

---

## 已知契约缺口（前端侧，未修）

见 `docs/CHANGE-REQUEST.md` **CR-6**：前缀已修好，但前端
`researchApi.ts` 仍有两处不匹配 —— `POST /sessions/{id}/messages` 端点在后端**不存在**，
且前端只识别具名 SSE 事件 `snapshot` / `event`，而后端领域事件是**无名帧**。
这两条会让前端 research 页面仍然不可用，需产品/前端决策后处理。

---

详细设计见 `D:\ppt-rescue-control\20260911-math-ai-platform-vnext\RV02\`。
