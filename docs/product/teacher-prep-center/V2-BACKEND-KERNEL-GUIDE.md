# 教师端备课中心 · 后端内核准则（V2，Lead 裁决判据）

> 适用：`services/teacher-platform` 及 8200/8201/导出链路的备课中心改动。
> 上游契约：`docs/product/teacher-prep-center/TEACHER_PREP_CENTER_PRODUCT_CONTRACT.md`（主契约）> `docs/product/teacher-prep-center/ACCEPTANCE-MATRIX.md` > 本文件 > 旧 `KERNEL-RUBRIC.md`（内核质量判据继续有效）。
> 配套：前端判据 `docs/product/teacher-prep-center/VISUAL-SPEC-V2.md`；视觉基线见 `docs/product/teacher-prep-center/golden/`。
> 调研事实来源：2026-09-27 内核调研（文件/符号名均已核对）。

---

## 1. 既有真实能力清单（KEEP，禁止重造/弄丢）

| 能力 | 位置（权威） | 说明 |
|---|---|---|
| 会话/蓝图/对话领域模型 | `app/teacher_prep/models.py`（tp_sessions/tp_blueprints/tp_blueprints_revision…）、`conversation/models.py` | 8100 事实权威 |
| 蓝图投影（确定性） | `teacher_prep/blueprint/projector.py`、`specs/projector.py`（`project_lesson_plan_spec`、`project_deck_spec`） | 无 LLM、可复现 |
| PPT deck 契约 | `app/ppt_v2/domain/spec.py`（`TeachingDeckSpec` v2.0.0、`SlideSpec`、`SlideElement`、`MathObjectSpec`） | 唯一事实源；禁像素坐标 |
| deck patch/并发 | `ppt_v2/domain/patch.py`（`PatchOp`/`DeckPatchProposal`、`apply_proposal` + `expected_revision`） | AI/教师修改统一走 patch |
| 椭圆参数化链 | `specs/projector.py::_ellipse_*` → `MathObjectSpec` → **8201** `presenton_adapter/engine/figures.py::ellipse_figure` | spec 派生参数→确定性绘制 |
| PPTX 真实导出 | `tasks/jobs/export_artifact_v2.py`（→8200 `DeckEngineClient` →8201 python-pptx assembler →MinIO+sha256+`register_artifact`） | 真实 OOXML，文本/形状可编辑 |
| 教案三格式导出 | `teacher_prep/specs/doc_render.py`（markdown/PDF reportlab/DOCX python-docx） | 共用 `_document_lines` |
| 任务/作业状态 | `platform_contracts/job.py`（`JobStatus` QUEUED/RUNNING/RETRYING/PARTIAL/SUCCEEDED/FAILED/CANCELLED）、taskiq `tasks/registry.py` | 进度只准映射真实 job |
| 下载/审计 | `storage/artifacts.py`（MinIO bucket teacher-exports、ticket、ACL/audit） | |

## 2. 平行内核禁令（FROZEN / 禁扩展）

- **FROZEN**（只修生产阻断/安全，禁塞 V2 功能）：`app/domains/generation/`、`app/domains/contents/`、`app/export/pptx_engine.py`（含其 matplotlib 椭圆）、`app/tasks/jobs/export_pptx.py`、`app/pagination/`、`app/prompts/deck/`、`/api/teacher-v3/generation/*` 与 `/api/teacher-v3/decks/*` 现行为。
- **并立但禁扩**：`app/teacher_prep_v2/`（跨端契约）不得演化为第二套备课内核；新能力一律落 `teacher_prep/` 或 `ppt_v2/`。
- **禁止新建**：`NewModelGateway`、`XxxDeckEngine2`、第二套导出器/椭圆绘制器/状态机。adapter/facade 优先（KEEP > ADAPT > REPLACE > REMOVE）。
- 契约双份风险：`ppt_v2/domain/spec.py` 是**唯一权威**；`services/ppt-studio-v2/app/contracts/deck.py` 只作 8200 边界 DTO，不得反向定义语义（注意其 `extra=ignore` 曾丢 `math_object`）。
- 导出状态机三套并存（export_jobs 表/tasks 表/JobStatus 投影）：对外只暴露 `JobStatus` 投影口径，不得再加第四套。

## 3. 生成链契约（PROMPT §5 的内核落地）

