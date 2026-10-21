# CHANGE-REQUEST — WP-A · RV02 生产上线期间发现的本命名空间外变更需求

- **提出者**：AGENT-A (`RES-B`)
- **日期**：2026-09-13
- **约束**：WP-A 硬约束禁止本 agent 写入 `services/api/`、`services/teacher-platform/`、
  `services/ppt-*/`、`deploy/`、`D:\frontend\` 以及 `services/research-vnext/` 之外任何位置。
  因此以下项目**一律未实施，标记 BLOCKED**，交由有相应权限的人工/agent 处置。

---

## CR-1 — 共享 P00 契约 schema 在本机不可达【BLOCKED】

- **严重度**：中（不阻塞 go-live；影响契约测试的**强度**）
- **现象**：`tests/contracts/test_p00_conformance.py` 依赖**权威** P00 契约 schema 做
  字段等价 + 哈希锚定校验。该权威副本位于 Teacher 平台命名空间：
  `services/teacher-platform/app/platform_contracts/`（当前 untracked）。
- **风险**：若该路径在本机缺失，契约测试只能退回校验
  `research_vnext/core/contracts/schema/platform-contracts.schema.json` 这个**镜像副本**
  （带 `.sha256` 锚定文件）。镜像自校验是"自己跟自己比"，**不能**发现"镜像已与权威漂移"。
  即：契约一致性实际上**未被独立验证**。
- **本次实测**：128 例全绿，说明当前镜像与权威**此刻**一致。但这是一次快照，不是持续保证。
- **需要谁做**：有权写 `services/teacher-platform/` 的人。
- **建议动作**：
  1. 把 `app/platform_contracts/` 纳入版本控制（它是跨服务共享契约，不该 untracked）；
  2. 在 CI 中对权威副本计算 sha256 并与
     `services/research-vnext/research_vnext/core/contracts/schema/platform-contracts.schema.sha256`
     比对，漂移即 fail；
  3. 确认 `platform_contracts` 只在**测试期**导入（生产 `research_vnext/**` 不得依赖），
     现有 `test_boundary_guards.py::FORBIDDEN_IN_PROD` 已守卫此点，保持即可。

---

## CR-2 — 本机系统代理会劫持 `httpx` 对回环地址的请求【BLOCKED（环境级，非仓库文件）】

- **严重度**：中高（**会伪造出 502 假故障**，极易误导排障）
- **现象**：
  ```
  PS> Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' \
        -Name ProxyEnable,ProxyServer
  ProxyEnable ProxyServer
            1 127.0.0.1:7897
  ```
  `httpx` 在 Windows 上**会从 WinINET 注册表读取代理**（即使没有任何 `http_proxy`
  环境变量），于是 `http://127.0.0.1:8300/...` 被送去代理，返回
  `httpx POST :resume -> 502`（无 JSON body）。
- **实测对照**：加 `trust_env=False` 后同一请求 → **200**；`curl.exe` 始终 200
  （curl 不读注册表，只读环境变量）。故 502 **纯属客户端代理行为**，服务无罪。
- **为什么不能自己修**：修它要动注册表 / 系统代理设置，属本机全局环境，不是本服务文件。
- **建议动作**（任一）：
  1. 仓库既有服务已用"清空 proxy 环境变量"规避（`scripts/e2e_real_api.py` 第 36–39 行），
     但那**不足以**覆盖 httpx 的注册表读取路径；
  2. 统一约定：**所有对 127.0.0.1 的服务间/健康检查用 `httpx` 时必须 `trust_env=False`**，
     建议写进 `AGENTS.md`（本 agent 无权修改根 `AGENTS.md`）；
  3. 或为本机代理加 `127.0.0.1;localhost` bypass 列表。
- **本 agent 的本地缓释**：`scripts/smoke_8300.py` 已内置
  `urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))`
  并对 `NO_PROXY=127.0.0.1,localhost` 做了显式设置，故脚本可复现通过。
  **但这是本命名空间内的局部缓解，没有解决其它服务/其它 agent 会踩同一个坑的问题。**

---

## CR-3 — 生产数据库连接使用 superuser `postgres`【BLOCKED】

- **严重度**：中（真实生产卫生问题，不影响本次功能验收）
- **现象**：`RESEARCH_DATABASE_URL` 使用 `postgres` 超级用户连接 `research_vnext`。
  该角色可 DROP 任意 schema/表，且绕过所有行级权限。
- **风险**：应用进程被攻破时，爆炸半径 = 整个 PostgreSQL 实例
  （含 `teacher-postgres` 之外的任何同实例对象）；也无法用 PG 权限模型做租户隔离加固。
- **为什么不能自己修**：需要 (a) 创建专用 role/`CREATE ROLE`，(b) 改连接串，
  (c) 通常在 `deploy/` 或基础设施编排里固化 —— `deploy/` 是 WP-A 禁止区域。
  且 `.env.example` / `config.py` 的默认值是**开发可用性**设计，不该由本 agent 单方面改硬。
- **建议动作**：
  1. 建最小权限 role（例如 `rv8300_app`：仅 `USAGE ON SCHEMA rv8300` +
     `SELECT/INSERT/UPDATE/DELETE` 于 8 张表；**无** DDL 权限）；
  2. 迁移用独立 role（`rv8300_migrator`，有 DDL）—— 迁移与运行时**权限分离**；
  3. 把连接串从 `.env` 提升为部署期注入的 secret（可考虑与现有密钥管理对齐）。
- **备注**：本 agent 已在 `docs/ADR-RV-001-schema-and-runtime.md` §5「未决策项」中
  显式登记此问题，避免它随"go-live 完成"一起被遗忘。

---

## CR-4 — `pyproject.toml` 无依赖声明 + README 测试计数不实【BLOCKED（未擅自改 README）】

- **严重度**：中（直接造成 WP-A 基线首次运行失败）
- **现象 1（依赖）**：`services/research-vnext/pyproject.toml` **完全没有
  `dependencies` / `[project.optional-dependencies]` 段**，只有
  `[tool.pytest.ini_options]` 与 `[tool.alembic]`。全仓无 lock 文件。
  后果：本机按 README 手工装依赖时**必然漏装** `jsonschema`
  （只被 `tests/contracts/test_p00_conformance.py:138` 用到），
  首次基线因此 **1 failed, 127 passed**。
  运行时库（fastapi/uvicorn/sqlalchemy/psycopg/alembic/pytest/httpx）同样无声明，
  任何新机器都无法确定性重建环境。
- **现象 2（README 计数）**：README 第 59–66 行表格写
  `tests/unit 65` / `tests/integration 48` / **全量 133 passed**。
  实测：unit **60**、contracts 14、migration 6、integration **48**、**全量 128**。
  unit 差 5，总分差 5。
- **为什么未自行修**：`pyproject.toml` 在命名空间内、理论上可改，但补一份**猜测的**版本约束
  不如把实测版本固定下来有价值，而固定版本属于"依赖策略"决策（是否引入 lock、是否用 uv），
  超出 WP-A"让服务跑起来"的范围，且与本机无 `uv` 的现实冲突。
  README 计数不实则证据确凿，但 WP-A 要求"不得削弱断言/伪造结果"，
  **改文档数字需要一个可信的计数口径**（例如是否存在被 skip 的用例），本 agent 无权单方面判定。
- **已实测的版本快照**（供固定依赖时直接采用，Python 3.13.5 / win_amd64）：
  ```
  alembic 1.20.0 · fastapi 0.141.1 · httpx 0.28.1 · jsonschema 4.26.0 · psycopg 3.3.5
  psycopg-binary 3.3.5 · pydantic 2.13.5 · pytest 9.1.1 · pytest-asyncio 1.4.0
  SQLAlchemy 2.0.52 · starlette 1.6.0 · uvicorn 0.52.4 · greenlet 3.5.5 · anyio 4.15.1
  ```
- **建议动作**：
  1. 为 `pyproject.toml` 补 `dependencies`（运行时）与 `[project.optional-dependencies].dev`
     （pytest/httpx/jsonschema/pytest-asyncio）；
  2. 既然本机无 `uv`，建议同时提交一份 `requirements.txt`（pip 可直接用）——
     这需要在根 `AGENTS.md` 的"后端 Python 使用 uv"规则上取得例外批准（本 agent 无权改根规则）；
  3. 人工核对 README 的 133/65 口径后修正文档。

---

## CR-5 — README「运行」段落引用了本机不存在的解释器路径【BLOCKED（信息性）】

- **严重度**：低
- **现象**：README 第 32 行写
  `PY=C:/Users/<user>/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe`。
  该绝对路径是**单机个人路径**，不应出现在服务 README 中。
  本次 WP-A 改用 `C:\python13\python.exe`（3.13.5）创建 `.venv`，
  真实解释器为 `services/research-vnext/.venv/Scripts/python.exe`。
- **建议动作**：README 改为相对描述（"用任意 ≥3.11 解释器创建 `.venv`"），
  并补充 `scripts/run_dev.ps1` 作为标准启动入口（本 agent 已新增该脚本）。
- **未自行修的原因**：属于文档规范问题，且改动 README 时会与 CR-4 的计数修正撞在同一文件，
  应由同一次文档整理一并处理，避免多处并行改动同一文件。

---

## CR-6 — 前端 research 契约还有两处断裂，非前缀问题【BLOCKED】

- **严重度**：高（**修好前缀后前端仍不可用**）
- **背景**：LEAD ADDENDUM A-2 只指出前缀不匹配（`/api/research-vnext` vs `/api/research-v1`）。
  A-2 已修复。但核对 `D:\frontend\src\features\research-vnext\api\researchApi.ts`
  全文后，发现**另外两处**断裂；**加前缀无法修复这两条**。
- **为什么不能自己修**：正确修法要么改前端（`D:\frontend\`，本 agent 明令禁止写入），
  要么为后端**发明**产品语义（message 如何映射到 9 态状态机），
  属于产品决策而非实现细节 —— 单方面实现会掩盖真实的设计缺口。

### CR-6.1 前端调用 `POST /sessions/{id}/messages`，后端不存在该端点

```
前端：researchApi.ts:5
  export async function sendResearchMessage(id, payload:{message;expected_revision?;client_message_id?}, signal)
    → transport.post(`/research-vnext/sessions/${id}/messages`, payload, ...)
后端：routes.py 只有 /sessions/{id}:transition，没有任何 /messages 端点
```

- **后果**：前端"发送消息"功能**必然 404**，与前缀修复无关。
- **现实评估**：这不只是"少一个 endpoint"。ResearchSession 是**命令式**的
  （`CommandEnvelope` + `expected_revision` 乐观锁 + 9 态状态机 + confirmation 闸门），
  而前端假设的是**消息式**接口（`{message, expected_revision, client_message_id}`）。
  两者是不同的交互模型。要落地必须先回答：
  1. 一条 message 是否等价于一次 `:transition`？若否，它落在哪个状态？
  2. `client_message_id` 与既有 `client_request_id` 幂等键是同一套还是两套？
     （若两套，`command_record` 的唯一键 `(tenant_id, actor_id, client_request_id)` 需要扩展）
  3. message 是否要触发 LLM / 产生 artifact？RV02 **没有** worker，outbox 只到 PENDING durable record。
- **需要谁做**：产品负责人定语义 + 前端与后端共同定契约。
- **建议动作**：先补一份 message→状态机映射的契约（含幂等键决策），再实现；
  在语义定稿前，**不要**加一个"看起来能用"的转发端点 —— 那会掩盖缺口。

### CR-6.2 SSE 事件名契约不一致（前端会把数据帧当 gap）

```
前端 researchApi.ts onFrame:
  if (f.event==='snapshot' && e.snapshot) h.onSnapshot(...)
  else if (f.event==='event')             h.onEvent(...)
  else                                    h.onGap(e)        // <-- 其余全部落到这里

后端 routes.py _sse(event_id, data, event=None):
  event="cursor"            → 前端 onGap
  event="snapshot_required" → 前端 onGap
  领域事件（_sse(ev_id, ev)，event=None）→ **不写 `event:` 行** → 前端 f.event 为空 → onGap
```

- **后果**：**每一个真实的领域事件都会被前端当作 gap 处理**，`onEvent` 永不触发 ——
  实时更新实际上不会渲染。这是功能性缺陷，不是样式问题。
- **根因**：后端把"领域事件"实现为**无名 SSE 事件**（依赖 SSE 默认 `message` 事件类型），
  而前端显式只认 `snapshot` / `event` 两个具名事件。
- **可选修法（需决策，勿擅自选）**：
  - (a) 后端给领域事件加 `event: event`，并把 `snapshot_required` 改名为 `snapshot`
        —— 改动小，但改变既有 SSE 线上契约，需确认无其它消费者；
  - (b) 前端放宽为"无 event 名时按 `event` 处理" —— 只改前端；
  - (c) 双发：既保留无名帧（兼容既有消费者），又补一个具名帧 —— 会重复数据，需去重约定。
- **本 agent 未实施任何一项**：三条都涉及线上契约或前端代码。
- **附带说明**：`_sse()` 的 `event_id` 语义正常（`Last-Event-ID` 续传可用，已实测），
  本条**只**关于 SSE 的 `event:` 字段命名。

---

## CR-7 — 没有进程守护/重启策略（8300 会在父 shell 被回收时退出）【BLOCKED】

- **严重度**：中高（服务会"莫名消失"，且对外表现为上游 500 而不是崩溃告警）
- **现象（由 LEAD 在统一验证中实测，我已复核进程血缘）**：
  `scripts/run_dev.ps1:79` 用 `& $py @uvicornArgs` 把 uvicorn 作为**自身子进程**启动。
  当它以**非交互方式**被调度（例如 agent/CI 的 tool-call 会话结束时回收了它的父 shell），
  包装进程被回收 → uvicorn **一并退出** → 8300 端口关闭 → 前端 vite 代理返回 500。
  LEAD 观测原文（`deliverables/vnext-autonomous/evidence/EVIDENCE-UNIFIED-VERIFICATION.md`）：
  > `scripts/run_dev.ps1` 启动的 8300 进程**在本次验证过程中自行退出**（首次探测通过，
  > 数分钟后端口 closed，vite 代理随之返回 500）。
- **复核证据**：`Get-CimInstance Win32_Process` 显示 8300 上的 uvicorn 为
  `PID=45664 (PPID=20672) created=09/13/2026 09:07:31`，命令行是
  `python.exe -m uvicorn research_vnext.core.api.app:app --host 127.0.0.1 --port 8300 --log-level warning`
  —— **不是** `run_dev.ps1`（该脚本经 `uvicornArgs` 启动且不带 `--log-level`）。
  即 LEAD 已改用直接托管。我自己的 4 次 `run_dev.ps1` 调用在**交互式存活期**内均正常服务
  （冒烟 27 项全绿），所以这不是"脚本永远起不来"，而是**在无守护的调度模型下会退出**。
- **性质判定（不夸大）**：`run_dev.ps1` 的文件头已自述为生产语义的**启动入口/开发验证脚本**，
  它**不是**服务管理器 —— 这一点本身不算实现缺陷。
  **真正的缺口是仓库内不存在任何守护策略**（无 compose service、无 systemd unit、无 supervisor 配置）。
  对"生产上线"而言这是运维缺口，不是代码 bug。
- **为什么不能自己修**：正确修法要动部署编排（`deploy/`）或新增运维资产，属 WP-A 禁止区域；
  且"用哪种守护"（compose / systemd / NSSM / k8s）是部署决策。
- **建议动作（任一，需部署侧决定）**：
  1. compose 增加 `research-vnext` service + `restart: unless-stopped` + healthcheck 打 `/readyz`；
  2. systemd unit（`Restart=always`、`RestartSec=2`，`ExecStart` 直接调用
     `.venv/Scripts/python.exe -m uvicorn ...`，**不要**包 `run_dev.ps1`）；
  3. 最低限度：把 `/readyz` 接入现有监控，让"进程消失"变成告警而不是 500。
- **本地缓解（文档层，非解决）**：README「运行」段已标注该脚本为**前景开发脚本**，
  并给出"长驻请直接托管 uvicorn 或交进程管理器"的指引。
- **有利条件**：本服务已实现 `/readyz`（含 schema/迁移校验），可直接作为守护层探针端点。

---

## 汇总

| ID | 主题 | 状态 | 阻塞级别 |
|---|---|---|---|
| CR-1 | 共享 P00 契约 schema untracked，契约校验强度不足 | **BLOCKED** | 非阻塞 go-live |
| CR-2 | 系统代理劫持 httpx → 伪造 502 | **BLOCKED** | 非阻塞（已有局部缓解） |
| CR-3 | 生产用 superuser 连接 | **BLOCKED** | 非阻塞，但是真实生产风险 |
| CR-4 | 无依赖声明 + README 计数不实 | **BLOCKED** | 非阻塞（已实测记录） |
| CR-5 | README 引用单机绝对解释器路径 | **BLOCKED** | 非阻塞 |
| CR-6 | 前端契约另有 2 处断裂：`/messages` 端点缺失、SSE 事件名不一致 | **BLOCKED** | **高**：修好前缀后前端**仍不可用** |
| CR-7 | 无进程守护/重启策略，8300 会随父 shell 回收而退出 | **BLOCKED** | 中高：长驻运行前必须解决 |

**关键声明**：以上 7 项**全部未实施**，本 agent 未在上述任何禁止区域内创建或修改任何文件。
WP-A 的 go-live 判定**不依赖**这 7 项的完成 —— 但：
- **CR-6 直接影响前端可用性**，建议在"前端 research 页面可用"的验收前先行决策；
- **CR-7 是长驻运行的前置条件**，正式对外服务前必须解决。

---
---

# CHANGE-REQUEST 续 — WP-H · CR-6 / RV03 / RV09–RV13 期间发现的本命名空间外变更需求

- **提出者**：AGENT-H (`RES-B`)
- **日期**：2026-09-13
- **约束**：与 WP-A 相同（禁止写 `services/api/`、`services/teacher-platform/`、
  `services/ppt-*/`、`deploy/`、`D:\frontend\`，以及 `services/research-vnext/` 之外的任何位置）。
  以下全部**未实施**。

---

## CR-6 状态更新 — 已由 AGENT-H 实现（后端侧）

WP-A 把 CR-6 标为 BLOCKED，理由是"要么改前端，要么为后端发明产品语义，
属产品决策而非实现细节"。**Lead 已给出该决策**：
`deliverables/vnext-autonomous/adr/ADR-RV-003-cr6-message-vs-command.md`
（Option C 双层模型）。AGENT-H 已**逐字实现**该 ADR：

- CR-6.1（`POST /sessions/{id}/messages` 不存在）→ 已实现，`docs/PHASE-1-CR6.md`；
- CR-6.2（SSE 事件名不一致）→ 已实现，线上帧只允许 `snapshot` / `event` / 注释心跳，
  游标走 `id:` 字段；由**真实 uvicorn 线上帧**测试守卫生效。

因此 CR-6 **不再 BLOCKED**。但其中一项仍有外部依赖，另立条目：

---

## CR-8 — 沙箱执行器（WP-I）尚不存在，实验运行只能诚实失败【BLOCKED】

- **严重度**：中（不阻塞 CR-6；阻塞 RV11 的端到端）
- **现象**：AGENT-H 已定义 `ExperimentExecutor` 的**类型化契约**
  （`core/execution/executor.py`：`ExecutionRequest` / `ExecutionResult` / Protocol
  + `HttpExecutor` / `NullExecutor`），并实现了 `experiment.run` 作业 handler。
  但**沙箱服务本身**（WP-I 的交付物）不存在于本机：
  `RESEARCH_EXECUTOR_ENDPOINT` 未配置 ⇒ `NullExecutor` ⇒ 作业以
  `EXECUTOR_NOT_CONFIGURED`（**永久**错误，不重试）失败。
- **为什么不能自己修**：真实沙箱执行 = 容器编排 + 代码/数据挂载 + 资源限额 +
  出网策略。这既超出 RV 命名空间（要动 `deploy/`），也是 **WP-I** 的明确交付范围。
  在契约之外自行实现一个"能跑 Python 的执行器"会造成两个互相不兼容的执行路径。
- **已实测**：`test_experiment_executor_is_honest_when_unconfigured` 与
  `test_experiment_job_fails_permanently_without_executor` 证明**不会伪造一次运行**。
- **需要谁做**：WP-I owner。
- **建议动作**：
  1. WP-I 暴露一个 HTTP 端点，请求/响应形状对齐 `ExecutionRequest` / `ExecutionResult`；
  2. `repro` 必须包含 `imageDigest` / `dependencyLockHash` / `seed` / `codeHash`
     —— 缺任何一项，本服务会把该运行标为**不可复现**（并可能触发 claim 重开）；
  3. **不要**让执行器返回 `claim_holds` 语义：那是 `VerificationRecord` 的职责。
     本服务会忽略执行器对该字段的任何主张（`_experiment_handler` 固定写 `None`）。

---

## CR-9 — 进程守护：8300/worker 都仍无守护策略【BLOCKED】（WP-A CR-7 的延伸）

- **严重度**：中高
- **现象**：WP-A CR-7 已登记 8300 无守护。AGENT-H 新增了 RV03 worker
  （`python -m research_vnext.core.worker.entrypoint`），它**同样**没有守护：
  仓库内不存在 compose service / systemd unit / NSSM 配置。
- **为什么不能自己修**：正确修法要动 `deploy/`（禁止区域），
  且"用哪种守护"是部署决策。
- **建议动作**：在同一个 compose/systemd 变更里同时纳入
  `research-vnext`（uvicorn，探针 `/readyz`）与 `research-vnext-worker`；
  worker 的优雅退出已就绪（`SIGINT`/`SIGTERM` → 在安全点停止领取新活，
  手头作业的租约到期后由别的 worker 接管）。

---

## CR-10 — Valkey 队列与本服务共享实例，需要确认 stream key 不冲突【信息性】

- **严重度**：低
- **现象**：`deploy/registry.yml` 已冻结 `strm:rv8300` / `rv8300-workers` / `rv8300`。
  但**没有**任何机读的"跨服务 stream key 不相交"断言
  （teacher-platform 用 `strm:tp8100`、ppt-studio-v2 用 `strm:ps8200`）。
- **风险**：未来有人新增服务并复用前缀时，两个服务的 worker 会读到彼此的消息。
  `job_inbox` 的唯一键不含服务标识，因此**跨服务误读**不会被本服务的去重挡住。
- **本 agent 的本地缓解**：`test_queue_prefix_and_group_match_registry`
  锁定本服务的三个值为 registry 冻结值；测试用独立 stream key
  （`strm:rv8300:test:<hex>`）避免污染生产流。
- **建议动作**：把 WP-F 提到的 `assert_policies_disjoint`（registry.yml 已声明）
  真正实现成 CI 门禁，对**全部**服务的 `stream_prefix` / `consumer_group` /
  `dedupe_scope` 做两两不相交断言。

---

## CR-11 — `pyproject.toml` 依然没有依赖声明，新增依赖只能手工 pip 安装【BLOCKED】

- **严重度**：中（可重现性）
- **现象**：WP-A CR-4 已登记此问题。AGENT-H 本轮**新增了两项运行时依赖**：
  - `redis`（Valkey Streams 客户端）—— RV03 队列必需；
  - `litellm`（LLM 网关）—— CR-6 模型调用必需（惰性导入）。

  两者都是通过 `pip install` 装进 `.venv` 的，**没有**写进任何依赖清单
  （因为 `pyproject.toml` 里就没有 `dependencies` 段）。新机器按 README 重建环境
  会缺这两个包：`redis` 缺失 ⇒ 队列直接 `QueueUnavailable`；
  `litellm` 缺失 ⇒ 只在**真实调用模型**时才报错（惰性导入）。
- **为什么不能自己修**：补一份**猜测的**版本约束不如把实测版本固定下来有价值，
  而"是否引入 lock 文件 / 是否用 uv"属依赖策略决策；且本机无 `uv`，
  与根 `AGENTS.md` 的"后端 Python 使用 uv"规则冲突 —— 需要人工给例外。
- **已实测的版本**（供固定依赖时直接采用，Python 3.13.5 / win_amd64）：
  ```
  redis 8.1.0 · litellm 1.100.1（+ openai 2.54.0, aiohttp 3.14.3, tiktoken 0.14.0,
  boto3 1.43.93, tokenizers 0.23.2, huggingface-hub 1.31.0, jinja2 3.1.6, …）
  ```
  `litellm` 的传递依赖较重（约 30 个包）。如果这是不可接受的体积，
  备选方案是只保留一个约 120 行的 OpenAI 兼容 httpx 客户端
  （`Backend` Protocol 就是为此留的替换点）—— 但那是**依赖策略决策**，
  不应由本 agent 单方面决定。
- **建议动作**：
  1. 为 `pyproject.toml` 补 `dependencies`（含 `redis`、`litellm`）；
  2. 若判定 litellm 过重，明确批准"使用 `RESEARCH_LLM_BACKEND=openai-http` 的 httpx 实现"
     并把它写进 ADR（当前代码**只**提供 LiteLLM 实现，无 httpx 实现）；
  3. 提交 `requirements.txt` 或 lock 文件（需根规则例外批准）。

---

## CR-12 — 本机 `.env` 未配置 LLM 凭据，真实模型调用未被端到端验证【BLOCKED】

- **严重度**：**高**（影响"CR-6 真的可用"的证据强度）
- **现象**：`RESEARCH_LLM_API_KEY` / `SPARK_API_PASSWORD` 均为空。
  LG-6 相关的全部测试只有两条路径：
  1. **桩后端**（`StubBackend`）—— 验证"收到模型输出之后平台做了什么"；
  2. **未配置诚实降级** —— 验证"没有凭据时不编造回答"。

  **没有任何一次对讯飞 MaaS 的真实调用**被验证过。
  因此下列事情**未被证明**：真实模型是否遵循"只输出 JSON"的约束、
  真实失败模式（超时/限流）是否被正确分类、`reasoning_content` 回退路径是否真的必要。
- **为什么不能自己修**：本 agent 没有凭据，且**禁止**打印/提交任何真实密钥。
  向外部端点发送请求也需要人工批准（外网 + 计费）。
- **有利条件**：网关已按"读环境变量即可"设计；配置
  `RESEARCH_LLM_API_KEY=<key>` 后**不需要改任何代码**即可端到端验证。
- **建议动作**：由持有凭据的人执行一次真实端到端：
  ```
  $env:RESEARCH_LLM_API_KEY = '<key>'          # 不要写进任何文件
  cd services/research-vnext
  .venv\Scripts\python.exe -m pytest tests/integration/test_messages_cr6.py -q
  # 或手工：POST /api/research-vnext/sessions/{id}/messages，观察
  #         research_ai_run.status / provider / model / tokens
  ```
  若失败，`research_ai_run.error_code` 会给出分类（`LLM_CALL_FAILED` /
  `PROPOSAL_NOT_JSON` / `PROPOSAL_SCHEMA_INVALID` 等），可直接定位。

---

## CR-13 — 前端仍不会渲染 `artifactFocus.objects` 与 `pending-actions`（信息性）

- **严重度**：中（产品可用性，不影响后端正确性）
- **现象**：AGENT-H 在 `ResearchSnapshot` 上以**加法式**补齐了
  `artifactFocus.objects.{intake, problemMap, researchMap, experimentPlan,
  experimentRuns, verifications, paperSpec, reviewFindings}`，
  以及 `capabilities` 的五个必需 key。
  但前端当前的 `researchApi.ts` 只有 `getResearchSession` / `sendResearchMessage`，
  **没有**调用 `GET /sessions/{id}/pending-actions`、也没有 confirm/reject 调用。
  即：**AI 产生了提案，但研究者在前端没有"确认"按钮**。
- **为什么不能自己修**：`D:\frontend\` 是本 agent 的禁止区域。
- **建议动作**（按价值排序）：
  1. 前端增加"待确认提案"面板，调用
     `GET /api/research-vnext/sessions/{id}/pending-actions`
     （已返回 `object_ref` 与 `proposal_kind`，足以渲染）；
     再调用 `POST .../pending-actions/{pid}:accept|reject`；
  2. 消费 `artifactFocus.objects` 渲染 ProblemMap / ResearchMap / ModelSpec 面板
     （注意：`problem_map` 与 `model_spec` 目前**没有** proposal kind，见
     `docs/PHASE-3-DOMAIN.md` §7 第 1、2 条 —— 后端缺口，不是前端问题）；
  3. 消费 `GET /api/research-vnext/sessions/{id}/jobs` 与 `/jobs/{id}:cancel`
     渲染作业进度（typed job 事件已通过 SSE `event: event` 下发）。

---

## 汇总（WP-H）

| ID | 主题 | 状态 | 阻塞级别 |
|---|---|---|---|
| CR-6 | 前端 research 契约断裂（消息端点 + SSE 事件名） | **已由 WP-H 实现（后端侧）** | 解除 |
| CR-8 | 沙箱执行器（WP-I）不存在 | **BLOCKED** | 中：RV11 端到端不可验证 |
| CR-9 | 8300 与 worker 无进程守护 | **BLOCKED** | 中高：长驻前置条件 |
| CR-10 | 跨服务 stream key 不相交断言未实现 | **BLOCKED（信息性）** | 低 |
| CR-11 | `pyproject.toml` 无依赖声明，新增 redis/litellm 未登记 | **BLOCKED** | 中：可重现性 |
| CR-12 | 无 LLM 凭据，真实模型调用未端到端验证 | **BLOCKED** | **高**：证据强度 |
| CR-13 | 前端不渲染提案确认与作业进度 | **BLOCKED（信息性）** | 中：产品可用性 |

**关键声明**：以上新增 6 项**全部未实施**（CR-6 已由本 agent 在自身命名空间内完成后端侧）。
本 agent 未在上述任何禁止区域内创建或修改任何文件。

---
---

# CHANGE-REQUEST 续 2 — WP-H · 按 LEAD ADDENDUM（模型参数 / 单供应商拓扑）的补充

- **日期**：2026-09-13
- **依据**：`deliverables/vnext-autonomous/MODEL-INVENTORY.md` + LEAD ADDENDUM
- **本命名空间内已实施**：网关端点逐字使用、账号↔模型配对可配置 + 启动校验、
  `/llmz` 可观测端点、每供应商容量闸、轮次重试端点（详见 `docs/PHASE-1-CR6.md` §9–§12）
- **以下仍未实施**（超出命名空间或属他人所有权）

---

## CR-14 — LLM 容量闸是**进程内**的，多副本下不是全局上限【BLOCKED】

- **严重度**：**高**（直接决定单账号会不会被打爆）
- **背景**：LEAD ADDENDUM 明确指出「平台**没有全局 rate limiting、没有 provider 并发控制**，
  所以你的背压工作是那唯一账号 RPM/TPM 上限之前唯一的东西 —— 按 P0 对待」。
  AGENT-H 已实现（`core/llm/capacity.py`）：
  1. 进程内在飞上限（`RESEARCH_MAX_LLM_INFLIGHT`，默认 4）；
  2. 每用户在飞轮次（`RESEARCH_MAX_LLM_TURNS_PER_USER`，默认 2）；
  3. 每会话在飞轮次（`RESEARCH_MAX_LLM_TURNS_PER_SESSION`，默认 2）；
  4. 每用户滑动窗口调用数（`RESEARCH_MAX_LLM_CALLS_PER_WINDOW`，默认 20/60s）。
  超限一律 `429 BACKPRESSURE` + `Retry-After`，且**在发出请求之前**判定
  （而不是等供应商回 429 才反应 —— 那时配额已经被消耗）。
- **本机实测限制（必须说清）**：闸门是 `threading` + `BoundedSemaphore`，
  **只在单个进程内有效**。`uvicorn --workers N` 或多副本部署时，
  真实全局上限 = `N × RESEARCH_MAX_LLM_INFLIGHT`，**不是**配置值。
  这对一个只有单账号的供应商来说是实质性偏差。
- **为什么不能自己修**：真正的全局上限需要一个跨进程共享计数器。
  本服务已有 Valkey（6380，`strm:rv8300`），技术上可做，
  但**同一 Valkey 被 teacher-platform / ppt-studio-v2 共享**，
  新增 `rv8300:llm:inflight` 之类的 key 属于跨服务的容量协调策略，
  且"全局限流是对每个服务各自做、还是平台统一做"是架构决策。
- **建议动作（任一）**：
  1. 由平台层（8000 gateway）做**统一**的 provider 并发/RPM 控制，
     RV 侧只保留本地兜底 —— 首选，因为容量是平台级资源；
  2. 或批准 RV 侧用 Valkey 计数器实现跨进程上限
     （需要约定 key 前缀与 TTL 策略，并登记进 `deploy/registry.yml`）；
  3. 至少：把 `RESEARCH_MAX_LLM_INFLIGHT` 写进部署清单，
     并按"期望副本数"手工折算，同时在监控里对 429 计数告警。

---

## CR-15 — `EMBEDDING_*` 仍指向废弃的 768 维端点；Research 的证据检索依赖它【BLOCKED】

- **严重度**：中高（RV12「证据支撑 claim」的可信度依赖它）
- **现象**：`MODEL-INVENTORY.md` §5.1 记录：`.env` 里
  `EMBEDDING_BASE_URL=https://maas-api.cn-huabei-1.xf-yun.com/v2`、
  `EMBEDDING_MODEL=xop3qwen8bembedding`（**768 维，且端点 404**），
  而库内权威配置 `system_configs['embedding']` 已指向
  **本地 `bge-m3` @ `http://127.0.0.1:8080`，1024 维**。
