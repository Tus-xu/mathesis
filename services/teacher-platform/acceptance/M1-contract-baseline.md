# M1 · 契约基线报告（红表）——后端骨架 vs 前端契约差距清单

> B5 集成与验收智能体 · 2026-09-07 · 依据 `agents/B5-INTEGRATION-QA.md §3.M1`
> 端点计数唯一来源：`tests/contract/api_inventory.json`（生成于本报告同日，A0/B3 并行施工中——每次 Gate 报告须重跑 `python tests/contract/export_api_inventory.py --write` 刷新）。
> 纪律：先红后绿是常态。本报告每一条红都有唯一所有者与出口条件；**默认不隐藏任何债务**。

## 1. 基线快照（api_inventory.json）

| 项 | 值 | 说明 |
|---|---|---|
| expected_total | **81**（REST 71 + SSE 10） | 前端 teacherV3.ts 声明 75 ∪ §12.3 classroom 12 ∪ backend-owned 3（并集去重，重叠 9——A0 已把 classroom 端点写进 teacherV3.ts，属契约收敛） |
| backend_actual | **8**（source=openapi，进程内 create_app） | login / upload/presign / tasks / platform/demo/sleep / platform/demo/tasks/{id}/stream / classroom/sessions/{id}/stream / __error_demo__ / healthz |
| missing | **77** | 77/81 = 5.1% 覆盖率（M1 早期正常） |
| extra | **3** | B3 demo/debug 路由：`/platform/demo/sleep`、`/platform/demo/tasks/{id}/stream`、`/__error_demo__`（见 §4 行动 B3-2） |
| method_mismatch | 0 | 通配参数名对账（{id}/{session_id}/{aid} 视为同位） |
| 线缆路径口径 | `/api/teacher-v3/*` | vite 代理不剥 `/api`（vite.config.js:38）；文档族 `/teacher-v3/*` 为 mock 相对口径——已写入 inventory 与 conftest |

## 2. 缺口按所有者归组（missing 77 条，路径前缀 → 所有者，02-ARCHITECTURE §9）

| 所有者 | 缺口 | 代表端点 |
|---|---|---|
| **B1** | generation 全域（5）+ plans（5）+ decks CRUD/导出（7）+ butler（4，含 2 SSE） | `POST /generation/deck(SSE)`、`POST /plans/{id}/confirm`、`POST /butler/chat(SSE)` |
| **B4** | classroom 域（12）+ recognition（5）+ figures/draw（5）+ grading（6）+ catalog 域大部分（15）+ insights/resources | `POST /classroom/sessions`、`POST /recognition/photo-ingest(SSE)`、`GET /quiz/questions` |
| **B3** | catalog 域骨架（today/classes/deck-templates/lesson-templates/recipes ≈5） | `GET /today`（M1 Gate L1 链路依赖） |
| SSE 合计 | 10 通道 0 实装（classroom 教师/学生流 2 条由 B3 传输层先行，业务事件待 B4） | §7.4 十通道 |

> 完整逐条清单见 `tests/contract/api_inventory.json → diff.missing`（含 source 标注），不在此重复。

## 3. 契约测试底座运行结果（pytest，2026-09-07）

```
36 failed, 11 passed, 2 skipped, 20 errors   （tests/ 全量，2.3s）
```

| 分组 | 结果 | 含义 |
|---|---|---|
| **绿证（11）** | JWT 往返/过期拒绝/错误码映射/AuthZ 矩阵 ×4；inventory 计数与方法一致 ×3；IFC-001 落库检查 + B1 无复制 grep ×2 | 测试资产本身可运行且 core 层 JWT/AuthZ/错误码**已达标** |
| **红·端点缺失（30）** | response_shell 16 + sse_sequences 8 + auth_matrix 4 + 导出/审计 2 | 全部断言在真实 app 上 404——B3/B1/B4 实装后自动转绿，无需改测试 |
| **红·数据审计（2）** | §7.1 表 quiz_questions 不存在（B3/B4 迁移）；§7.3 种子 manifest 不存在（B2） | 数据真实性审计已在岗；B2/B4 落地即红转可评 |
| **setup error（20）** | IFC-001 三断言（app/pagination/schemas.py 未实现，B4）×18；classroom 流 compose 用例 ×2 | 红点=IFC-001 缺口清单；compose 用例等全栈 |
| **skip（2）** | 审计 1/5 依赖拍照题/聚类数据 | L2/M3 链路跑后自动参战 |
| compose 档（未计） | 幂等 4 用例 + 课堂流 2 用例 | `TEACHER_CONTRACT_BASE_URL=http://127.0.0.1:8100 pytest -m compose`（当前 8100 被占，见 DEF-002） |

