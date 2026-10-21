# PHASE 1 · CR-6 — 让 Research 前端真正可用

- **实施者**：AGENT-H (`RES-B`)
- **日期**：2026-09-13
- **权威依据**：`deliverables/vnext-autonomous/adr/ADR-RV-003-cr6-message-vs-command.md`
  （Option C 双层模型）
- **状态**：**DONE**（含一处既有断言的有意更新，见 §6）

---

## 1. 实现契约（逐条对照 ADR-RV-003）

### 1.1 `POST /api/research-{v1,vnext}/sessions/{session_id}/messages`

请求体（`ADR-RV-003` §3.1）：

```json
{ "message": "...", "expected_revision": 3, "client_message_id": "c-8f2a...", "attachments": [] }
```

| 项 | 实现 |
|---|---|
| `message` | 必填，`1..8000`；空/超长 → `422 MESSAGE_INVALID` |
| `expected_revision` | 可选；不匹配 → `409 REVISION_CONFLICT`，body 含 `expected`/`current`/`snapshot_ref` |
| `client_message_id` | 可选，正则 `^[A-Za-z0-9._:-]{8,128}$`；映射为内部 `client_request_id`；**缺省则幂等不生效**（服务端生成 `auto-<hex>`） |
| `attachments` | 本轮契约留空数组；非空 → `422`（**明确拒绝**，不静默忽略） |
| 未认证 | `401`（`ACCESS_CONTEXT_MISSING` / `ACCESS_CONTEXT_INVALID_SIGNATURE`） |
| 对象级鉴权 | tenant → project/session scope → owner → capability → security_version（复用 `authorize_session`）；失败 `403` |
| 新写入 | `201` + `X-Idempotency-Status: EXECUTED` |
| 幂等重放 | `200` + `X-Idempotency-Status: REPLAYED`，返回**原结果**，**不**再调模型 |
| 同 id 不同内容 | `409 IDEMPOTENCY_CONFLICT`（服务端 `canonical_digest` 为唯一权威判据） |
| 响应体 | **完整 `ResearchSnapshot`**（不是单条消息） |
| 双前缀 | 路由由 `include_router(prefix=p)` 注入，`/api/research-v1` 与 `/api/research-vnext` 同时挂载，有测试守卫 |

### 1.2 `GET /api/research-{v1,vnext}/sessions/{session_id}`

**加法式变更**：既有内部字段 `session` / `revision` / `summary_ref` / `trace_hint`
**原样保留**，新增 `timeline` / `evidence` / `capabilities` / `artifactFocus` /
`problem` / `snapshot_ref` / `contract_version`。

### 1.3 `ResearchSnapshot` 形状（前端 adapter 硬契约）

前端 `adaptResearchSnapshot` 的硬检查已逐条复刻成测试
（`tests/integration/test_cr6_frontend_contract.py`）：

- `timeline` / `evidence` / `capabilities` **任何路径下都是数组**（不是 `null`、不是缺失）；
- `capabilities` **恰好**包含五个 key：`research-session` / `research-message` /
  `research-stream` / `research-evidence` / `research-artifact`，
  每项 `{key, label, state}`，`state ∈ available|capability-disabled|unavailable|forbidden`；
- `timeline` 项：`{id, role, text, createdAt, evidenceRefs}`，`role ∈ user|assistant|system`，
  `evidenceRefs` 永远是数组；
- 无产物时 `artifactFocus` 为 `null`（返回空壳对象会让前端以为有产物）。

### 1.4 能力状态的**诚实**语义（降级路径）

| 情形 | `research-message.state` | `reason` |
|---|---|---|
| 模型正常返回并通过校验 | `available` | `""` |
| **未配置** LLM 凭据 | `unavailable` | `llm_not_configured` |
| 模型调用失败（超时/HTTP 错误） | `capability-disabled` | `llm_call_failed` 等 |
| 模型输出违反契约/领域规则 | `capability-disabled` | `proposal_not_json` / `map_dangling_edge` 等 |

降级时 assistant message 是**明确的说明文本**，并且
`research_ai_run.error_code` 记录真实原因。**绝不编造助手回答**。

### 1.5 SSE 事件名对齐（CR-6b）

线上帧**只允许三类**：