- **对 Research 的具体影响**：`deploy/registry.yml` 与
  `research_vnext/core/config.py` 都声明 `EMBEDDING_DIMS = 768`，
  而 ADR-VN-05 的决策与库内权威配置是 **1024**。
  RV12 的 claim 支撑质量依赖"证据是否真的相关、是否可检索"，
  若嵌入端点是 404，检索层要么退化到关键词匹配，要么静默返回空 ——
  **两者都会让 claim 的 SUPPORTED 判定失去意义**。
- **为什么不能自己修**：`EMBEDDING_*` 是**跨服务共享**配置（`.env` 在仓库根，
  归 Lead 单点改并留 diff）；且 `EMBEDDING_DIMS` 与向量列类型绑定，
  改动会波及 teacher-platform 的 collection。本 agent 无权动这两处。
- **本 agent 的现状（诚实说明）**：RV 服务**本轮不自己做 embedding**
  —— `evidence_card` 只持有引用与授权位，检索能力归平台资产层。
  因此这个 404 **不会**让 RV 的测试变红，但也意味着
  **证据检索这条链路在 Research 侧实际上尚未接通**。
- **需要谁做**：Lead（按 `MODEL-INVENTORY.md` §5.1 的修复方向）。
- **建议动作**：
  1. `EMBEDDING_BASE_URL=http://127.0.0.1:8080`、显式 `EMBEDDING_DIMS=1024`；
  2. 把 `bge-m3` 本地服务**固化为受管服务**（compose + `restart: unless-stopped`）
     —— 否则重启机器即静默失效，而失效表现为"检索结果变差"这种极难发现的现象；
  3. 同步修正 `deploy/registry.yml` 里 `research-vnext-8300: embedding_dims: 768`
     这一条（**本 agent 不得修改该文件**）。

