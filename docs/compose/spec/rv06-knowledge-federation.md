---
feature: rv06-knowledge-federation
status: delivered
updated: 2026-09-13
branch: master (in-place research-vnext only)
commits: pending (not committed per task)
---

# RV06 Knowledge Federation — 统一只读检索契约与维度隔离

## Report

**What was built** — 在 `services/research-vnext` 新增 `core/federation/`：
五类规范（Literature / ProjectAsset / FoundationalMath / Dataset / Web-External）
一处登记，前三类接真实数据上线，后两类保留 contract 且默认 disabled。
统一 `FederatedQuery` / `ProviderHit` / `FederatedResult` 契约；维度隔离做成三层
（类型层 locator 配对、字段层白名单、查询层 scope 冻结 + 命中复核）；
注入防线放在检索出口；provider 失败如实降为 `PARTIAL`；检索审计落
`research_retrieval_trace`（只存 hash/ID）。FoundationalMath 经只读 DSN 读
Teacher direct-chunk（22 源 / 460 节 / 4202 chunk），locator 只有 source→section→chunk，
结果恒标 `TEXTBOOK`/`FOUNDATIONAL_MATH` 且 `citation_eligible=false`。

**Verification** — `pytest tests/unit tests/contracts -q` → **308 passed**；
`pytest tests/integration -q` → **276 passed, 0 error**（真实 PostgreSQL + Alembic 0008
+ 真实 Teacher 54330 + 真实 HTTP）；`alembic upgrade/downgrade/upgrade` → exit 0/0/0。
固定评测集（8 教材 + 2 文献 + 2 资产）：教材 `Recall@10 0.9167` / `nDCG@10 0.9337`，
文献与资产 `1.0`，**locator 可回源率 1.0（84/84）**，真实语料注入标记数 0。
独立审查报出 1 High + 2 Med + 5 Low，其中 High（scope 维度取自请求体）与两处 Med
（受控字段无约束、trace_id 未落库）已修并复验，Low 逐项处置或登记（见 FINAL-GATE §5.1）。

**Journey log** — ① SQL 里 `::text` 被 SQLAlchemy 当成绑定参数名，只读 adapter 起初整条查询报错；
② `SET SESSION CHARACTERISTICS` 在 connect 事件里只作用于当次事务，写操作实测能通过，
改用 `connect_args` 强制只读；③ 检索初版在 SQL 侧按 `id` 截断 300 条，评测立刻暴露
Recall@10=0.25，改为"SQL 只过滤可见性、相关性全在 Python 侧"并加章节标题主导权重；
④ 候选数量悬殊（教材 300 vs 资产 1）导致 top-k 被单维度占满，加维度交织；
⑤ nDCG 因折叠后重复计分算出 4.54，指标实现本身有 bug，去重后修正。

## [S1] Problem

`SHARED-PLATFORM-FINAL-GATE.md` §1 裁定 Knowledge Federation = `NO-GO`：**五个 Provider 一个都不存在**，
全仓零命中。目前 Research 侧的检索能力是三块互不相通的孤岛：

- `core/literature/`（RV04）能抓 Crossref metadata，但 `LiteratureMetadata` 是**入库 DTO**，
  不是检索结果，无 score、无 locator 语义、无 warnings、无 rights 门；
- `core/assets/`（RV04）有真实字节与 ACL，但没有任何"按当前 project 检索资产"的读取路径；
- Teacher direct-chunk（22 源 / 460 节 / **4202 chunk**，全 STAGED 且已 768 维索引）
  Research **完全读不到**（768/1024 + 两库分裂，ADR-VN-02 已决策"平台只读 adapter"但**未实施**）。

同时存在三类**已知真实的混淆风险**，当前无任何机器化的防线：

1. **维度混淆**：`EvidenceRef` 的 `source_type` 只有 6 值且与教材/文献语义重叠，
   一旦把 Teacher 教材 chunk 投影成 `LITERATURE`，下游就会为它生成 DOI/期刊/页码，
   直接违反"MUST NOT 把教材伪装成学术文献"；
2. **跨租户/跨 project 泄漏**：三条读取路径的隔离维度不同
   （literature = `tenant_id`，asset = `tenant_id`+`project_id`+ACL，
   foundational-math = `school_id` NULL/非 NULL），没有统一 scope 权威；
3. **prompt injection**：检索文本、PDF 片段、教材 chunk 都会进上下文，
   而 P00 ADR-PLAT-05 的 trust label 只在 **Teacher 侧** `context_manifest` 实现，
   Research 侧检索结果**没有**任何 sanitize/标记。

## [S2] Design

### S2.1 五类规范（evidence_type）

统一枚举 `EvidenceType`，是**唯一**区分数据语义的轴：

| evidence_type | 语义 | 本轮 Golden Path |
|---|---|---|
| `LITERATURE` | 学术文献（真实 DOI/期刊/页码允许） | ✅ 启用 |
| `PROJECT_ASSET` | 本项目上传资产（PDF 等） | ✅ 启用 |
| `FOUNDATIONAL_MATH` | 教材 direct-chunk（**非文献**） | ✅ 启用 |
| `DATASET` | 结构化数据集 | ⛔ contract 保留，默认 `disabled` |
| `WEB_EXTERNAL` | 外部网页 | ⛔ contract 保留，默认 `disabled` |