链路：`自然语言 → 教材定位 → 蓝图提案 → 老师确认 → 教学流程 → PPT/教案同源投影 → 老师编辑 → 导出`

1. **同源**：PPT（`project_deck_spec`）与教案（`project_lesson_plan_spec`）必须读同一 approved blueprint revision + 同一 context snapshot（血缘 `projection` 四字段齐全，`body_hash` 真实）（G20）。
2. **空态有出路**：无 deck/无 spec 时 API 必须提供可达下一步（propose/confirm/generate 真实入口），前端空态按钮接真 API（G11）。
3. **进度=真实 job**：queued/running/progress/completed/failed 全部来自 `JobStatus`/SSE（`conversation/events`、`artifacts/exports/{job_id}`）；失败保留已完成部分，支持**单页重试**（`ppt_v2` `/regen`、lesson-plan `/regen`）（G13/G14）。
4. **0 页禁导出**：`POST /artifacts/decks/{id}/exports` 与 lesson-plan exports 在无产物时必须拒绝并给出原因（前端据此禁用按钮，G12）。
5. **产物真实**：PPTX 可打开、可编辑（解包 `<a:t>` 存在、无整页截图伪装）、DOCX/PDF/MD 真实（G15/G18）。
6. **教师主权**：一切 AI 修改走 patch + `expected_revision` 乐观并发；AI 不得覆盖教师手工块（edit origin）；重进/刷新可还原。

## 4. 反幻觉与数学正确（旧 KERNEL-RUBRIC §1.2 继续有效）

- 占位符（`（待确认…）`）不得进产物；根治在 LLM 提议阶段产出真实内容，投影器禁止"猜"填充。
- 教材引用必须来自 `textbook_v2` 检索（SourceLocator v1），禁止编造页码；例题三源分标签（教材例题/题库/AI 生成）。
- 椭圆：只存独立参数 a/b，`c=√(a²−b²)` 恒派生不落库；`a>b>0` 强校验；公式 canonical 服务端重算（G17）。
- LLM 超时走确定性兜底并写 `warnings`/`ai_runs`；用户只见产品化文案。

## 5. API 面（改动前核对 consumers）

- `teacher_prep/router.py`：`/prep/sessions…`、`/prep/blueprints/propose|confirm|reject`、`/prep/profiles/*`
- `conversation/router.py`：`/prep/conversations`、`messages`（202 受理）、`actions`、`blueprint/node`、`revisions`、`textbook/view`、`specs`、`events`（SSE）
- `ppt_v2/api/router.py`（`/artifacts`）：`/decks/{id}`、`pages`、`patches(/apply|/reject)`、`regen`、`enrich`、`qa(/retry)`、`/decks/{id}/exports`、`/exports/{job_id}`、`/downloads/{ticket}`、`revisions/revert`、audit
- `teacher_prep/specs/router.py`：`/artifacts/lesson-plans/{spec_id}`、patches、regen、exports
- 前端直连 8100（vite 代理 `/api/teacher-v3`）；不新增 header 协议、不自造签名。

## 6. 质量验收判据（产物层，旧 §2 表继续有效）

- PPT：8–12 页、每页 ≥2 条真实内容、相邻页版式不重复、无占位、≥1 页参数化图形可联动、summary ≥3 条。
- 教案：六章非空、教学过程表与蓝图 `lesson_flow` 节点一一对应且合计 ≤45min、meta 四项真实、板书三栏有内容。
- 真实产物终审 A1–A10（渲染截图/spec 一致/解包验证/公式抽查/改参联动/一致性/持久化）。

## 7. 验证命令与边界

```bash
# services/teacher-platform 内
uv run pytest tests/unit/teacher_prep tests/unit/ppt_v2 tests/unit/textbook_v2 -q
uv run ruff check .
# ppt_v2 边界守卫（禁 import legacy）：tests/contract/test_ppt_v2_boundary.py
```

- 禁止 `make up/seed`、生产迁移；新 migration 必须带 down/rollback 说明与兼容测试。
- UI/产物证据要求见 teacher-platform AGENTS.md（1440×900 真实浏览器 + 真实 PPTX 打开检查）。
- 任何 API contract 改动先列 consumers 再改；禁止为接通前端偷建第二套认证。