| 场景 | 帧 |
|---|---|
| 连接建立 / 全量对齐 | `event: snapshot`，`data: {"snapshot": <ResearchSnapshot>}`，**无 `id:`** |
| 游标超出保留窗口 | `event: snapshot`，`data: {"snapshot": ..., "reason": "cursor_expired"}` |
| 领域事件增量 | `event: event`，`data:` = domain event，`id:` = **真实数值** `domain_event.id` |
| 心跳 | `: ping <iso8601>` 注释帧，**无 event 名、无 `id:`** |

- **已彻底移除** `event: cursor`（前端会落 `onGap`）；
- **已彻底移除** `event: snapshot_required`（前端会落 `onGap` ⇒ 静默无操作）；
- **不存在**无名帧承载 domain event；
- 游标走标准 `id:` 字段。

以上全部由 `tests/integration/test_sse_wire_cr6b.py` **读取真实 uvicorn 进程的线上字节**验证
（不是断言 `:resume` 的返回值 —— 那测不到本缺陷）。端口段 8355–8360。

### 1.6 会话内部一致性（发现的既有缺陷）

`GET /sessions/{id}/pending-actions` 此前**不过滤状态**，会把已 `ACCEPTED` 的项也返回。
调用方拿列表第一项去 accept 必然 409。已修为只返回 `PENDING`，并在代码注释里写明原因。

---

## 2. 事务结构（诚实说明，不假装是单事务）

ADR-RV-003 §4 要求"一次调用在同一事务内完成"。实际实现是**两段事务 + 幂等兜底**，
因为"一次调用"里包含一次可能长达 120s 的模型调用：

```
tx1（立即提交）：锁会话行 → 分配 seq → 写 user message + domain event + outbox
     ↓（事务外调用模型）
tx2（一次提交）：写 assistant message（+ 需要时 PendingAction）+ domain event
                + outbox + 幂等记录
```

- `tx1` 与 `tx2` **各自**满足"domain event 与 outbox 同事务"的铁律（有测试逐行验证）；
- 不让模型时延占着数据库行锁（否则一次慢调用会阻塞整个会话的所有写入）；
- 跨事务原子性由**幂等记录** + `research_message.client_request_id` 兜住：
  在 tx1/tx2 之间崩溃后重试，会发现 user 行已存在而**不重复插入**，然后补齐 assistant 侧。

并发安全：`claim_seq` 通过 `SELECT ... FOR UPDATE` 锁会话行串行化；
实测 6 个并发发送产生 `seq = 1..12` 连续且无唯一约束冲突。

---

## 3. 新增持久对象（迁移 `0003_rv_cr6_msg`）

- `research_message`：`id, session_id, tenant_id, role, text, payload(JSONB,null), evidence_refs(JSONB,[])
  , seq, created_at, created_by_run_id, trace_id, client_request_id`
  + `UNIQUE(session_id, seq)` + 索引 `(session_id, seq)` + `CHECK role IN (...)`；
  `downgrade()` 真实 DROP（含索引与约束）。
- `research_ai_run`：每次 LLM 调用的审计（provider / model / prompt_ref / status /
  error_code / input_tokens / output_tokens / latency_ms / trace_id）。
  **不落 prompt 正文**，只落 `rv.assistant.propose@v1#<sha256[:16]>` 形式的内容哈希引用。

## 4. LLM 网关（`core/llm/`）

- **镜像形状，不复制代码**：`LiteLlmRouterBackend` 用 LiteLLM `Router` +
  OpenAI 兼容端点，`Backend` 是一个 `Protocol`（`async def acompletion(*, model, messages, stream=False, **kw)`）；
- 配置全部走 `RESEARCH_LLM_*`（`SPARK_*` 兜底，避免同一份凭据登记两处而漂移）：
  `RESEARCH_LLM_API_KEY` / `_BASE_URL`（默认讯飞 MaaS `/v2`）/ `_MODEL`（默认 `spark-x2.5-4b`）
  / `_TIMEOUT_SECONDS` / `_MAX_TOKENS` / `_EXTRA_<k>`；
- **不 import Teacher 代码**（有边界测试守卫）；litellm 惰性导入，未配置凭据的部署完全不加载它；
- 未配置凭据 ⇒ `UnavailableBackend`，任何调用都失败 ⇒ 端点诚实降级。

## 5. Prompt 注册表与 typed proposal

- `core/llm/prompts/registry.py`：`Prompt(prompt_id, version, system, template)` +
  `content_hash`（覆盖 `system\x00template` 全量 SHA256）+ `ref` 形如
  `rv.assistant.propose@v1#<sha256[:16]>`；