`disabled` 的 provider 仍必须存在于 catalog 且可被 `describe()`，
但进入 query allowlist 时返回 `PROVIDER_DISABLED` 而非静默忽略（证明 contract 存在）。

### S2.2 Query 契约（provider 不能扩展 scope）

`FederatedQuery` 必填：`actor_id` / `tenant_id` / `project_id` / `session_id` / `purpose`
/ `provider_allowlist` / `filters` / `budget` / `deadline_ms` / `trace_id`。

**scope 权威在 orchestrator**：查询期由 `resolve_scope()` 从 `AccessContext` 派生
`ResolvedScope`（tenant/project/school/reader capabilities），一次性冻结后传给 provider。
provider **只能收窄、不能放宽**：每个 provider 返回的结果都要经
`assert_result_within_scope()` 复核；越界结果被剔除并记 `SCOPE_VIOLATION` warning。

### S2.3 Result 契约

`FederatedResult` 必填字段（对应任务书第 3 条）：

- `evidence_type`、`provider`、`provider_version`
- `locator`（typed，按 evidence_type 分派，见 S2.4）
- `snippet` + `snippet_hash`（`snippet` 永为 untrusted，见 S2.7）
- `rights`（`rights_status` + `rights_evidence_ref` + `redistributable`）
- `provenance`（`retrieved_at` / `parser_version` / `source_hash`）
- `retrieved_at`
- `score_components`（分项，不只有总分：`lexical` / `vector` / `rights_penalty` / `recency`）
- `warnings[]`

### S2.4 Locator 分派与维度隔离

| evidence_type | locator 类型 | 允许字段 | 禁止 |
|---|---|---|---|
| `LITERATURE` | `LiteratureLocator` | `doi` / `venue` / `page` / `section` / `asset_anchor_id`（RV05 anchor） | 无 DOI 时**必须** `doi=""` 而非编造 |
| `PROJECT_ASSET` | `AssetLocator` | `asset_id` / `asset_version` / `content_hash` / `anchor_id` | 暴露 `storage_key`/MinIO endpoint |
| `FOUNDATIONAL_MATH` | `TextbookLocator` | **仅** `source_id` / `section_id` / `chunk_id` | `page` / `page_range` / `bbox` / `page_refs` / `pNN` / DOI / venue |
| `DATASET` / `WEB_EXTERNAL` | 对应 typed locator | — | — |

`FOUNDATIONAL_MATH` 的 locator **类型层**不含页码字段（Teacher `textbook_chunks.page_range`
真实存在，adapter 必须显式丢弃并在 `warnings` 记 `DROPPED_PAGE_RANGE`）。

### S2.5 FoundationalMath adapter（只读）

- 新配置 `RESEARCH_FOUNDATIONAL_DATABASE_URL`（**只读** DSN，指向 teacher_platform 54330）。
- 只读 `textbook_sources` / `textbook_sections` / `textbook_chunks` 三表。
- rights：只返回 `rights_status ∈ {STAGED, ACTIVE}`；`QUARANTINED` 永不进结果。
- tenant：`school_id IS NULL`（平台种子）对全部可见；非 NULL 只对同 school 可见。
- 每个结果 `labels` 必须含 `TEXTBOOK` + `FOUNDATIONAL_MATH`，且
  `citation_eligible = false`（**不用于声称"文献已支持"**）。
- 无写入路径：连接以只读事务打开，任何写尝试在测试中被显式断言拒绝。

### S2.6 去重 / rerank 保留来源维度

去重键 = `(evidence_type, provider, locator_discriminator)`，**不跨 evidence_type 合并**。
若两条记录 locator 相同但 `rights` 或 `provenance` 不同 → **不合并**，
保留两条并在两条上互记 `DUPLICATE_LOCATOR_DISTINCT_SOURCE` warning。
rerank 用 `score_components` 加权和，权重是**常量**（不随 query 特例变化）。

### S2.7 Untrusted 与注入防线

- 所有 `snippet` 一律 `trust_level = UNTRUSTED_DATA`，`untrusted = True`。
- `sanitize.py` 检测注入模式（`instruction_override` / `fake_system_tag` /
  `role_play_escape` / `tool_call_forgery` / `privilege_escalation` 共 5 类；
  分隔符逃逸由 `role_play_escape` 的 `[INST]` / `<<SYS>>` 模式承载），命中即：
  1. 在 snippet 中把命中片段替换为 `[[INJECTION-REDACTED:<pattern_id>]]`；
  2. 记 `warnings += INJECTION_PATTERN_DETECTED`；
  3. `sanitized = true`。
- 契约**不允许**任何 API 把检索文本提升为 `system`/`tool_call`：
  `FederatedResult` 无 `role`/`tool` 字段，且提供 `to_untrusted_block()`，
  其返回结构的 `trust_level` 字段为**只读常量**。