---

## CR-16 — 无第二供应商 ⇒ 需要"容量事件"告警，而非仅靠 429【BLOCKED（运维）】

- **严重度**：中高
- **现象**：单一供应商、单一账号 = 单点。AGENT-H 已让失败**可识别**
  （`LLM_RATE_LIMITED` / `LLM_MODEL_ACCOUNT_MISMATCH` / `LLM_UNAUTHORIZED`）
  且**可恢复**（`POST /sessions/{id}/messages/{mid}:retry`，不新增用户消息）。
  但"可识别"只在有人看的时候有用。
- **缺什么**：没有任何东西把"429 开始变多"变成**主动告警**。
  在单点拓扑下，这等于"整体不可用"的第一个信号被埋在一堆 200 里。
- **为什么不能自己修**：这属于监控/告警栈（`deploy/` 或平台 dashboard），
  是 WP-H 禁止区域；且"告警发到哪、阈值多少"是运维决策。
- **建议动作**：
  1. 对 `research_ai_run.error_code IN ('LLM_RATE_LIMITED','LLM_UNAUTHORIZED','LLM_MODEL_ACCOUNT_MISMATCH')`
     的**计数**建告警（这些错误码就是为此设计的）；
  2. 把 `GET /llmz` 接入存活探测：它返回 503 + `model-account-mismatch` 时，
     这是**配置错误**而不是瞬时故障，应当直接告警而不是自动重启；
  3. 监控 `job_dlq` 与 LLM 429 的比值 —— 三者一起升高说明账号配额已被打满。

**关键声明（续 2）**：CR-14 / CR-15 / CR-16 **全部未实施**。
本 agent 未在 `deploy/`、仓库根 `.env` 或任何其它服务目录内创建或修改任何文件。
