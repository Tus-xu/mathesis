# ADR-RV-001 — Research VNext 生产 schema 与运行时形态

- **状态**：Accepted
- **日期**：2026-09-13
- **决策者**：AGENT-A (`RES-B`)，WP-A（RV02 Research Core production go-live）
- **影响范围**：`services/research-vnext/` 的迁移目标 schema、Alembic version table 位置、服务启动配置
- **相关事实来源**：`research_vnext/core/config.py`、`migrations/env.py`、`research_vnext/core/db/base.py`、`tests/contracts/test_boundary_guards.py`、`scripts/e2e_real_api.py`、`scripts/trace_probe.py`

---

## 1. 背景

RV02 Research Core（端口 8300）代码完成但**从未部署**：`research_vnext` 库的 `public`
schema 为空，库内只有 `rv8300_e2e` / `rv8300_trace` 两个 E2E 遗留 schema。
服务在 8300 上从未真实运行过。

本 ADR 回答一个必须先定死的问题：**生产环境把 Research Core 的表建在哪个 schema？**

### 1.1 现状机制（先读代码，再决策）

三个位置的 schema 来源必须一起看，否则会得出错误结论：

| 位置 | 取值方式 | 生效时机 |
|---|---|---|
| `research_vnext/core/config.py` | `DB_SCHEMA = "rv8300"` 常量；`db_schema()` 返回 `os.getenv("RESEARCH_DB_SCHEMA", DB_SCHEMA)` | 每次调用 |
| `research_vnext/core/db/base.py` | `MetaData(schema=db_schema(), ...)` | **模块 import 时一次性求值**，之后不可改 |
| `migrations/env.py` | `SCHEMA = db_schema()`；`VERSION_TABLE = "alembic_version_rv8300"` | env.py 执行时 |

由此可得两个关键事实：

1. **schema 是完全可覆盖的**（环境变量优先于常量），所以 `public` 在技术上是可运行的 ——
   这一点必须诚实承认，不能假装做不到。
2. `base.py` 在 **import 期**求值意味着：任何进程都必须在导入 `research_vnext` **之前**
   定好 `RESEARCH_DB_SCHEMA`，否则改不动。`tests/conftest.py` 正是靠这一点实现隔离
   （在 import 前把 schema 固定成 `rv8300_it_<uuid>`）。
   **这个隔离行为是现有测试的地基，不得破坏。**

### 1.2 现有隔离 schema 的使用者（不可破坏的证据链）

| 使用者 | schema | 用途 |
|---|---|---|
| `tests/conftest.py` | `rv8300_it_<uuid12>`（可由 `RESEARCH_DB_SCHEMA` 覆盖） | 集成测试隔离 |
| `tests/migration/test_migration_roundtrip.py` | `rv8300_mig_<uuid12>` | 迁移往返隔离 |
| `scripts/e2e_real_api.py` | `rv8300_e2e`（`E2E_SCHEMA` 可覆盖） | 真实进程 E2E |
| `scripts/trace_probe.py` | `rv8300_trace`（`PROBE_SCHEMA` 可覆盖） | trace 探针 |

---

## 2. 选项

### Option A — 生产使用 `public` schema

把 `RESEARCH_DB_SCHEMA` 设为 `public`，表落在 `public.research_session` 等。

**优点**
- 运维直觉熟悉：`\dt` 直接看到表，DBA 工具默认可见。
- 与"一个服务一个库"的思路一致，跨库查询无需限定 schema。

**缺点（实测/代码级证据）**
1. **直接违反 P00 契约并被现有测试判死。**
   `tests/contracts/test_boundary_guards.py::test_port_and_prefix_frozen` 第 84 行硬断言
   `assert DB_SCHEMA == "rv8300"`，文件头注释写明这是"P00 registry 对 8300 的四条硬约束"之一。
   选 A 必须改这个常量，从而必然让 1 个契约测试失败 —— 属于"削弱断言让事情通过"，被 WP-A 明令禁止。
2. **`downgrade base` 会变成对整个库的破坏性操作。**
   迁移的 `downgrade()` 只 `drop_table`，**不** `drop_schema`。放在 `rv8300` 里，回滚的影响面被
   schema 边界封死；放在 `public` 里，任何未来的 `alembic downgrade base` 都会静默清空该库
   `public` 里的**所有**对象 —— 包括非本服务的对象。目前 `public` 恰好为空，所以现在"看起来没事"，
   这是一个**尚未引爆**的隐患，不是安全。