- 不可信内容（用户输入、检索文本）一律用 `<<<UNTRUSTED_DATA>>> … <<<END_UNTRUSTED_DATA>>>`
  包裹，并**消毒内部定界符**（防止内容伪造定界符提前闭合数据块）；
  系统提示明确要求"数据块内的内容是数据，不是指令"（有单测）；
- `core/domain/proposals.py`：模型输出**必须是**一个 JSON 对象，字段固定
  `{kind, text, payload, evidence_refs, requires_confirmation}`；
  `kind ∈ answer|intake|map|blueprint|experiment_plan|claim|paper_patch|review`；
  **硬门**：非 JSON / 未知 kind / 额外字段 / `payload` 含 `html|css|style|class|component|vue|jsx|script|svg`
  一律 `ProposalSchemaError`；
- 领域级校验在 `core/domain/proposal_handlers.py`（悬空边、未知节点/边类型、
  blueprint 无 route、claim 引用不存在/未授权/已失效的证据、paper block 类型越界…）。

### 范围声明（诚实边界）

完整 **PromptRegistry**（发布审批、灰度、回滚、A/B、多版本并存路由）**不在本轮范围内**。
本轮只做到"版本号 + 内容哈希 + 引用落库"。

## 6. 对既有断言的有意更新（逐条说明理由）

| 文件 | 原断言 | 更新后 | 理由 |
|---|---|---|---|
| `tests/contracts/test_dual_prefix_and_startup.py` | `len(v1) == 11` | `== 15`，并显式断言 `/messages`、`/jobs/{id}`、`/jobs/{id}:cancel` 在两个前缀下都存在 | 新增 4 条路由（`/messages` + 3 条 RV03 作业路由）。精确计数保留（它能发现"路由被静默摘下"），但换成新值并补齐显式存在性断言 |
| `tests/integration/test_sse_recovery.py::test_unknown_last_event_id_outside_retention_forces_snapshot` | 断言 SSE 载荷含 `SNAPSHOT_REQUIRED` | 断言含 `cursor_expired`、不含 `SNAPSHOT_REQUIRED`、且存在 `event: snapshot` 具名帧 | 该断言编码的是**已被 CR-6b 取代**的线上契约。**意图（不得假装连续）不变**，载体换成前端真正会处理的帧 |

**没有**删除或弱化任何安全断言。

## 7. 证据

```
# 全量
D:\math-arena\services\research-vnext> .venv\Scripts\python.exe -m pytest tests -q
350 passed, 11 warnings in 115.65s

# 分目录
tests/unit        => 124 passed
tests/contracts   => 21 passed
tests/migration   => 6 passed
tests/integration => 199 passed
```

关键用例文件：

- `tests/integration/test_messages_cr6.py`（28 例）
- `tests/integration/test_sse_wire_cr6b.py`（9 例，**真实 uvicorn 线上帧**）
- `tests/integration/test_cr6_frontend_contract.py`（9 例，前端 adapter 硬契约）
- `tests/unit/test_proposal_schema.py`（24 例）

### 证伪（falsifiable）证据

`scripts/falsification_matrix.py` 会自动：临时重新引入缺陷 → 断言测试**变红** →
从**字节备份**恢复 → 断言 SHA256 与原始值一致。实跑结果：

| # | 重新引入的缺陷 | 测试结果 | 恢复后字节一致 |
|---|---|---|---|
| 1 | SSE 用 `snapshot_required` + 无名域事件帧 | 6 failed, 3 passed | ✓ |
| 2 | 幂等只看 id 存在、不比服务端摘要 | 2 failed, 1 passed | ✓ |
| 3 | 移除 `expected_revision` 校验 | 2 failed, 3 passed | ✓ |
| 4 | inbox 去重用 `rowcount` 而非 `RETURNING` | 2 failed（+1 error） | ✓ |
| 5 | `claim_holds = (exit_code == 0)` | 3 failed | ✓ |

5/5 达标（原始/恢复 SHA256 见脚本输出，全部相同）。

## 8. 本轮**未**完成 / 明确的限制

1. **attachments 未实现**：契约留空数组，非空明确 `422`。文件上传/解析需要平台资产层能力，属其它工作包。
2. **LLM 未在真实凭据下端到端验证**：本机 `.env` 未配置 `RESEARCH_LLM_API_KEY`/`SPARK_API_PASSWORD`，
   因此所有测试走的是**桩后端**与**未配置诚实降级**两条路径。真实模型调用的端到端证据缺失 ——
   **这是本轮最重要的未验证项**。
