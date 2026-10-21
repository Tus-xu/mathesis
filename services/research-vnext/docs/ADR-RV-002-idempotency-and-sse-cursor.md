# ADR-RV-002 — 幂等判定权威性与 SSE 续传游标

- **状态**：Accepted
- **日期**：2026-09-13
- **触发**：独立交叉评审 `deliverables/vnext-autonomous/review/WP-A-VERDICT.md`
  finding **P1-1**（幂等信任调用方指纹）与 **P1-2**（SSE cursor 污染 `Last-Event-ID`）
- **决策者**：AGENT-A (`RES-B`)，WP-A FIX-A1 / FIX-A2
- **影响范围**：`command_record` 表（新增 2 列）、迁移 `0002_rv02_idem_digest`、
  `application/session_service.py`、`api/routes.py`
- **证据**：`docs/FIX-EVIDENCE.md`

---

## 1. 背景

评审用两个对抗性探针证明了两条**已声明但实际不成立**的保证：

| ID | 声明 | 实测 |
|---|---|---|
| P1-1 | 同一 `client_request_id + actor + command_type` 只产生一次效果 | 同一 crid + 同一客户端 fingerprint + **不同 payload** → `200 REPLAYED`，新命令的 mutation **被静默丢弃** |
| P1-2 | SSE 支持 `Last-Event-ID` 增量续传 | 流首帧发 `id: cursor-<millis>`，客户端回传该值 → **每次自动重连**都退化为 `SNAPSHOT_REQUIRED` |

两者都不影响服务启动/持久化，但都**破坏了对外承诺**。P1-1 属于数据完整性缺陷
（调用方以为状态变了，实际没变），按 WP-A 的严重度标准为最高优先级。

---

## 2. 决策一（FIX-A1）：以**服务端重算摘要**为唯一权威判据

### 2.1 问题根因

```python
# 修复前（session_service.py:225 / :304）
if existing.idempotency_fingerprint != cmd.idempotency_fingerprint:
    raise IdempotencyConflict(...)
```

`cmd.idempotency_fingerprint` 来自客户端（`contracts/command.py:34`），只校验**形状**
（`^[0-9a-f]{64}$`）。这行代码比较的是"调用方自报的字符串"与"该字符串的存档副本"——
**payload 从未在服务端被哈希**。因此调用方只要保持 fingerprint 不变，就能让语义完全
不同的命令被当作重放吞掉。依赖调用方诚实申报的"保证"不是保证。

### 2.2 决策

新增服务端权威摘要 `canonical_command_digest(cmd, payload)`：

```python
material = {"command_type": ..., "payload": {...}, "expected_revision": ...}
blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
sha256(blob.encode("utf-8"))
```

判定改为：**先**比对客户端 fingerprint（不一致即冲突），**再**比对服务端摘要
（不一致即冲突）。客户端字段保留作 P00 契约兼容，但**不再是唯一依据**。

### 2.3 规范式与字段取舍（逐条理由）

**规范式**：`json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`。
`sort_keys=True` 递归生效，因此嵌套字典的键序也不影响摘要（有测试守卫）。
**明确不用** `repr()` / `hash()` —— 二者稳定性不属于语言契约；也不直接
`json.dumps(dict)` —— 结果受字典插入顺序影响。三者的摘要都不可复现。

**纳入** `command_type` + `payload` + `expected_revision`：

- `command_type` 决定命令语义，必须纳入。
- `payload` 是业务内容本体。
- `expected_revision` **纳入**（评审要求"想清楚再决定"，这是结论与理由）：
  进入摘要的是**调用方声称的前置 revision**。合法重试必然重发同一个 revision，
  因此纳入它**不会**破坏合法重试；而"同 crid + 同 payload + 不同 revision"在乐观锁
  语义下本就是不同命令，冲突才是正确答案。有测试锁定该行为。

**排除** `traceparent` / `deadline` / `payload_ref` / 客户端 `idempotency_fingerprint`：

- `deadline` 在"同一意图的两次重试"之间通常会被**重新计算**；纳入它会把合法重试
  变成**假冲突**。假冲突（服务不可用）比漏检更糟，因此必须排除。
- `traceparent` / 客户端 fingerprint 是基础设施与自报字段，与语义无关。

**关于 `payload_ref`：有意偏离评审建议。** 评审要求"摘要或 `payload_ref` 任一不同即冲突"。
本 ADR 决定**不**把 `payload_ref` 差异作为冲突，理由是：
`payload_ref` 是**指针/引用**（`inline:<sha>` 或对象键），**不是内容本身**。
对**内容**做摘要已经覆盖了语义；再把指针纳入判定，会让"内容相同但引用写法不同"的
合法重试变成假冲突 —— 属于 §2.3 已论证要避免的故障模式。
`payload_ref` 仍然**持久化留痕**（新增列），供审计与排障，只是不参与判定。

### 2.4 历史行：fail closed，不回填

新增列 `canonical_digest` **可空**，且迁移**不回填**历史行 —— 历史行没有保存原始命令
payload，无法重算出可信摘要，**伪造一个摘要比留空更危险**。
运行时对 `canonical_digest IS NULL` 的行采取 **fail closed**：无法证明等价即按冲突拒绝。