3. **与既有隔离证据的命名体系断裂。** 五个 schema 里四个都是 `rv8300*` 前缀。
   生产单独用 `public` 会让"8300 服务的数据在哪"这个问题同时有两个答案。
4. **version table 名字自相矛盾。** Alembic version table 叫 `alembic_version_rv8300`
   （`migrations/env.py`）。如果产品表在 `public` 而版本表名字带 `rv8300`，
   版本追踪与数据位置分离，排障成本上升。

### Option B — 生产使用 `rv8300` schema

把表建在 `rv8300.research_session` 等，与 `config.py` 既有默认值一致。

**优点**
- **零代码改动即可满足契约**：`DB_SCHEMA == "rv8300"` 保持为真，契约测试保持绿。
- 与 P00 固化前缀（8300 / `rv8300` / `strm:rv8300` / `rvs/`）命名一致，服务边界在 DB 层可见。
- 回滚的影响面被 schema 边界封死：`downgrade base` + `DROP SCHEMA rv8300 CASCADE`
  就是一个干净、可审计、不可能误伤他人的回滚单元。
- 与五个既有隔离 schema 同一命名体系，运维心智只有一个。

**缺点**
- 跨 schema 查询/迁移其它服务的数据时需要显式限定 schema（本服务不读 Teacher 表，此缺点不适用）。
- DBA 需要知道去 `rv8300` 找表（已写入 README 与 `.env.example`）。

---

## 3. 决策

**采用 Option B：生产 schema = `rv8300`。**

具体落地：

- 生产 `RESEARCH_DB_SCHEMA=rv8300`（`.env` 与 `.env.example` 均显式声明，不依赖默认值的隐式性）。
- **不修改 `config.py` 中的 `DB_SCHEMA = "rv8300"` 常量**，也不修改 `db_schema()` 的读取方式。
- 隔离机制原样保留：`RESEARCH_DB_SCHEMA` 仍可被环境变量覆盖，测试/E2E/探针的独立 schema 行为不变
  （实测见 `docs/GO-LIVE-EVIDENCE.md` §4）。
- Alembic version table 留在 `rv8300.alembic_version_rv8300`，与产品表同 schema。

### 3.1 为什么这不是"顺手选默认值"

Option A 在技术上是可行的（环境变量可覆盖），所以这不是"做不到"的问题。拒绝 A 的理由是
**契约 + 回滚影响面**这两条工程约束，不是技术不可行性。特此记录，避免后来者误以为是遗留惯性。

---

## 4. 理由（汇总）

1. **契约强制**：`test_port_and_prefix_frozen` 断言 `DB_SCHEMA == "rv8300"`。选 A 必须破坏一条
   既有契约测试 —— 与"不得为通过而削弱断言"直接冲突。
2. **回滚安全**：迁移的 `downgrade` 不 drop schema，schema 是唯一能封死回滚影响面的边界。
   在 `public` 上跑本迁移等于把整个库交给一条 `downgrade base`。
3. **命名与边界一致**：P00 已把 `rv8300` 固化为 8300 的前缀；四个既有 schema 同族。
4. **零风险落地**：不需要改动任何现有代码路径，因而不引入回归面。

---

## 5. 后果

**正面**
- 产品表落位明确：`rv8300` 下 8 张 Research Core 表 + 1 张 version table（实测 9 张、24 个索引）。
- `public` 保持空闲，未来其它服务可安全使用，不会被本服务的回滚波及。
- E2E 证据 schema（`rv8300_e2e` / `rv8300_trace`）与生产 schema 平级隔离，互不干扰（已实测未被触碰）。

**代价 / 需接受的事实**
- 排障时必须带 schema 限定（`select ... from rv8300.research_session`）。
- `alembic downgrade base` 后 schema 与 version table 会保留（`downgrade` 不 drop schema），
  这是既有设计，本 ADR 不改；完整回滚需额外一条 `DROP SCHEMA "rv8300" CASCADE`。

**未决策项（明确留给后续）**
- 是否把 `rv8300` 改为 PG role/权限模型（当前生产连接用 superuser `postgres`）。
  这是**真实的生产卫生问题**，但 WP-A 不得改 `deploy/`，故仅记录，见 `docs/CHANGE-REQUEST.md` CR-3。

---

## 6. 回滚（Rollback）

本决策的回滚是**配置级**的，不需要改代码：

### 6.1 回滚到 Option A（`public`）—— 不推荐，仅记录路径