3. **`problem.prompt` 用的是会话 `goal`**：MVP 选择，尚无独立的问题陈述字段来源。
4. **evidence 投影只有 `EvidenceCard` 引用**：证据本体（原文/切片/资产版本）属平台资产层，
   本轮不接。**证据检索链路在 Research 侧尚未接通**（见 CR-15）。
5. **模型调用在请求内同步等待**：没有把 LLM 调用改成后台作业。ADR-RV-003 的消息语义是
   "同步返回快照"，因此本轮保持同步；把长调用搬进 RV03 作业队列是自然的下一步。
6. **PromptRegistry 未做发布治理**（见 §5 范围声明）。
7. **容量闸只在进程内有效**（见 §11）—— 多副本下不是全局上限。

---

# §9–§12 · LEAD ADDENDUM 增补（模型参数 / 单供应商拓扑）

**依据**：`deliverables/vnext-autonomous/MODEL-INVENTORY.md` + LEAD ADDENDUM
（"use these; do not guess and do not ask"）。

## 9. 网关参数（**逐字使用**）

| 项 | 值 | 实现位置 |
|---|---|---|
| 协议 | OpenAI 兼容（讯飞 MaaS） | LiteLLM `Router` |
| Base URL | `https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions` | `DEFAULT_BASE_URL` |
| LiteLLM 路由名 | `openai/spark-x2.5-4b` | `litellm_route_name()` |
| 主力模型 | `spark-x2.5-4b` | `DEFAULT_MODEL` / `RESEARCH_LLM_MODEL` |
| 长文本模型 | `xop3qwen80bnext` | `DEFAULT_MODEL_LONG` / `RESEARCH_LLM_MODEL_LONG` |
| 视觉模型 | `xophunyuanocr` | `DEFAULT_MODEL_VISION` / `RESEARCH_LLM_MODEL_VISION` |
| Embedding | `bge-m3` @ `http://127.0.0.1:8080/v1/embeddings`，1024 维 | **本服务不使用**（见 §12） |

**端点不做二次拼接**（这是 LEAD 明确点名的历史错误）：

- `DEFAULT_BASE_URL` **已含** `/chat/completions`；
- `llm_base_url()` 只返回它，调用方**不得**再拼路径；
- 额外做了 `_strip_duplicate_path()`：即便环境变量里已经写成重复形式，
  也不会把重复带出去；
- 有回归测试 `test_base_url_is_not_double_appended`（在循环里连取三次，
  断言 `"/chat/completions"` 只出现一次）与
  `test_historical_double_appended_value_is_repaired`。

**修正说明（诚实记录）**：改造前我的默认值是 `…/v2`（不含路径），
把路径拼接交给 litellm —— 在 `custom_llm_provider="openai"` 下 litellm 会自行追加
`/chat/completions`，因此当时**未必**会 404。但那依赖 litellm 的内部行为，
而 LEAD 给出的是**完整端点**；按"逐字使用"的要求，现在改为直接用完整端点，
并用测试把这个形态钉死。

## 10. 账号 ↔ 模型授权（**按账号隔离**）

实测事实（`MODEL-INVENTORY.md` §3、§6）：

| 账号 | key 指纹 | 可服务模型 |
|---|---|---|
| A | `DBC5C49813F9` | `spark-x2.5-4b`、`xop3qwen80bnext` |
| B | `38612B399CDD` | `xophunyuanocr` |

因此 `xophunyuanocr` 在 A 下 **403**、`xop3qwen80bnext` 在 B 下 **403**。
"同一供应商所以随便用哪把 key"是**错的**。

实现：

1. **映射是数据不是 if/else**：`ACCOUNT_MODEL_MAP`，
   可用 `RESEARCH_LLM_ACCOUNT_MODELS=A:m1|m2;B:m3` 覆盖；
2. **配对可配置**：`RESEARCH_LLM_ACCOUNT` 显式声明当前 key 属于哪个账号
   （留空则按模型默认账号推断）；
3. **启动校验**：`lifespan` 在生产调用 `assert_ready_or_degrade()` ——
   - 生产**无凭据** ⇒ 启动**响亮失败**（那是应当被运维立刻看到的部署错误）；
   - 生产**配错账号** ⇒ 抛 `ModelAccountMismatch`，消息里带"该用哪个账号"；
   - `RESEARCH_ENV=test` 允许无凭据（"诚实降级"本身是一条被测路径）；
