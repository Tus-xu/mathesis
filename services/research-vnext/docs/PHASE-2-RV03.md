# PHASE 2 · RV03 — durable Job Runtime

- **实施者**：AGENT-H (`RES-B`)
- **日期**：2026-09-13
- **状态**：**DONE**（12 条要求全部实现 + chaos 式测试；一处既有边界断言**有意重写**，见 §3）

---

## 1. 十二项交付与实现位置

| # | 要求 | 实现 | 测试 |
|---|---|---|---|
| 1 | **Outbox relay** | `core/application/outbox_relay.py`：`SELECT … FOR UPDATE SKIP LOCKED` 领取 → 发布 → 回填 `published_at`/`attempts`；并发/重启安全 | `test_relay_*`（6 例） |
| 2 | **Queue** | `core/queue/streams.py`：Valkey/Redis Streams，端口 **6380**、前缀 `strm:rv8300`、group `rv8300-workers`、dedupe scope `rv8300`（**来自 `deploy/registry.yml`**） | `test_queue_prefix_and_group_match_registry`、`test_valkey_stream_publish_read_ack`（真实 Valkey） |
| 3 | **Worker** | `core/worker/runtime.py` + `core/worker/entrypoint.py`（可运行：`python -m research_vnext.core.worker.entrypoint`），消费 group、执行 handler、**重启可恢复** | `test_worker_crash_leaves_recoverable_state`、`test_pending_in_db_jobs_are_recovered_after_restart` |
| 4 | **Inbox / 幂等消费者** | `job_inbox` 唯一键 `(event_id, job_id, attempt)`；`try_accept()` 用 `ON CONFLICT DO NOTHING … RETURNING` | `test_inbox_dedupe_*`（3 例） |
| 5 | **Lease + 单调 fencing token** | `claim_job()` 用**条件 UPDATE** 原子迁移并 `fencing_token += 1`；上报结果必须回传当前 token，否则 `StaleFencingToken` | `test_stale_fencing_token_is_rejected`（核心用例） |
| 6 | **Heartbeat / 超时 / max attempts / 退避 / 分类** | `heartbeat()`（**与 progress 分离**）、`report_progress()`、`job_stage_timeout_seconds()` / `job_total_timeout_seconds()`、`backoff_seconds()`（指数 + **全抖动**）、`classify_error()` | `test_heartbeat_*`、`test_backoff_*`、`test_permanent_error_codes_never_retry` |
| 7 | **Cancel** | 持久化 `cancel_requested_at`；QUEUED/RETRYING 立即生效；running 由 worker 在**安全点**（`ctx.stopped()`）退出并保存有效 partial manifest | `test_cancel_*`（3 例） |
| 8 | **DLQ** | `job_dlq`（input ref + policy version + **脱敏** error + trace + replayed_job_id）；重放是**显式管理命令** | `test_dlq_*`（3 例）+ 源码级"无自动重放"断言 |
| 9 | **背压** | `check_backpressure()`：per-user / per-project / per-provider 并发上限 + 队列深度 + 队首年龄；超限 `429/503` + `Retry-After` | `test_*_backpressure*`、`test_enqueue_backpressure_surfaces_as_429_with_retry_after_header` |
| 10 | **对外状态规范** | `public_status()`：内部 `CLAIMED` → 对外 `QUEUED`；`PARTIAL` 投影带 `succeeded_stages` / `failed_stages` / `is_success: false` | `test_claimed_is_not_exposed_as_success`、`test_partial_is_never_rendered_as_green_success` |
| 11 | **Typed job SSE 事件** | `emit_job_event()` 写 domain event + outbox（同事务）：`job.queued/claimed/started/stage_changed/progress/heartbeat/retrying/partial/succeeded/failed/cancelled/dlq/replayed` | `test_typed_job_events_are_emitted` |
| 12 | **Valkey 丢失恢复** | relay 在队列不可用时**不回填** `published_at`；worker 有从 PostgreSQL 领活的兜底路径（`claim_due_jobs`）；**生产禁止进程内队列** | `test_valkey_loss_does_not_lose_truth`、`test_production_refuses_in_process_queue` |

## 2. Registry 冻结值（读自 `deploy/registry.yml`，**不发明**）

