---
feature: t24-artifact-edit-export
status: in-progress
updated: 2026-09-13
branch: master
---

# T24 · LessonPlanSpec / TeachingDeckSpec 结构化编辑 · 局部重生成 · PPT QA · 真实导出

> 本文件是 T24 的**冻结接口契约**（backend 与 frontend 共同实现对象）。任何实现不得偏离本文件的
> 端点路径、字段名、错误码与不变量；偏离必须先改本文件。

## Report

（交付时填写）

## [S1] Problem

T22/T22.3 建成了 conversation-first 的 living artifact 工作区；`sol-luna-frontend/T24` 在该工作区内
建成了 `artifacts/{lesson-plan,deck,shared}` 组件，但只发出 typed action 桩——8100 侧**没有**任何
spec 编辑、patch proposal、局部重生成、逐页 QA、真实导出与下载能力，因此 UI 只能显示
`capability-disabled`。教师无法真正改一个块/一页、无法局部重生成、无法拿到可打开的 PPTX/PDF/教案。

## [S2] Design

### S2.1 事实来源与所有权

- `TeachingDeckSpec`（唯一事实来源）= `app/ppt_v2/domain/spec.py`，落 `ppt_v2_deck_specs`（revision 乐观并发）。
- `LessonPlanSpec` = `app/teacher_prep/specs/lesson_plan_schema.py`，落 `tp_lesson_plan_specs`。
- 修订历史 / proposal / 逐页 QA 是**新的持久化事实**（本轮新增表）。
- artifact 信封（hash/ACL/lineage/retention）遵循 `app/platform_contracts/artifact.py`；字节落 MinIO
  （`app/storage/minio.py`），浏览器**不得**知道 bucket/key。
- PPT 生产引擎不变：8100 只做编辑与编排，渲染仍由 8200（VPL + pipeline）→ 8201（python-pptx + PowerPoint COM）。
- 冻结域（`app/export/**`、`app/tasks/jobs/export_pptx.py`、`app/pagination/**`、`app/domains/contents/**`、
  `app/domains/generation/**`）**不得修改**。

### S2.2 新增持久化（单条迁移，见 S2.7）

| 表 | 关键列 | 约束 |
|---|---|---|
| `ppt_v2_spec_revisions` | deck_id FK→ppt_v2_deck_specs.id, revision, kind(projection/patch/regen/revert/baseline), spec JSONB, spec_sha256, base_revision, summary, actor_id, created_at | UNIQUE(deck_id, revision) |
| `ppt_v2_edit_proposals` | id, deck_id FK, status(proposed/applied/rejected/superseded/failed), source(human/ai/regen), expected_revision, base_revision, summary, instruction, operations JSONB, refs JSONB, affected JSONB, actor_id, error, idempotency_key, created_at, decided_at, applied_revision | UNIQUE(idempotency_key) WHERE NOT NULL |
| `ppt_v2_page_qa` | deck_id FK, spec_revision, page_no, status(pass/fail/unknown), defects JSONB, metrics JSONB, detector_version, attempts, checked_at | UNIQUE(deck_id, spec_revision, page_no) |
| `tp_lesson_plan_revisions` | spec_id FK→tp_lesson_plan_specs.id, revision, kind, body JSONB, body_sha256, base_revision, summary, actor_id, created_at | UNIQUE(spec_id, revision) |
| `tp_lesson_plan_edit_proposals` | 同 ppt_v2_edit_proposals 形状，spec_id FK | UNIQUE(idempotency_key) |
| `teacher_artifacts` | id, owner_id, spec_kind(teaching_deck/lesson_plan), spec_id(text), spec_revision, kind(pptx/pdf/docx/qa_json), media_type, bucket, object_key, content_sha256, size_bytes, version, created_by_run_id, source_lineage JSONB, acl JSONB, retention JSONB, qa_status JSONB, created_at | UNIQUE(object_key) |
| `teacher_artifact_tickets` | id, ticket(text, UNIQUE), artifact_id FK, actor_id, expires_at, used_at NULL, revoked_at NULL, acl_epoch, created_at | |
| `teacher_artifact_download_audit` | id, ticket, artifact_id, actor_id, decision(allow/deny), reason, ip, user_agent, at | |