**运维影响（必须知情）**：迁移 `0002` 之前写入的 `command_record` 行，
其 `client_request_id` **不能再被重放**（会得到 409 而不是 REPLAYED）。
对"同一 crid 重试"的调用方，补救方式是换一个新 `client_request_id`。
当前 `rv8300` 中这类行全部是 WP-A 期间的冒烟/E2E 测试数据（无真实流量），
因此实际影响为零；**但若在已有真实流量的环境执行本迁移，需要先评估该窗口**。

### 2.5 为什么选"加列"而不是"就地推导"

评审给出了"能避免迁移就避免"的选项。本决策选择**加列**，理由：
响应体 `response_body` 里没有原始命令 payload，**无法**从既有数据推导出摘要。
不加列就只能每次运行时重算并与"某个存储值"比较，而那个存储值不存在 ——
所以**存储是必需的，不是便利**。迁移是纯增量的 `ADD COLUMN`（可空），
`downgrade` 为 `DROP COLUMN`，已实测在**全新库**上 `upgrade head` 一条命令即到 head。

---

## 3. 决策二（FIX-A2）：SSE `id:` 必须是**可续传的真实水位**

### 3.1 问题根因

```python
# 修复前（routes.py）
yield _sse(f"cursor-{int(time.time() * 1000)}", {...}, event="cursor")   # 首帧！客户端会回传它
...
rows = svc.events_after(db2, session_id, after_id=0, limit=500)          # 数值水位从未被使用
```

按 SSE 规范，客户端把最后见到的 `id:` 作为 `Last-Event-ID` 回传。首帧的
`cursor-<millis>` 服务端认不出 → 每次都 `SNAPSHOT_REQUIRED`。
同时 `after_id` 恒为 0，增量路径实际上退化为"取最早 500 条再在 Python 里按字符串游标过滤"。

### 3.2 决策

1. **`id:` 一律是真实的 `domain_event.id`**（`BigInteger` 主键，必然可续传）。
2. **没有可指向的真实事件时不写 `id:` 行**（`_sse(event_id=None, ...)`）。
   宁可不发 id，也不发一个**不可续传**的 id —— 后者会让客户端把无害的元信息帧
   变成一次全量快照。
3. `events_after(after_id=<数值水位>)` 真实生效，**由数据库过滤**。
4. 保留窗口判定改为统一的"窗口"规则（见 `session_service.resume` docstring）：
   游标 **< window_floor**（中间事件被裁掉）或 **> window_ceiling**（服务端从未发出过
   该 id）→ `SNAPSHOT_REQUIRED`；落在窗口内 → `NONE` + 精确增量。
   **空窗口不再等同于"需要快照"** —— 空增量是合法结果。
5. `:resume` 同时接受**数值**与历史 **`evt-*`** 游标形态（后者查库换算），
   避免因游标格式升级打断既有调用方；完全无法解析的值 fail closed 成快照。

### 3.3 为什么保留 `SNAPSHOT_REQUIRED`

`SNAPSHOT_REQUIRED` 本身是**正确**的设计（绝不伪造事件连续）。缺陷是它被**误触发**。
本决策只修正触发条件，不改变"窗口外必须回退快照"的语义。

### 3.4 已知未闭环项（诚实记录）

SSE **live tail** 仍有 `limit=500` 的批次上限；当一次轮询间隔内新增超过 500 条事件时，
余量会在下一 tick 继续补发，但**没有**对"批次被截断"本身做断言。
大事件量（600 条）的正确性已由 **`:resume` 路径**覆盖（与 tail 共用同一
`events_after`/窗口逻辑），tail 的截断行为**未被独立断言**。
判定：FIX-A2 主体**完全闭环**，`limit=500` 截断的边界断言**部分闭环**。
详见 `docs/FIX-EVIDENCE.md` §7。

---

## 4. 后果

**正面**
- 幂等保证重新成立于服务端可独立验证的判据之上，不再依赖调用方诚实。
- 自动重连恢复为增量续传；`Last-Event-ID` 行为符合 SSE 规范。
- 增量查询由数据库执行，不再把最早 500 条拉进内存过滤。

**代价 / 需接受的事实**
- `command_record` 增加 2 列（`canonical_digest`、`payload_ref`）。
- 0002 之前的历史幂等行不可重放（fail closed），见 §2.4。
- 未收录 `payload_ref` 与 `deadline` 于摘要，是有意偏离评审建议（§2.3）。

---

## 5. 回滚

- **FIX-A1**：`alembic downgrade 0001_rv02` 删除两列；同时回滚
  `session_service._check_idempotency_conflict` 与 `_save_idempotency`
  （即恢复旧比较）。**注意**：回滚即重新引入 P1-1 缺陷，仅限应急。
- **FIX-A2**：回滚 `routes.py` 的 `_sse`/`sse_events` 与 `session_service.resume`
  即恢复旧行为（合成 cursor id）。同样会重新引入 P1-2。
- 两者都可独立回滚；无数据破坏性（`ADD/DROP COLUMN` 均为可空列）。