```python
SERVICE_PORT  = 8300
DB_SCHEMA     = "rv8300"
QUEUE_PREFIX  = "strm:rv8300"
QUEUE_PORT    = 6380
CONSUMER_GROUP = "rv8300-workers"
DEDUPE_SCOPE  = "rv8300"
```

`queue_stream_key() == "strm:rv8300:jobs"`。有测试锁定这些值。

## 3. 背压的定位（LEAD ADDENDUM 后升级为承重结构）

LEAD ADDENDUM 明确：本平台只有**一家** LLM 供应商、**一个**账号
（用户有意统一，见 `MODEL-INVENTORY.md` §3–§4），**没有任何供应商级容灾**；
而平台此前**没有全局 rate limiting、没有 provider 并发控制**。
因此背压（本文件第 9 项）不是"nice-to-have"，而是**那唯一账号的 RPM/TPM 上限之前
唯一的东西**。

据此做了两件事：

1. **RV03 作业侧**（原有）：`check_backpressure()` —— per-user / per-project /
   per-provider 并发上限 + 队列深度 + 队首年龄 ⇒ `429/503` + `Retry-After`。
2. **LLM 侧**（ADDENDUM 新增 P0 项）：`core/llm/capacity.py` ——
   进程内在飞上限 + 每用户/每会话在飞轮次 + 每用户滑动窗口，
   **在发出请求之前**判定（而不是等供应商回 429，那时配额已被消耗）。
   详见 `docs/PHASE-1-CR6.md` §11。

**这两层是互补的**：作业侧限制"排队的活有多少"，LLM 侧限制"同时在打供应商的请求有多少"。
只有后者能直接保护那个唯一账号。

**诚实边界（已登记 CR-14）**：LLM 容量闸是进程内的（`threading` + `BoundedSemaphore`）。
多副本部署时真实上限 = `副本数 × RESEARCH_MAX_LLM_INFLIGHT`，**不是**配置值。
真正全局的上限需要跨进程计数器；Valkey 已被三个服务共享，
新增容量 key 属跨服务的协调策略，不由本 agent 单方面决定。

## 4. 对既有断言的有意重写（说明理由）

`tests/contracts/test_boundary_guards.py::test_outbox_has_no_worker_or_relay`

**原断言**（RV02 时期）：

```python
assert "def publish" not in app_src
assert "def relay" not in app_src
```

**原文意图**："RV02 只到 durable record，不得有假装已发布的逻辑"。
RV03 的**交付内容就是补上真实 relay**，因此"不存在 relay"这条断言已经被有意取代。

**取代后的断言更强**，守住同一条安全性质（"不得假装已发布"）：

1. relay 必须真实存在，但**不得**挤进 `session_service.py`
   （写路径与投递路径分离，否则"发布"会在业务事务里被顺手调用，
   让"已发布"变成事务的一部分而不是独立可观测的事实）；
2. `session_service` 里 `published_at=` 只能出现在**写入 NULL** 的地方；
3. relay 必须用 `skip_locked=True`，且必须在 `except QueueUnavailable` 分支里
   **不回填** `published_at`。

函数名从 `test_outbox_has_no_worker_or_relay` 改为
`test_outbox_relay_lives_outside_session_service_and_never_fakes_success`。

## 5. 实现期间实测到的三个真实缺陷（都已修，且都有回归测试）

1. **`ON CONFLICT DO NOTHING` + `rowcount` 不可靠**（psycopg3 实测返回 1 而非 0）
   ⇒ inbox 去重静默失效，重复投递全部放行。改用 `RETURNING`。
   证伪实验 #4 证明修好后的测试确实能发现它。
2. **"先 SELECT 判断再改"的领取不是原子的**：4 个并发 worker 全部领走了同一个作业，
   `fencing_token` 被推到 4。改成**单条条件 UPDATE** 后只有一个赢家。
3. **`drain_once` 领到作业后又调 `run_job` 二次领取**，因"租约仍有效"被判不可领取
   ⇒ worker 什么都不干。拆成 `run_job`（领取+执行）与 `_execute_claimed`（只执行）。

另外一个**设计决策**（有注释写明）：`lease_expires_at IS NULL` 且状态为 `CLAIMED`
**不**视为可抢占 —— 那是"正在被别人持有"的中间态。真正的崩溃会留下
**有值且已过期**的租约，恢复不依赖这条路径。