迁移必须：down/rollback 完整、不改动既有表、不回滚既有数据。

### S2.3 不变量（LOCAL-REGEN-INVARIANTS 的证据来源）

- **INV-1 乐观并发**：任何写 spec 的入口都必须带 `expected_revision`；不匹配 → HTTP **409**，且服务端
  **不得**产生任何副作用（新 revision / proposal 状态 / 事件 / artifact 一律不变）。
- **INV-2 局部性**：一次 patch/regen 只改 `AffectedSlideSet` 内的 slide（deck）或声明的 block（lesson plan）；
  其余 slide/block 的 `content_sha256` 必须逐字节不变。
- **INV-3 Blueprint 不可变**：patch/regen **不得**改动 `spec.projection`（approved_blueprint_id / revision /
  context_snapshot_hash / body_hash）。只允许在 regen 请求里**引用**它。
- **INV-4 先提案后落库**：修改先是 `proposed` proposal；只有 `apply` 才写 revision+1；`reject` 不写。
- **INV-5 可撤销**：每次成功 apply 都落一条 `*_spec_revisions` 快照；revert 以快照生成**新** revision（不删历史）。
- **INV-6 可编辑 PPTX**：导出 PPTX 必须是 python-pptx 可打开、元素可编辑（非图片化）；
  文档必须真实存在、非零、media_type 匹配、可被对应阅读器打开。
- **INV-7 下载即鉴权**：下载链接短时（默认 300s）、单次使用、绑定 actor+artifact+ACL；
  redeem 时**重新查权**并写审计；过期 → 410，撤权 → 403。
- **INV-8 血缘齐全**：每个 artifact 必须能回答 source lineage / spec revision / content hash /
  created_by_run / PromptRef / QA status。

### S2.4 HTTP 契约（前缀 `/api/teacher-v3/artifacts`，统一信封 `{code,message,data,request_id}`）

成功 `code=0`；冲突 `HTTP 409 / code 40900`；校验失败 422 / 42201；越权 403 / 40300；过期 410 / 41000。

**Deck（TeachingDeckSpec）**

| 方法 | 路径 | 请求 | 响应 `data` |
|---|---|---|---|
| GET | `/decks/{deck_id}` | — | `{deck_id, spec_id, revision, schema_version, spec_sha256, metadata, slide_count, spec, slides:[{id,index,title,layout_hint,qa_status}], lineage, recent_change_log}` |
| GET | `/decks/{deck_id}/revisions` | — | `{current_revision, revisions:[{revision,kind,summary,actor_id,created_at,affected_slide_ids,spec_sha256}]}` |
| POST | `/decks/{deck_id}/revisions/{revision}/revert` | `{expected_revision, summary?}` | `{deck_id, revision, reverted_to, affected}` |
| POST | `/decks/{deck_id}/patches` | `{expected_revision, summary, source, idempotency_key?, operations:[{op,target,field,value}]}` | `{patch_id, deck_id, status, base_revision, expected_revision, affected, operations}` |
| POST | `/decks/{deck_id}/patches/{patch_id}/apply` | `{expected_revision}` | `{deck_id, revision, applied_patch_id, affected, change_log_entry}` |
| POST | `/decks/{deck_id}/patches/{patch_id}/reject` | `{reason?}` | `{patch_id, status:"rejected"}` |
| POST | `/decks/{deck_id}/regen` | `{expected_revision, target:{kind:"slide",slide_id}, instruction, refs:{approved_blueprint_id,blueprint_revision,spec_revision,prompt_ref{...},context_manifest_id,evidence_refs[],source_refs[]}}` | `{patch_id, target, changed_slide_ids, proposal, refs_echo, provider:{model,run_id}|null}` |
| GET | `/decks/{deck_id}/qa` | — | 见 S2.5 |
| POST | `/decks/{deck_id}/qa/retry` | `{page_no, expected_revision}` | `{page_no, attempts, status, defects, metrics, others_unchanged:true}` |
| POST | `/decks/{deck_id}/exports` | `{format:"pptx"\|"pdf", expected_revision, idempotency_key?}` | `{job_id, format, status, task_id}` |
| GET | `/decks/{deck_id}/pages/{slide_id}` | — | `{slide_id, content_sha256, revision, slide}` |

