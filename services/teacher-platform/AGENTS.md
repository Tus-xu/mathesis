# Teacher Platform（教师端 V3 平台）

本目录规则在 `../../AGENTS.md`（仓库根）之上生效，近目录规则优先。先读根规则，再读本文件。

## 服务与命令

- 本服务是 FastAPI（app/），Python 3.12，通过 `uv run` 执行
- 关键命令：
  - `uv run uvicorn main:app --host 0.0.0.0 --port 8100 --reload` 启动开发服务器（make api）
  - `uv run pytest tests/unit -q` 单元测试（make test）
  - `uv run pytest tests/integration/valkey_gate -q -m gate` 集成门禁（make gate）
  - `uv run ruff check .` ruff 检查
  - `uv run alembic upgrade head` 数据库迁移（**生产禁止**；本地如需迁移必须提供 down/rollback 说明）
- 禁止运行 `make up`、`make seed`、任何会写数据库、触发种子或迁移的构建命令（纯审计场景）

## LEGACY/FROZEN 域（冻结，禁止扩展）

以下均视为 LEGACY/FROZEN，只修复生产阻断或安全漏洞（需单独审批），**不得**把 V2 功能塞回这些模块：

- `app/domains/generation/`（旧生成器、deck-outline、deck SSE）
- `app/domains/contents/`（旧 Deck/Plan/slide CRUD、PPTX import/export 语义）
- `app/export/pptx_engine.py`
- `app/tasks/jobs/export_pptx.py`
- `app/pagination/`（旧分页/layout）
- `app/prompts/deck/`
- 现有 `Deck.slides` JSONB 与 V3 element schema
- `/api/teacher-v3/generation/*`、`/api/teacher-v3/decks/*` 现有行为

## V2 命名空间规则（PPT Studio）

- 新功能只允许在 `app/ppt_v2/` 新命名空间内开发；**禁止创建空目录预占位**，目录随真实代码出现
- `app/ppt_v2/**` 禁止 import 以下 legacy 模块（由 `tests/contract/test_ppt_v2_boundary.py` 静态守卫）：
  - `app.export.pptx_engine`
  - `app.export` 整包
  - legacy `app.domains.generation`、`app.domains.contents`（旧 Deck/Plan schema）
  - `app.pagination` 旧模块
- V2 只能通过 adapter 使用平台能力：AI（LiteLLM Gateway）、RAG、Storage（MinIO）、Auth、课程上下文；adapter 返回 V2 DTO，不泄露供应商响应
- TeachingDeckSpec 是 V2 唯一事实来源，禁止在 V2 model 中出现任意引擎 `x/y/w/h` 像素坐标；`layout_hint` 只能是语义枚举
- domain 层禁止 import LiteLLM、MinIO、Presenton、旧 V3 schema

## 教材资源库 V2（app/textbook_v2/）

- 模型/权利状态机在 `app/textbook_v2/models.py` + `rights.py`；导入器在 `app/textbook_v2/importers/`；migration 头为 `a05b1c2d3e4f`（R05 建表，已应用本地 PG）
- **教材源铁律**：唯一教材源 = 已授权切片（`D:\知识库\AC切片`，rights_evidence_ref='队伍确认授权'）；不使用原 PDF、不使用 TextbookPage
- 切片入 3 层表：TextbookSource→TextbookSection→TextbookChunk（R06 已完成 22 批，全 STAGED；幂等键 sha256(batch_id+file_sha256)，同 section 重复内容去重）
- 语义提取目标表：`textbook_content_units`（R07）+ `textbook_problems`（R07）；图题资产（MinIO/AssetRef/Vision/老师上传）归 R13
- 升级 ACTIVE 前置：R08 索引+检索可用，经 rights 状态机（禁止业务层直改 rights_status）

## UI/PPT 证据要求

- 涉及 UI：必须用真实浏览器以 teacher 身份走完整路径，至少桌面 1440×900，交付关键步骤截图和控制台/网络错误摘要
- 涉及 PPT：必须真实生成 `.pptx`，渲染每页 PNG/PDF 和 contact sheet（LibreOffice headless → PDF → PNG），并人工用 PowerPoint 打开检查 repair prompt；只给 JSON 不算完成
- 禁止仅用颜色卡/色块作为模板预览

## 数据库迁移

- 任何新增 migration 必须具备：down/rollback 说明、对旧表的向后兼容测试、不回滚旧资源数据
- 禁止 moving 现有 production 数据；无法验证则标 UNKNOWN，不猜测实现

## 备课中心 V2 重构轮

- 遵循 `D:\教师端备课中心重构宪法\PROMPT.md`（主契约）与 `docs/product/teacher-prep-center/V2-BACKEND-KERNEL-GUIDE.md`（内核判据）。
- `课堂设计.png`、`PPT.png`、`教案.png` 三张 Golden 为备课链路视觉基线（产物质量判据见 ACCEPTANCE-GATES G15–G20）。
- 既有内核（teacher_prep / ppt_v2 / specs 导出 / 8200–8201 导出链）必须服务该体验：KEEP > ADAPT > REPLACE > REMOVE，禁止平行内核。

## 验证与 STOP

- 每轮改动必须运行最小相关测试 + `git diff --check` + 边界测试；无法运行必须给出可验证原因和替代证据
- 结束必须输出 `STOP — 等待人工验收，不开始下一轮。`