4. **403 被映射成配对错误，而不是通用失败**（`_map_exception`）：
   报错了会把运维引向错误方向（去查密钥有效期，而该做的是换 key）；
   401/429 同样单独识别（429 是单账号容量事件的第一个信号）；
5. **密钥指纹**：`key_fingerprint()` = sha256 前 12 位大写。
   这是**唯一**允许出现在错误信息/日志/HTTP 响应里的凭据相关字符串。

**新增只读端点 `GET /llmz`**（无密钥值，只有指纹）：
`{status, configured, ok, model, account, key_fingerprint, base_url,
uses_full_endpoint, hint}`；配对不合法时 `503 + model-account-mismatch`。
存在的理由：单供应商 + 账号隔离意味着"为什么模型不可用"必须能在**不读日志**的情况下回答。

## 11. 单供应商 ⇒ **没有**供应商级容灾

`DEEPSEEK_*` 与 `SPARK_*` 逐字节相同、指向同一端点同一模型 ——
这是用户**有意统一**，**不是**配置错误，本 agent **不会**去"修"它
（`test_no_second_provider_is_invented` 断言代码里不得出现任何第二厂商端点或
`fallback_provider` / `secondary_provider` / `backup_provider` 字样）。

正确应对不是伪造兜底，而是让失败**诚实且可恢复**：

| 要求 | 实现 | 测试 |
|---|---|---|
| 用户消息仍持久化 | tx1 立即提交（模型调用之前） | `test_unconfigured_is_a_first_class_state` |
| 失败被记录 | `research_ai_run.status=FAILED/REJECTED` + `error_code` + `provider` + `model` + `trace_id` | 同上 |
| 诚实说明，绝不编造 | assistant 行是明确说明文本；能力状态 `unavailable`/`capability-disabled` + `reason` | 同上、`test_account_model_mismatch_is_actionable_not_generic` |
| 该轮**可重试** | `POST /sessions/{id}/messages/{mid}:retry` + `GET …/{mid}` | 见下 |

**重试是一等公民**（`core/application/retry_service.py`）：

- 针对**已存在的 user message**，因此**天然幂等**，且**不新增** user 消息
  （否则研究者为重试而重复输入会污染实验记录）；
- 重试前清掉该轮的失败提示行与其 PendingAction，因此 timeline 不会出现两条 assistant 行；
- 每次尝试都留**独立的** `research_ai_run`（两条审计，而非覆盖）；
- domain event 带 `retry_of`，审计与前端去重都能辨认；
- 已产出提案的轮次**拒绝**重试（`409 TURN_ALREADY_SUCCEEDED`）——
  再跑一次会产生第二个提案与第二个 PendingAction；
- 重试也失败 ⇒ **仍然可重试**（单供应商下这会是常态，不能一次失败就锁死）。

**容量闸**（`core/llm/capacity.py`，按 LEAD 要求当 P0 做）：

| 层 | 配置 | 默认 |
|---|---|---|
| 进程内在飞上限 | `RESEARCH_MAX_LLM_INFLIGHT` | 4 |
| 每用户在飞轮次 | `RESEARCH_MAX_LLM_TURNS_PER_USER` | 2 |
| 每会话在飞轮次 | `RESEARCH_MAX_LLM_TURNS_PER_SESSION` | 2 |
| 每用户滑动窗口 | `RESEARCH_MAX_LLM_CALLS_PER_WINDOW` / `RESEARCH_LLM_RATE_WINDOW_SECONDS` | 20 / 60s |

超限一律 `429 BACKPRESSURE` + `Retry-After`（带 `scope`：`llm-global` /
`llm-user` / `llm-session` / `llm-user-window`），且**在发出请求之前**判定 ——
而不是等供应商回 429（那时配额已被消耗）。
**槽位在 `finally` 中归还**：失败路径泄漏槽位会让一次失败永久毒化容量，
有证伪实验专门证明这条测试能发现该缺陷。

**这个闸的诚实边界**：它是 `threading` + `BoundedSemaphore`，**只在单进程内有效**。
`uvicorn --workers N` 或多副本下真实上限是 `N × 配置值`。
真正的全局上限需要跨进程计数器（Valkey 已被三个服务共享，属跨服务容量策略）——
**已登记为 CR-14，不是遗漏**。