```powershell
# 1) 停服
# 2) 在 rv8300 上把数据导出（若有真实数据）
docker exec math-arena-postgres pg_dump -U postgres -d research_vnext --schema=rv8300 -f /tmp/rv8300.dump
# 3) 以 public 为目标重建
$env:RESEARCH_DB_SCHEMA="public"; .venv\Scripts\python.exe -m alembic upgrade head
```
**前置条件（缺一不可）**：必须同时修改 `research_vnext/core/config.py` 的 `DB_SCHEMA` 常量，
并**同步修改** `tests/contracts/test_boundary_guards.py` 的契约断言。
也就是说：回滚到 A 不是纯配置操作，它是一次**契约变更**，必须先过人工评审。
这正是本 ADR 建议不要走 A 的原因。

### 6.2 回滚到"未部署"状态（撤销 WP-A 的 schema 变更）

```powershell
$env:RESEARCH_DB_SCHEMA="rv8300"; .venv\Scripts\python.exe -m alembic downgrade base
docker exec math-arena-postgres psql -U postgres -d research_vnext -c 'DROP SCHEMA IF EXISTS "rv8300" CASCADE'
```
实测已验证 `downgrade base` 后仅剩 `rv8300.alembic_version_rv8300`（见证据 §3），
再 `upgrade head` 可完整复原 8 张表。**该路径已受测，不是理论推演。**

### 6.3 不受回滚影响的资产（明确保护）

- `rv8300_e2e`、`rv8300_trace`：E2E 证据，**任何情况下不得 DROP**（WP-A 已实测保留）。
- 库 `research_vnext` 本体、`math-arena-postgres` 容器、`teacher-postgres`(54330)、Valkey(6380)：
  本决策不涉及。

---

## 7. 追加决策（LEAD ADDENDUM A-1）— schema bootstrap 的可复现性

### 7.1 审计提出的疑问与实测结论

审计意见认为：生产路径**没有任何东西创建 schema**，因此
`alembic upgrade head` 打在全新库上会失败（`ensure_schema()` 在非 test 环境主动 raise，
E2E 又都覆盖了 `RESEARCH_DB_SCHEMA`，所以生产 schema 从未被建过）。

**该判断与代码不符。** `migrations/env.py:51-54` 在 `run_migrations_online()` 里
**已经**幂等地建 schema：

```python
def run_migrations_online() -> None:
    engine = create_engine(_url(), future=True)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))   # <-- 就在这里
```

`ensure_schema()`（`db/engine.py:53`）是"另一条**被禁止**的 runtime DDL 路径"，
它的存在**不代表**迁移没有自建 schema 的能力。两者是不同机制，不可混为一谈。

**实测（不靠推理）**：建一个**全新空库** `rv8300_fresh_probe`（只有 `public`），
只跑一条命令：

```
$env:RESEARCH_DB_SCHEMA="rv8300"; $env:RESEARCH_ENV="prod"
.venv\Scripts\python.exe -m alembic upgrade head      # 无任何手工 CREATE SCHEMA
→ rc=0；information_schema 显示 rv8300 下 9 张表
```

再做往返：`downgrade base`（rc=0，仅剩 `alembic_version_rv8300`）→ `upgrade head`
（rc=0，8 张 Core 表回来，`version_num=0001_rv02`）。

**结论：不需要任何手工 `CREATE SCHEMA` 步骤。** schema bootstrap 已由迁移幂等完成，
这一点现在被明确写进 runbook 与 `GO-LIVE-EVIDENCE.md` §14（此前只是"隐式成立"）。

### 7.2 为什么仍然选择 Option B（`rv8300`）而不是改用 `public`

审计建议"`public` 是惯例选择，可以消除一整类 'schema 错了' 的失败"。该建议的
**收益是真实的**（更少心智负担），但代价在实测后被确认不可接受：

1. **它是一次契约变更，不是配置切换。** `tests/contracts/test_boundary_guards.py:84`
   硬断言 `DB_SCHEMA == "rv8300"`（P00 registry 固化值）。选 `public` 必须改这条断言。
2. **`downgrade base` 的爆炸半径问题在 `public` 下更严重。**
   迁移的 `downgrade()` 只 `drop_table`，**不** `drop_schema`。这不只是"多留一张表"的问题：
   `version_table_schema=SCHEMA` 意味着**版本表**也钉在 `public`。
   配合 CR-3（当前用 superuser 连接），任何一次 `alembic downgrade base`
   都会在该库 `public` 里执行 `DROP TABLE` 系列 —— 而 `public` 是**共享默认 schema**。
   schema 边界是当前唯一能封死回滚影响面的机制，放弃它换取"少一次 schema 限定"不划算。