## 4. 缺陷与环境登记（acceptance/defects/）

| DEF | 级 | 所有者 | 一句话 | 出口 |
|---|---|---|---|---|
| DEF-001 | P1 | B3 | `events.py:_alloc_seq` 对同一 Result 二次 `scalar_one_or_none()` → `ResourceClosedError`，**首个 durable 事件即崩**（aiosqlite 实证） | **CLOSED**（B3 已修，见 acceptance/M1-report.md） |
| DEF-003 | P1 | B3 | 幂等中间件 `_IDEM_SKIP_PREFIXES` 漏 `/api` 前缀 → 全部教师端写请求被静默跳过 | **CLOSED**（B3 已修） |
| DEF-004 | **P1** | B3 | `run_task` 成功路径漏调 `_finish` → 任务执行完成仍恒 `running`（Gate 实证：progress 100% 事件齐全、无 done） | Gate `test_valkey_gate.py::test_b` 转绿 |
| DEF-002 | P2 | B3+用户 | **8100 端口被 student-api 占用**（架构定案：学生 8000/教师 8100）；教师端**当前临时驻 8101**（inventory 已自动探测 8101） | 迁回 8100 或 IFC 改 8101 定案 |

## 5. 对各所有者的行动请求（M1 Gate 前置）

- **B3**：① 修 DEF-001（Result 一次性取值）；② 修 DEF-003（skip 前缀改 `/api/teacher-v3` + 单测）；③ 与用户/学生端协调 8100 端口（DEF-002）；④ catalog 骨架 5 端点（today 优先——L1 链路）；⑤ demo/debug 路由（3 条 extra）要么登记进 backend-owned 白名单走 IFC，要么 Gate 前移除。
- **B4**：① `app/pagination/schemas.py + layout.py`（IFC-001 冻结签名，18 个红点出口）；② classroom 域 12 端点（M2-A Gate 前置）；③ quiz_questions 等域表迁移。
- **B1**：generation/plans/decks/butler 21 端点——其中 `POST /plans/{id}/confirm` 与 R5（teacher_confirmed 不可覆写）是契约测试重点。
- **B2**：`corpus/sources/manifest.toml` + 种子题库入库（审计 §7.3 出口；M1 交付物承诺 ≥1000 题）。
- **A0**：无阻断项；classroom 端点已入 teacherV3.ts（与 §12.3 通配一致 ✓）。

## 6. 用户会怎么想（体验评述）

现在的平台对教师而言是「门牌装好了、里面还没有房间」：登录闸机（JWT/40301 门禁）已经会拦人放人，但推开门后没有任何一个页面能给出真实数据。这一层看不见——用户明天来看的判断标准依然是「点开今天要做什么有没有东西」。M1 剩余工作全部是给这些房间通水电，不涉及体验设计变更。

## 7. 已知债务（须用户点头）

1. **audit §7.2 通过是空过**：占位黑名单扫描在域表未建时跳过对应表——域表落地后需重跑才算数（B5 已在用例注释标注）。
2. **inventory 的 frontend 提取基于正则**：A0 若引入非常规调用写法（直拼 fetch 等）会漏提取——B3 Gate 报告时以 OpenAPI 反向复核 extra 列表兜底。
3. **A0/B3 并行施工中**：本报告数字是时点快照，Gate 验收前必须重跑导出器与 pytest 刷新。
