# B5 Gate M1 集成报告（摘要）

> 2026-09-07（第三版，随并行实装滚动刷新）· 用户已预授权自动推进（Gate 放行不再等待人工裁决，按证据自裁决）。
> 判定依据：`02-ARCHITECTURE §17`（M1 = 骨架健康 + ValkeyCompatibilityGate PASS）+ `03-ACCEPTANCE §9` 交付纪律。

## Gate 判定：**BLOCKED（不放行 M2-A 合入）——ValkeyCompatibilityGate 已完整 PASS，余项为骨架端点与端口收口**

| Gate 条件 | 判定 | 证据 |
|---|---|---|
| 骨架健康 | ⚠️ 技术全绿、**端口偏移未收口** | `GET :8101/healthz` → `{"status":"ok","checks":{"db":"up","valkey":"up","minio":"up",…}}`；compose 全栈（teacher-postgres/valkey 9.1.2-alpine pin+digest/minio）healthy。但教师端驻 **8101**（架构定案 8100，DEF-002 未收口）；`/today` 等骨架端点未实装 |
| ValkeyCompatibilityGate | ✅ **PASS（B3 主跑 + B5 复核双侧全绿）** | **B3 主跑**：`test_valkey_compat.py` 5 用例（enqueue→consume→ACK→result / crash→XAUTOCLAIM 重投 / 进程重启重连 / result expiry，真实 valkey/valkey:9.1.2-alpine 容器），出口标记 `VALKEY_TASKIQ_COMPAT=VERIFIED`。**B5 复核**：`test_valkey_gate.py` 4 用例全绿——传输原语（XREADGROUP/XACK/XAUTOCLAIM/maxlen/PubSub）＋真实 worker CLI 全链（enqueue→run_task→demo_sleep 事件双通道→DB 终态 done）＋崩溃恢复（杀 worker→claim-worker 1.5s 认领重投→done，P10 实证）＋结果后端。DEF-004 已由 B3 修复（成功路径补 `_finish(done)`），B5 复跑验证关闭 |

## 契约（api_inventory.json，实时对账）

- expected **81**（REST 71 + SSE 10）＝前端 75（A0 已并入 9 条 classroom 端点，收敛✓）∪ §12.3 classroom 12 ∪ backend-owned 3；
- backend_actual **8**（openapi @8101）｜missing **77**｜extra **3**（demo/debug 路由，待 B3 登记 IFC 或移除）｜method_mismatch 0；
- 覆盖率 8/81 ≈ 9.9%（B5 资产就位后 B1–B4 实装即自动转绿，测试零改动）。

## 链路（M1 覆盖 L1 前置：登录→今日工作台）

- ✅ 登录/门禁内核：JWT 签发/过期拒绝/40301/40100/AuthZ 矩阵 **core 层 11 绿**；
- ❌ `GET /today` 未实装（B3 catalog）——L1 链路不可走查；
- 幂等：`/api` 前缀修复（DEF-003 CLOSED ✅）已由 B3 落地；窗口重放 compose 用例待 8101 上复跑。

## 缺陷总账（acceptance/defects/）

| DEF | 级 | 所有者 | 状态 |
|---|---|---|---|
| DEF-001 events.py Result 二次消费 → durable 事件崩溃 | P1 | B3 | **CLOSED**（B3 已修复，B5 复核代码 ✓，Gate 复跑验证中） |
| DEF-002 8100 被 student-api 占用 | P2 | B3+用户 | OPEN → 已用临时规避（教师端驻 8101），**收口动作**：迁回 8100 或 IFC 改定案 |
| DEF-003 幂等中间件前缀错位 | P1 | B3 | **CLOSED**（`_IDEM_SKIP_PREFIXES=("/api/teacher-v3",)`） |
| DEF-004 run_task 成功路径漏 `_finish` → 任务恒 running | **P1** | B3 | **CLOSED**（B3 补成功路径 `_finish(done)`，Gate `test_b` 全链转绿） |
| DEF-005 classroom 开课 class_id 未校验 → 500 | P2 | B4 | OPEN（工作树瞬态证据；M2-A 前修复） |

## B5 M1 资产（已全部落地，B1–B4 实装即转绿）

契约套件（响应壳/鉴权矩阵/SSE 十通道/幂等/IFC-001/inventory/课堂契约）+ 数据审计五项 + perf harness（P1/P5/P8 locust、P6/P7/P9 课堂模拟、render_smoke COM+XML）+ 71 条契约事实清单（`notes/B5-notes.md`）。IFC-001 已落库 `docs/handoff/`。

## 结论（自裁决）

**M1 BLOCKED，不放行 M2-A 合入**。硬门禁（ValkeyCompatibilityGate）已双侧全绿；剩余出口条件：
1. `GET /today` 等骨架端点实装 → L1 前置走通（当前 catalog 域 404）；
2. 端口收口：8100 迁回或 IFC 改 8101 定案（DEF-002）；
3. demo/debug 3 条 extra 路由：IFC 登记或移除；
4. B4 修 DEF-005（class_id 校验）。

## 基线数字（第三版时点）

pytest 全量 **174 绿 / 45 红 / 2 skip / 23 setup-error**（红=端点未实装 404 为主；绿含 B1 单测 46 全绿、B3 平台壳、契约底座可运行证明）。库存对账：expected 81 / actual 8（live @8101）/ missing 77 / extra 3。M2-A 阶段资产已预置（课堂契约套件 + compose 用例），B4 classroom 域落地即参战。

M2-A 阶段工作不受阻：课堂契约测试套件（状态机矩阵/token 作用域/snapshot 重放/作答幂等）已预置，B4 classroom 域落地即参战。