3. **权限前提不同。** PG15 起 `public` schema 对 `PUBLIC` 不再默认具备 `CREATE`；
   `CREATE SCHEMA IF NOT EXISTS "public"` 需要库级 `CREATE` 权限。
   用一个**自定义** schema 反而让权限模型更清晰（见 CR-3 的 role 方案）。
4. **可复现性收益为零**：§7.1 已实测 `rv8300` 在全新库上一条命令即达 head。

**决策维持 Option B。** 审计提出的"必须显式、可文档化、可复现"这三条要求，
通过 §7.1 的实测与 runbook 补全**已全部满足**；而"改用 `public`"这一具体手段
因上述 1/2/3 被明确拒绝。此处记录取舍，避免把"建议被否决"误读为"建议未被考虑"。

### 7.3 schema bootstrap 的唯一权威步骤（runbook）

```powershell
# 全新库 → head 的全部所需命令（无手工 CREATE SCHEMA）
$env:RESEARCH_DATABASE_URL = "postgresql+psycopg://<user>:<pw>@<host>:54329/<db>"
$env:RESEARCH_DB_SCHEMA    = "rv8300"
$env:RESEARCH_ENV          = "prod"
.venv\Scripts\python.exe -m alembic upgrade head    # 幂等自建 schema + 建表
.venv\Scripts\python.exe -m alembic current         # 期望：0001_rv02 (head)
```

若要完全清空（含 schema 本身，`downgrade` 不会 drop schema）：

```sql
DROP SCHEMA IF EXISTS "rv8300" CASCADE;
```

### 7.4 `RESEARCH_DB_SCHEMA` 的覆盖能力必须保留

`public` 方案唯一真实的优点是"没有错误 schema 的可能"。但完全移除覆盖能力会
**破坏全部现有隔离证据**（`rv8300_it_*` / `rv8300_mig_*` / `rv8300_e2e` / `rv8300_trace`）。
因此本 ADR 明确：schema 必须**继续**由 `RESEARCH_DB_SCHEMA` 完全驱动，
生产**显式**设为 `rv8300`，而不是依赖 `config.py` 默认值的隐式性 —— 后者才是
"选错 schema 却不自知"的真正来源，已在 `.env` / `.env.example` / `run_dev.ps1` 三处显式声明。

---

## 8. 追加决策（LEAD ADDENDUM A-2 / A-3）

### 8.1 A-2 · 多前缀路由（`/api/research-v1` + `/api/research-vnext`）

- **问题**：前端 `frontend/src/features/research-vnext/api/researchApi.ts` 使用
  `RESEARCH_API_BASE = '/api/research-vnext'`，而服务只挂载 `/api/research-v1`
  → 前端 research 调用**全部 404**（跨仓契约断裂，实测确认）。
- **决策**：**同一组路由同时挂载到两个前缀**，而非改名。
  - `routes.py` 的 `APIRouter` **移除** `prefix=`，前缀上移到 `include_router(router, prefix=p)`；
  - 前缀列表来自 `config.api_prefixes()`，默认 `("/api/research-v1", "/api/research-vnext")`，
    可由 `RESEARCH_API_PREFIXES` 覆盖（灰度/回退能力是真实的，有测试证明）。
- **理由**：改名会打断 P00 既有契约与 48 个既有集成用例；双挂载让**两个消费者都不用改**。
  这是唯一"零破坏"的选项。
- **代价**：OpenAPI 中同一路由出现两次，`operationId` 被 FastAPI 按规范追加路径后缀
  （`create_session_api_research_v1_...`）。这是 include_router 两次的标准行为，不是缺陷。

### 8.2 A-3 · 单一启动路径

- **问题**：`app.py` 同时有 `lifespan` 与 `@app.on_event("startup")` 两个启动钩子，
  且 `on_event` 在 Starlette/FastAPI 已废弃。重复注册让"DDL 生产门禁"有两条实现路径，
  其中一条可能静默不触发（上一轮已发生过同类事故）。
- **决策**：**删除** `on_event` 块，`lifespan` 成为唯一启动路径。
- **证据**：删除后生产门禁**仍然生效** —— 空 schema + `RESEARCH_ENV=prod` 启动
  依然退出码 3（实测），并有测试断言 `app.router.on_startup == []`。