**Lesson plan（LessonPlanSpec）**：同形，路径 `/lesson-plans/{spec_id}`，block 级：

`GET /lesson-plans/{spec_id}` · `GET /lesson-plans/{spec_id}/revisions` ·
`POST /lesson-plans/{spec_id}/revisions/{revision}/revert` ·
`POST /lesson-plans/{spec_id}/patches`（`operations:[{op:"set_block", target:"<block_id>", field, value}]`）·
`POST /lesson-plans/{spec_id}/patches/{patch_id}/apply|reject` ·
`POST /lesson-plans/{spec_id}/regen`（`target:{kind:"block",block_id}`）·
`POST /lesson-plans/{spec_id}/exports`（`format:"docx"`）

**导出作业 / artifact / 下载（deck 与 lesson plan 共用）**

| 方法 | 路径 | 请求 | 响应 `data` |
|---|---|---|---|
| GET | `/exports/{job_id}` | — | `{job_id, format, status(pending/running/done/failed/cancelled), progress:{stage,page_no,page_count}, artifact_id, error, events:[{seq,type,ts,detail}]}` |
| POST | `/exports/{job_id}/cancel` | — | `{job_id, status:"cancelled"}` |
| GET | `/{artifact_id}` | — | `{artifact_id, version, kind, media_type, size_bytes, content_sha256, created_at, created_by_run_id, spec_kind, spec_id, spec_revision, source_lineage, acl, retention, qa_status, download:{ticket_required:true}}` |
| POST | `/{artifact_id}/tickets` | `{ttl_seconds?}` | `{ticket, url, expires_at, single_use:true}` |
| GET | `/downloads/{ticket}` | — | **原始字节**（`Content-Type=media_type`，`X-Content-Sha256`，`Content-Disposition: attachment`） |

> 路径歧义消解：`/downloads/{ticket}` 必须注册在 `/{artifact_id}` **之前**。

**409 冲突载荷（`data`，deck 与 lesson plan 同形）**

```json
{ "kind": "revision_conflict",
  "expected_revision": 3, "current_revision": 5,
  "server_spec_sha256": "…",
  "diff": { "summary_zh": "…",
            "fields": [ {"target":"slide-03","field":"content","base_value":"…","server_value":"…"} ],
            "slide_ids": ["slide-03"] },
  "operations": [ {"index":0,"status":"conflict"|"applied"|"unknown","message":"…"} ],
  "server_snapshot": {"revision":5} }
```

### S2.5 逐页 QA 契约

`GET /decks/{deck_id}/qa` → `data`：

```json
{ "deck_id": "…", "spec_revision": 5, "artifact_id": "…|null",
  "baseline": {"name":"tp-h1","vpl_version":"r21.1-vpl-1.0.0","rubric":"VISUAL-RUBRIC.md","schema":"tp-h1-visual-qa/v1"},
  "checks": ["overflow","overlap","font","formula","graphic","duplicate","contrast","off_slide","blank","density"],
  "pages": [ {"page_no":1,"status":"pass","defects":[{"kind","severity":"p0|p1","message","shape_ids":[]}],
              "metrics":{"ink_pct":0,"cv":0,"shape_count":0,"has_picture":false},"attempts":1} ],
  "summary": {"p0_count":0,"p1_count":0,"failed_pages":[],"by_kind":{}} }
```

检测器（`app/ppt_v2/qa/detector.py`）读**真实 PPTX + 真实渲染 PNG**，独立于生成侧：
P0 = `overflow` / `overlap` / `off_slide` / `duplicate` / `graphic`（空框占位）/ `contrast`（WCAG AA < 3.0）；
P1 = `font`（缺字形 / < 12pt）/ `formula`（数学字形覆盖）; 信息性 = `density`。
阈值来自 TP-H1 `VISUAL-RUBRIC.md`（overlap 0.04in、页面 13.333×7.5in、body 12pt、ink<6% 等），**不得**改阈值。