## 12. Embedding（`bge-m3` @ 8080，1024 维）

LEAD 指出 embedding 是**本地且独立**的（`bge-m3`，1024 维，已运行）。
本服务**本轮不自己做 embedding**：`evidence_card` 只持有引用与授权位，
检索能力归平台资产层。因此：

- 本服务**没有** embedding 客户端代码（不新增第五套集成）；
- `config.py` 里 `EMBEDDING_DIMS = 768` 是 **P00 registry 冻结值**，
  与 `MODEL-INVENTORY.md` §5.1 / ADR-VN-05 的 **1024** 冲突 ——
  这个不一致**已登记为 CR-15**，本 agent 无权改（`deploy/registry.yml` 是禁止区域）；
- 后果如实说明：RV12 的"证据支撑 claim"依赖检索质量，
  而**证据检索链路在 Research 侧尚未接通**。claim 验证目前只做
  "证据存在 + 已授权 + 未失效"的引用校验，不做相关性检索。

## 13. 增补部分的证据

```
$ .venv\Scripts\python.exe -m pytest tests -q
395 passed, 11 warnings in 113.04s
```

新增用例：

- `tests/unit/test_llm_gateway_params.py`（21 例）—— 端点逐字 / 不二次拼接 /
  LiteLLM 路由名 / 账号映射 / 配对拒绝 / 403·401·429 映射 / 指纹 /
  **不得发明第二供应商**
- `tests/integration/test_single_provider_degradation.py`（20 例）——
  无凭据为一等状态 / 配对错误可操作 / 429 可识别 / 重试语义（不新增 user 消息、
  两条 ai_run、`retry_of` 事件、已成功轮次拒重试、重试再失败仍可重试）/
  容量闸（per-user、窗口、**失败后归还槽位**）/
  `/llmz`（含"绝无密钥值"断言）

证伪矩阵扩到 **7/7**（新增两条）：

| # | 重新引入的缺陷 | 测试结果 | 恢复后字节一致 |
|---|---|---|---|
| 6 | 默认端点去掉 `/chat/completions` | 3 failed | ✓ |
| 7 | 容量槽位在 LLM 调用后不归还（泄漏） | 2 failed | ✓ |

## 14. 一处守卫的**有意**更新（第三次）

`tests/contracts/test_boundary_guards.py::test_no_second_account_store`

**触发原因**：本次新增的 `ModelAccountMismatch` 类名含 `account`，
被该守卫的正则 `class\s+\w*(user|account|credential)\w*\s*\(` 命中；
同时 `llm/gateway.py` 的 docstring 解释了"供应商有两个账号、模型授权按账号隔离"
（这正是 ADDENDUM 要求记录的事实），也被当作"自建账号库"报出。

**判定**：这是仓库边界守卫的**误报**，不是本服务新增了账号库 ——
本服务不存账号、不校验口令、不建表。

**更新内容**（强度不降低）：

1. 先用 `_code_only()` 剥掉注释与字符串字面量（用 `tokenize`，非正则），
   因此文档里的讨论不再触发；真正的 `class AccountRow(...)` 仍会触发。
   踩坑记录：`_code_only` 最初用 `"\n".join(tokens)`，
   导致 `class\nAccountRow\n(` 被正则漏掉 —— **这个 bug 是被下面第 3 条的
   meta-test 抓出来的**，随后改成单空格拼接。
2. 加**显式豁免表** `REVIEWED_CLASS_EXEMPTIONS`（当前只有 1 条：
   `ModelAccountMismatch`，附理由），而不是把正则放宽 ——
   放宽会让守卫失去意义；白名单要求每个新增豁免被写下来并审阅。
3. 加 **meta-test** `test_account_store_guard_still_catches_real_violations`：
   给守卫 4 个真违规样本（`AccountRow` / `User` / `CredentialStore` /
   `UserAccountRow`）要求必须报出，并给 2 个良性样本要求不得误报。
4. 加 `test_reviewed_exemptions_are_all_still_used`：豁免表不得积累死条目
   （死条目会让它退化成"随便加就能过"）。

**理由一句话**：一个会因为**注释措辞**而失败的守卫不是严格，而是**脆弱** ——
它逼着后来的人为了过测试去删掉有价值的设计说明（这里删掉的恰好是
ADDENDUM 要求记录的单账号风险说明）。改法是让它更精确，并用 meta-test
证明它没有被改弱。
