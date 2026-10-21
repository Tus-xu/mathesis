# PHASE 3 · RV09–RV13 — 领域对象

- **实施者**：AGENT-H (`RES-B`)
- **日期**：2026-09-13
- **状态**：**PARTIAL** —— 全部对象族的模型/迁移/领域规则/**确认门落库**已落地并有测试；
  **前端投影只做到部分对象族**，UI 面向的读写端点未全部开放（见 §5）。

---

## 1. 统一模式：不可变修订链

除 `ExperimentRun` / `VerificationRecord` / `ReviewFinding`（本身是"事件/条目"而不是"版本"）
之外，所有对象都继承 `RevisionedObjectRow`：

```
id, session_id, tenant_id, revision, content(JSONB), content_hash,
client_request_id, confirmed_at, confirmed_by, trace_id, created_at
UNIQUE(session_id, revision)
```

**每次确认追加一行**，旧行永不修改。因此"谁在何时基于哪个 revision 改了什么"
不需要额外审计表就能回答（`test_two_confirmed_blueprints_produce_two_immutable_revisions`）。

## 2. 写入路径（唯一）

```
LLM 输出 ──parse_proposal()──► Proposal（schema 硬门）
   │
   ├─ proposal_handlers.prepare()  ← **领域语义校验**
   │     失败 ⇒ 不创建 PendingAction，诚实降级（error_code 落 research_ai_run）
   │
   └─► PendingAction（object_ref + content + content_hash）
          │
          └─ 用户 confirm ─► accept_pending()
                 │
                 ├─ 复检 capability / revision
                 ├─ proposal_handlers.apply()  ← **唯一**写正式状态的函数
                 │     失败（如证据在确认前失效）⇒ REJECTED_STALE + 409
                 └─ domain event `session.proposal_confirmed` + outbox（同事务）
```

有**总不变量测试**（`test_message_never_creates_object_without_confirmation`）：
对 6 个 kind 逐一验证"未确认前正式对象表必须为空"，同时验证提案**确实**进了确认门
（证明"没有对象"不是因为提案被丢弃）。

## 3. 各对象族

### RV09 · `ResearchIntake` + `ProblemMap`

`{problemStatement, subproblems, constraints, variables, knowns, unknowns, deliverables, successCriteria}`，
每个元素形如 `{"text": ..., "origin": "USER|SOURCE|INFERENCE"}`。

**`origin` 安全行为（保守方向）**：
- 缺失 `origin` ⇒ **降级为 `INFERENCE`**（不是 `USER`）；
- 非法值（如 `"OFFICIAL_FACT"`）⇒ 同样降级为 `INFERENCE`；
- content 里额外落 `origins` 计数，前端可据此标注"哪些是模型推断"。

即"把推断冒充成用户原话"这条错误路径在**代码层**上不成立。

### RV09 · `ResearchMap`

typed nodes（`question|hypothesis|concept|method|source|model|experiment|claim|risk`）
+ typed edges（`decomposes|supports|challenges|requires|derived_from|tests|contradicts`）。
未知类型 ⇒ 拒绝；**悬空边**（指向不存在的节点）⇒ 拒绝
（否则前端会渲染出指向不存在节点的边）。

### RV10 · `ModelSpec` + `ResearchBlueprint`

`ModelSpec` 额外列：`label / constraints / evidence_coverage / gaps`。
`Blueprint`：`route` **必填**（`BLUEPRINT_NO_ROUTE`）；
标记为 `unverified` 的假设其 `origin` 必须是 `INFERENCE`（`BLUEPRINT_UNVERIFIED_NOT_INFERENCE`）。

### RV11 · `ExperimentPlan` + `ExperimentRun`

`ExperimentPlan` 必须有 `dataset_ref`。

`ExperimentRun` 存：`code_ref / dataset_ref / environment / params / result / metrics /
figures / logs_ref / exit_code / repro{imageDigest, dependencyLockHash, seed, codeHash,
inputHash, outputHash} / not_reproducible_reason`。

**`exit_code == 0` 绝不等于"结论成立"**：
`claim_holds` 是与 `exit_code` **正交**的字段，且 `evaluate_run()` / worker 的
`_experiment_handler` **永远**给它 `None`（未判定）。判定只能来自 `VerificationRecord`。

**可插拔执行器**（`core/execution/executor.py`）：
`ExperimentExecutor` Protocol + `HttpExecutor`（POST 到 WP-I 的沙箱服务）+
`NullExecutor`（未配置 ⇒ 抛 `ExecutorNotConfigured`）。
`EXECUTOR_NOT_CONFIGURED` 属于**永久**错误 ⇒ 立即失败、不重试。
真实沙箱执行是 **WP-I** 的交付物，本轮只定义契约。

### RV12 · `Claim` + `VerificationRecord`

- `status ∈ SOLVED|CONDITIONAL|INCONCLUSIVE|REFUTED`
- `support_quality ∈ UNSUPPORTED|PARTIAL|SUPPORTED|CONFLICTED` —— **与 status 正交**
- 证据引用必须指向**存在 + 已授权 + 未失效**的证据；
  `SOLVED` 且无有效证据 ⇒ 拒绝（`CLAIM_SOLVED_WITHOUT_EVIDENCE`）；
  未授权证据**不**进入可用集合（`test_unauthorized_evidence_does_not_enable_claim`）。
- `UNKNOWN` **永不**被强制转成 `PASS`：`normalize_verification_result()` 对
  任何无法识别的输入（`"passed"`、`"ok"`、`"1"`、`""`）都返回 `UNKNOWN`。
  有证据但只有 `UNKNOWN` ⇒ `PARTIAL`（**不是** `SUPPORTED`）。
- **重开**：`invalidate_evidence()` 会找出依赖该证据的 `SOLVED` claim，
  写**新修订行**并把状态降为 `INCONCLUSIVE`（不是 `REFUTED` —— 证据消失只说明
  "不再成立"，不说明"被证伪"）。不可复现的实验同样触发重开。

### RV13 · `PaperSpec` + `ReviewFinding`

- `PaperSpec.content = {"blocks": [...]}`，block 类型封闭集合：
  `section|paragraph|equation|figure|table|citation|claim_ref|evidence_ref|review_mark`。
  **LaTeX 只是 import/export 格式，不是规范模型**（有测试断言 content 里不含 `\documentclass`）。
  `apply_paper_patch()` 强制 `baseRevision == currentRevision`，
  否则 `PAPER_BASE_REVISION_STALE`（AI 的 patch 不得静默覆盖别人的修改）。
- `ReviewFinding`：类别封闭集合 `EVIDENCE_GAP|MATH_ERROR|DATA_LEAKAGE|REPRO_GAP|
  CITATION_MISMATCH|OVERCLAIM|WRITING_ISSUE`，带 `severity / location /
  related_claim_id / related_evidence_refs / suggestion / status / resolution_evidence_refs`。

## 4. 迁移

`0005_rv_domain_objects`：8 张修订链表（`research_intake`、`problem_map`、`research_map`、
`model_spec`、`research_blueprint`、`experiment_plan`、`paper_spec`、`claim`）
+ `evidence_card` + `experiment_run` + `verification_record` + `review_finding`，
并给 `pending_action` 增加 `object_ref` / `source_message_id`。
`downgrade()` 真实 DROP 全部对象。

## 5. 快照投影（`artifactFocus`）

已投影：

- `claims[]`（id / text / status / evidenceRefs / relation）
- `routes[]`（ModelSpec → id / label / constraints / evidenceCoverage / gaps）
- `blueprint`（problem / objective / route / evidenceNodes / assumptions / lineage /
  confirmationState）
- `objects.{intake, problemMap, researchMap, experimentPlan, experimentRuns,
  verifications, paperSpec, reviewFindings}` —— **加法式**，前端当前未渲染，
  增量消费即可。
- `evidence[]` 来自 `evidence_card`
- 能力 `research-artifact` 在**有已确认产物**时 `available`，否则 `unavailable`

**未投影**：`artifactFocus.key` 目前只在 `claims`/`blueprint`/`route` 三者间选择，
没有为 `run`/`paper`/`review`/`export` 做独立的 focus 选择逻辑（ADR-RV-003 §3.4 列了这些 key）。
`runStatus` / `run` 固定为 `null` —— 因为实验运行**尚未**接入会话级 artifact focus。

## 6. 证据

```
tests/unit/test_rv_domain_rules.py                => 32 passed
tests/integration/test_rv09_to_rv13_domain.py     => 34 passed
```

重点关注用例：

- `test_exit_code_zero_does_not_mean_claim_holds`（含证伪实验 #5）
- `test_unknown_never_becomes_pass`
- `test_invalidated_evidence_reopens_solved_claim`
- `test_unauthorized_evidence_does_not_enable_claim`
- `test_message_never_creates_object_without_confirmation`
- `test_inference_is_never_presented_as_user_fact`
- `test_experiment_executor_is_honest_when_unconfigured`
- `test_experiment_job_fails_permanently_without_executor`

## 7. 本轮**未**完成（明确清单）

1. **`ProblemMap` 没有独立的 proposal kind**：模型输出的 `intake` 落到 `research_intake`；
   `problem_map` 表与 `apply()` 路径已就绪，但**没有** `kind="problem_map"` 的校验器，
   因此前端 `ProblemMapFocus.vue` 目前拿不到数据。这是最明显的缺口。
2. **`ModelSpec` 没有 proposal kind**：`model_spec` 表与 `apply()` 分支已就绪，
   但 `VALIDATORS` 里没有 `model_spec` 入口 ⇒ `ModelRouteArena` 无数据来源。
3. **`ExperimentRun` / `VerificationRecord` / `EvidenceCard` 没有 HTTP 端点**：
   只有应用服务函数 + 直接调用（测试走这条路）。理由：这些对象的调用方
   应当是有明确权限的服务端组件（worker / 沙箱服务），而不是浏览器；
   开放写端点需要先定义管理员/服务鉴权模型，本轮不擅自加。
4. **`ReviewFinding` 的 status 流转（RESOLVED 等）未实现**：只到 `OPEN`，
   `resolution_evidence_refs` 字段已就绪但没有写入路径。
5. **`ClaimLedger`**：ADR/RV12 提到"ClaimLedger"这一层；本轮把它实现为
   `claim` 修订链 + `verification_record`，**没有**单独的 ledger 聚合对象。
6. **`artifactFocus` 对 run/paper/review/export 的独立 focus 逻辑**未实现（见 §5）。
7. **实验执行端到端未验证**：`HttpExecutor` 的 HTTP 契约已定义，但没有真实沙箱服务
   可对接（WP-I），因此只验证了"未配置时诚实失败"。