## 6. 新增持久对象（迁移 `0004_rv03_job_runtime`）

- `job_run`：状态 / attempts / max_attempts / lease_owner / lease_expires_at /
  fencing_token / cancel_requested_at / retryable / next_attempt_at /
  partial_detail / stage / progress / last_heartbeat_at / policy_version / trace_id；
  4 个索引（含 `(status, created_at)` 供背压与超时扫描）。
- `job_inbox`：`UNIQUE(event_id, job_id, attempt)`。
- `job_dlq`：可重放（`replayed_job_id` / `replayed_at`）。

`downgrade()` 真实 DROP。

## 7. 证据

```
tests/integration/test_rv03_job_runtime.py => 54 passed
真实 Valkey（6380，独立 stream key strm:rv8300:test:<hex>，不污染生产流）
真实 PostgreSQL 15
```

chaos 覆盖清单（任务书逐条）：

| 要求 | 用例 |
|---|---|
| duplicate publish | `test_duplicate_publish_does_not_execute_twice` |
| inbox dedupe | `test_inbox_dedupe_absorbs_duplicate_delivery`、`test_inbox_dedupe_survives_concurrent_duplicates` |
| worker crash | `test_worker_crash_leaves_recoverable_state` |
| lease expiry | `test_expired_lease_can_be_taken_over_with_higher_fence` |
| stale fencing token rejected | `test_stale_fencing_token_is_rejected` |
| heartbeat interruption | `test_heartbeat_interruption_still_lets_job_retry`、`test_heartbeat_extends_lease_but_does_not_touch_progress` |
| retryable vs permanent | `test_permanent_error_codes_never_retry`、`test_unknown_error_code_defaults_to_no_retry`、`test_retryable_failure_schedules_retry_then_exhausts_to_dlq` |
| DLQ | `test_dlq_stores_replayable_input_and_redacted_error`、`test_dlq_replay_is_not_automatic` |
| cancel | `test_request_cancel_on_queued_job_takes_effect_immediately`、`test_cancel_running_job_exits_at_safe_point_with_partial_manifest`、`test_cancel_requested_never_reports_succeeded` |
| PARTIAL | `test_partial_reports_which_stages_failed`、`test_partial_is_never_rendered_as_green_success` |
| Valkey loss recovery | `test_valkey_loss_does_not_lose_truth`、`test_database_fallback_path_works_without_queue` |

## 8. 本轮**未**完成 / 明确的限制

1. **没有进程守护**：worker 是可运行的入口，但仓库内仍无 systemd/compose unit
   （`deploy/` 是禁止区域 —— 见 CHANGE-REQUEST CR-9）。
2. **`job.total_timeout` 只在配置里存在，未被强制**：目前只强制 **stage** 超时
   （`asyncio.wait_for`）。总超时需要一个独立看门狗协程，本轮未实现。
   在 `config.py` 里保留该配置是为了不隐藏这个缺口。
3. **作业背压是准入控制，不是 provider 侧令牌桶**：给出上限 + 429/Retry-After 语义，
   但没有做真正的速率整形（token bucket / leaky bucket）。
   （注意：**LLM 侧**另有 `core/llm/capacity.py` 的滑动窗口限速，
   见 `docs/PHASE-1-CR6.md` §11 —— 两层是互补的。）
4. **DLQ 重放没有 API 端点**，只有 CLI（`--replay-dlq <id>`）。管理端点需要管理员
   鉴权模型，本轮没有定义，因此不擅自加。
5. **`XAUTOCLAIM`（`claim_stale`）已实现但未接线**到 worker 主循环：
   由 PostgreSQL 兜底路径覆盖了同样的场景。留着是因为 Valkey 侧的 pending
   清理在高吞吐下更省数据库扫描。
6. **作业 SSE 事件走的是会话级 SSE 流**（`/sessions/{id}/events`）。
   独立的作业流端点未实现。
7. **LLM 容量闸只在进程内有效**（`BoundedSemaphore`），多副本下不是全局上限 ——
   已登记为 CHANGE-REQUEST CR-14。这是**唯一账号**保护层的实际弱点，
   必须与部署形态一起决定。