### S2.6 导出链路（真实，不伪造）

```
8100 export job (taskiq worker)
  ├─ deck:  8100 ppt_v2_deck_specs ─HTTP→ 8200 /decks/ingest ─→ deck_id
  │         8200 /jobs {job_type:"ppt.deck.generate"} → 轮询 /jobs/{id} → COMPLETED
  │         8200 /decks/{id}/artifacts → kind=DECK_PPTX → 取字节（新增 content 端点）
  │         → sha256 校验 → 8100 MinIO teacher-artifacts/… → teacher_artifacts(kind=pptx)
  │         → [format=pdf] 8201 POST /api/v1/deck/export/pdf（PowerPoint COM SaveAs 32）→ teacher_artifacts(kind=pdf)
  └─ lesson plan: LessonPlanSpec → app/teacher_prep/specs/docx_render.py → .docx bytes
                → 8100 MinIO → teacher_artifacts(kind=docx)
```

失败必须诚实：provider/8200/8201 失败 → job `failed` + `error`，**不产出**假 artifact。

### S2.7 交付物与验证

- 迁移：`alembic/versions/r22_t24_spec_edit_and_artifacts.py`（单 head，承接 `f19a2b3c4d5e`）。
- 后端命令（uv 已装，但 repo 亦可用 venv 直呼）：
  `& D:\math-arena\services\teacher-platform\.venv\Scripts\python.exe -m pytest tests/unit tests/contract -q`
- 前端命令：`pnpm --dir D:\frontend typecheck|test|build|e2e`。
- 引擎命令：`.venv\Scripts\python.exe -m pytest tests -q`（8200 / 8201 各自）。
- 真实渲染：PowerPoint COM 16（`presenton_adapter.engine.renderer`），LibreOffice 本机**不存在**。

## [S3] Out of Scope

- 不改 TeachingBlueprint 事实与 R20 投影算法；不改 Student / Research 任何文件。
- 不更换 PPT 生产引擎（仍 8200 VPL + 8201 python-pptx），不做像素坐标写入。
- 不动冻结域（`app/export/**`、`app/tasks/jobs/export_pptx.py`、`app/pagination/**`、`app/domains/{contents,generation}/**`）。
- 不做 PDF 进 OMML/可编辑公式；PDF 由 PPTX 经 COM 导出（保真渲染，不做公式语义化）。
- 不做多人实时协同（仅乐观并发 + 冲突解决）。

## Tasks

- [ ] T1: 迁移 + ORM（deck 修订/proposal/QA + lesson plan 修订/proposal + artifact/ticket/audit） — acceptance: 迁移可 upgrade/downgrade，旧表不变
- [ ] T2: deck 块/页级 patch proposal + apply(409) + reject + revisions/revert — acceptance: 409 无副作用；INV-2/3/4/5 有测试
- [ ] T3: lesson plan block 级 patch proposal + apply(409) + revisions/revert + docx 渲染 — acceptance: 同 T2（block 粒度）
- [ ] T4: 局部重生成（deck slide / lesson plan block）+ 显式 refs — acceptance: 只有目标变化，blueprint 引用不可改
- [ ] T5: 逐页 QA 检测器（7 维 + TP-H1 阈值）与 `/qa` `/qa/retry` — acceptance: 真实 PPTX+PNG 上可定位失败页并单页重试
- [ ] T6: 导出作业（pptx/pdf/docx）经 8200/8201 + artifact 注册（hash/lineage/created_by_run） — acceptance: 文件真实存在、非零、类型匹配、可打开
- [ ] T7: 下载票据（短时/单次/ACL/审计/过期/撤权） — acceptance: 过期 410、撤权 403、审计有行
- [ ] T8: 路由注册 + 前端接线（proposal/409 diff/QA/导出/血缘/下载） — acceptance: 真实 8100 走通，无 capability-disabled 桩
- [ ] T9: 测试（backend 失败先行 + vitest + Playwright）+ 真实渲染 contact sheet + hashes — acceptance: RED→GREEN 证据齐备
- [ ] T10: 10 份交付文档 + FINAL-GATE