### S2.8 错误与 PARTIAL

- provider 抛 `PROVIDER_TIMEOUT` / `PROVIDER_RATE_LIMITED(429)` / `PROVIDER_ERROR` →
  该 provider 结果为空 + `provider_status = DEGRADED|UNAVAILABLE` + 错误码进 warnings；
- **一个 provider 失败绝不伪造全部成功**：`FederatedResponse.status ∈ {OK, PARTIAL, FAILED}`，
  只要有 provider 失败即 `PARTIAL`（全部失败才 `FAILED`），且 `degraded_providers[]` 非空；
- 无 locator 的结果被丢弃并记 `MISSING_LOCATOR` warning，不产出"看起来可回源"的假结果。

### S2.9 Trace / cost

新表 `research_retrieval_trace`（migration `0008`）：`trace_id` / `tenant_id` / `project_id`
/ `query_hash` / `strategy` / `prompt_version` / `provider_ids[]` / `result_ids[]` /
`token_in` / `token_out` / `cost` / `latency_ms` / `status` / `warnings` / `redaction_version`。
**只存 hash 与 ID，永不存 query 原文与 snippet 原文**；`purpose` / `prompt_version`
在契约层受长度（≤64）与字符白名单（`[A-Za-z0-9._:-]`）约束 —— 它们是受控标识，
不是自由文本，因此不存在"把用户原话写进审计列"的路径。重复同一查询（同 `query_hash`）
幂等：同 trace 不新增行，且响应返回的 `trace_id` 恒为**落库那一行**的 id
（否则调用方按返回的 id 查审计会 404）。

### S2.10 评测集

固定落盘 `tests/fixtures/federation/eval/queries.jsonl` + `qrels.jsonl`：
查询集来自真实数据（Teacher 460 节 / 4202 chunk、真实 Crossref metadata、本轮真实 asset）。
记录 `Recall@k` / `nDCG@k` / `locator_resolvable_rate`。
**禁止硬编码查询特例**：评分路径不出现 query 字面量分支（由静态守卫测试断言）。

## [S3] Out of Scope

- **不实施 Agent 自由跨库搜索**：无 LLM tool 选择 provider，无自动 fan-out 决策。
- **不改 Teacher 教材库**：`teacher-platform/**` 零改动；只读 DSN 单向只读。
- **不把教材伪装成学术文献**：`FOUNDATIONAL_MATH` 永不携带 DOI/期刊/页码。
- 不删除/不修改 legacy research；不动 shared contracts / App Shell / 前端。
- 不 push / 不 deploy / 不 commit。
- Dataset / Web/External 只保留 contract 与 disabled 状态，不实现真实抓取。

## Tasks

- [x] T1: 先写失败测试（维度混淆 / 跨 tenant+project+class / 教材伪 DOI / 无 rights 全文 /
  prompt injection / 重复结果 / 超时+429+PARTIAL / 无 locator），acceptance: 全部先红（covers: S2.1–S2.9）
- [x] T2: federation 契约层（EvidenceType / FederatedQuery / FederatedResult / locator 分派 / scope 冻结），
  acceptance: 契约校验测试绿（covers: S2.1–S2.4）
- [x] T3: LiteratureProvider adapter，acceptance: 真实 Crossref metadata 出 Gold 结果且带 rights/locator
  （covers: S2.2–S2.4; depends: T2）
- [x] T4: ProjectAssetProvider adapter，acceptance: 真实 asset 按 project ACL 命中、跨 project 零命中
  （covers: S2.4; depends: T2）
- [x] T5: FoundationalMathProvider 只读 adapter，acceptance: 真实 4202 chunk 可检索且 locator 只含
  source/section/chunk、`citation_eligible=false`（covers: S2.5; depends: T2）
- [x] T6: Dataset/Web contract 保留 + 默认 disabled，acceptance: `PROVIDER_DISABLED` 可断言（covers: S2.1）
- [x] T7: 去重/rerank 保留来源维度 + sanitize 注入防线 + PARTIAL/warnings 传播，
  acceptance: 注入样本被标记且不提升为指令（covers: S2.6–S2.8; depends: T2)
- [x] T8: migration 0008 + trace/cost 落库与脱敏、幂等，acceptance: 同 query 二次不新增行 + 无原文
  （covers: S2.9; depends: T2）
- [x] T9: 真实混合评测集与指标（Recall@k / nDCG@k / 可回源率）+ 无硬编码守卫，
  acceptance: 指标由命令可复现（covers: S2.10; depends: T3,T4,T5）
- [x] T10: 全量验证（unit/contracts/integration）+ 9 份交付物 + FINAL-GATE，
  acceptance: 命令与证据写入控制目录（depends: T7,T8,T9）
- [x] T11: 独立审查发现处置（scope 维度取自请求体 / 受控字段约束 / trace_id 落库对齐 /
  维度冒充校验 / locator 子类严格类型 / 无 DOI 去重键 / 文档与代码对齐），
  acceptance: 全部复验通过（covers: S2.2, S2.3, S2.9）
