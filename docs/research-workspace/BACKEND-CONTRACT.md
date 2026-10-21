# 科研端后端契约与架构决策（BACKEND-CONTRACT）

> 本文档是科研端**后端/全栈的唯一技术真相源**，配合 `RESEARCH_PRODUCT_CONTRACT.md`（产品行为）与 `VISUAL-SPEC.md`（视觉）使用。
> 所有结论均来自对现有代码与运行环境的实测，不是推测。

---

## 1. 架构决策（已冻结）

### D1 — 服务权威划分（不新建服务、不新建库）

| 能力 | 权威 | 说明 |
|---|---|---|
| 身份 / 用户 / 角色 / JWT | **8000**（`services/api`） | 唯一权威，不新建账号库 |
| 科研领域（Project/Asset/Paper/Citation/Evidence/Job/Compile） | **8300**（`services/research-vnext`，库 `research_vnext`，schema `rv8300`） | 已有 16 条迁移 / 38+ 表 / 175 端点 |
| 沙箱执行（Python 实验） | `services/sandbox`（`math-arena-sandbox:0.2.0`） | 仅 Python，**不含 LaTeX** |
| LaTeX 编译 | 8300 → Docker 镜像 **`math-arena-tex:0.1.0`** | 已存在镜像，non-root/ro/no-net/cpus/mem/pids 限制 |

**禁止**：新建 research-v2 / v3 服务；把 8000 的旧 research 域（`app/domains/research/`，默认 `research_enabled=False`）复活成第二权威。

### D2 — 网关链路（这是本轮最重要的一处修复）

**正确链路（必须）：**
```
浏览器 ──/api/research-vnext/*──► 8000 (JWT 校验 + 派生 AccessContext + HMAC 注入)
                                      └──► 8300 (只信任网关注入的 X-Research-Access-*)
```

**现状缺陷（必须修复）**：`D:\frontend\vite.config.js` 把 `/api/research-vnext` 直连 `8300`，**绕过 8000 网关**。后果：
- 8300 收不到 HMAC 签名 → **恒 401 fail-closed**（这就是"AI 服务故障频出"的架构级根因之一）
- 前端只能塞「开发签名桥」读 8300 的 `.env` 密钥 → **dev 能跑、prod 必挂**
- 没有真实研究者身份

**修复动作**：把 `vite.config.js` 中 `/api/research-vnext` 的 `target` 由 `researchVnextProxyTarget`(8300) 改为 `apiProxyTarget`(8000)。8000 的 `app/gateway/research_gateway.py` 会注入签名并流式透传 SSE（含 `Last-Event-ID`）。

**已实测通过（2026-09-16）**：
```
POST /api/auth/challenges/sms  → demo_code
POST /api/auth/login/sms  {preferred_role:"researcher"}  → access_token
GET  /api/research-vnext/projects   (Bearer)  → HTTP 200 {"items":[]}
GET  /api/research-vnext/welcome    (Bearer)  → HTTP 200 {...}
```

### D3 — 身份：researcher 角色

- 8000 已支持 `researcher` 角色（注册需 `research_direction` + `organization_name`）。
- `role_bindings` 表列为 `id/user_id/role/org_name/verified/created_at/updated_at/deleted_at`；ORM 里 `verified` 由 `_legacy_verified` + `status` 双写。
- 登录短信路径在 `review_enabled=False` 时对 `teacher/researcher` **自动批准**绑定 → 本地可直接获得 researcher JWT。
- **实测事实：全库 396 用户中 researcher = 0**。科研端从未有过真实用户（用户投诉"没有用户"由此坐实）。
- 产品侧必须补齐：科研端登录/注册入口、角色引导、进入科研工作台的入口（宪法 §48）。

### D4 — Embedding 铁律（不得违反）

教师平台 768 维 / 通用 API 1024 维 / 8300 声明 768 但未使用。**禁止交叉写索引**。
8300 **没有 pgvector、没有 embedding 列**。项目知识检索**优先用 PG 全文检索**（`tsvector` / `pg_trgm`）在项目文献解析文本上实现，不引入新向量设施；如后续确需语义检索，另立迁移。

---

## 2. 现有能力盘点（复用清单，附真实路径）

| 能力 | 实现路径 | 复用方式 |
|---|---|---|
| 会话 / 消息 / SSE | `research_vnext/core/api/routes.py`、`stream_routes.py` | 直接用 |
| 项目容器 | `core/project/models.py`、`core/api/project_routes.py`、表 `research_project`(0016) | 扩展 |
| 资产 + 签名 URL | `core/assets/{storage,security,signing}.py`、表 `research_asset`/`asset_signed_url`(0006) | 直接用 |
| PDF 解析 + 锚点 | `core/parser/`（docling）、表 `asset_parse_run`/`asset_anchor`(0007) | 直接用 |
| 文献记录 | 表 `literature_record`/`literature_dedup_conflict`(0006) | 直接用 |
| LaTeX 编译 | `core/export/compiler.py`(`DockerTexCompiler`)、`core/xf/latex.py`、`POST /xf/latex:compile`、Valkey 流 `strm:rv8300:compile` | 扩展成项目级 |
| 引用 | `core/citation/`（csl/formats/roundtrip）、表 `research_citation`(0009) | 直接用 |
| 证据 | `core/evidence/`（locator/origin_grounding/resolvable）、表 `research_evidence_card`/`evidence_card` | 直接用 |
| 数学验证 | `core/verification/`（registry/adapters/sympy 经沙箱）、表 `verification_record` | 直接用 |
| 写作 | `core/writing/`（latex_editing/method_rag/academic_translate）、`wb_routes.py` | 直接用 |
| 模型网关 | `core/llm/gateway.py`（LiteLLM Router）+ `RESEARCH_LLM_*` | 直接用 |
| Job 运行时 | `core/worker/`、表 `job_run`/`job_inbox`/`job_dlq`(0004)，lease+fencing_token | 直接用 |
| 审计 / 事件 | `command_record`/`domain_event`/`outbox_event`/`object_revision_audit`/`paper_edit_audit` | 直接用 |
| 上下文清单 | `core/llm/context_manifest.py`、`core/memory/` | 直接用 |

---

## 3. 缺口与新增契约（P0，黄金图可见能力全部要真）

> 原则：**加法式**。新增表走 alembic 新迁移（下一个版本号 `0017_*`），不修改既有迁移。
> 所有新端点同时挂 `/api/research-v1` 与 `/api/research-vnext`（沿用 `config.api_prefixes()`）。
> 所有端点沿用现有 `authorize_session` 六层鉴权 + Web 统一错误信封。

### 3.1 项目级文献（补齐 metadata 与归属）

```
GET    /projects/{project_id}/papers            → [{id,title,authors[],year,venue,doi,asset_id,read_state,page_count,parse_status}]
POST   /projects/{project_id}/papers            (multipart 上传 或 {asset_id}) → paper
DELETE /projects/{project_id}/papers/{paper_id}
PATCH  /projects/{project_id}/papers/{paper_id} {read_state, last_page}
GET    /projects/{project_id}/papers/{paper_id}/text?page=N   → {page, width, height, blocks:[{text,bbox[],section}]}
```
- 元数据解析失败**不得**导致文献不可用（宪法 §10）：`parse_status=failed` 仍可读 PDF。
- 阅读定位：`{paper_id, page, bbox, selected_text}` 作为证据/笔记/Agent 的 locator（宪法 §14）。

### 3.2 笔记（新增）

新表 `research_note`：
`id, project_id, actor_id, title, body, paper_id?, page?, section?, selected_text?, xref_kind(paper|agent|manual), xref_id?, created_at, updated_at, deleted_at`
```
GET    /projects/{project_id}/notes            → 列表（含 locator）
POST   /projects/{project_id}/notes            → 创建
PATCH  /notes/{note_id}  /  DELETE /notes/{note_id}
GET    /projects/{project_id}/notes/recent?limit=6
```
- 点击笔记必须能回到 paper/page/selection（宪法 §11/§44）。

### 3.3 草稿文件树（新增）

新表 `research_project_file`：
`id, project_id, path, kind(tex|bib|sty|cls|image|pdf|data), content(text|null), asset_id?, revision, updated_at`
```
GET    /projects/{project_id}/files            → 树
GET    /projects/{project_id}/files/{path}     → {path,content,revision,saved_at}
PUT    /projects/{project_id}/files/{path}     {content, expected_revision}  → 乐观并发（冲突 409）
POST   /projects/{project_id}/files            {path, kind}  → 新建
DELETE /projects/{project_id}/files/{path}
POST   /projects/{project_id}/files:rename     {from,to}
GET    /projects/{project_id}/outline          → 由 main.tex 的 \section/\subsection/\begin{abstract} 解析出的结构树（含行号）
```
- `GET outline` 必须返回**行号**，供 Editor 定位（宪法 §18）。
- `PUT` 必须做**自动保存**语义 + revision 冲突保护（宪法 §51）。

### 3.4 项目级编译（扩展既有 compiler）

```
POST /projects/{project_id}/compile   {engine:"xelatex", entry:"main.tex"} → {job_id 或 artifact}
GET  /projects/{project_id}/compile-runs/{run_id}
GET  /projects/{project_id}/compile-runs/latest → {status, pdf_artifact_id, log_lines:[{ts,level,text,file?,line?}], duration_ms, error_count, warning_count}
```
- 实现：把 `research_project_file` 组装成目录树 → 写入临时工作目录 → 复用 `DockerTexCompiler`（`math-arena-tex:0.1.0`）→ 产出 PDF 存 S3 → 解析 `.log` 为结构化 `log_lines`（**含 file+line**，用于点击定位）。
- **必须支持**多文件、images、BibTeX/Biber、中英文（宪法 §52）。
- 编译失败**不得清空 preview**：`latest` 必须同时返回 `last_successful_pdf_artifact_id`（宪法 §23）。
- 隔离：沿用既有限制（non-root、read-only、no-net、cpus/mem/pids）；用户 LaTeX 是不可信输入（宪法 §53）。

### 3.5 Research Agent 任务（能力 → 真实后端的映射）

统一任务入口（避免每加一个能力再造一种卡片）：
```
POST /projects/{project_id}/agent/tasks  {preset, context:{...}}  → {task_id}
GET  /agent/tasks/{task_id}             → 结构化 Research Result
```
**结构化结果 schema（宪法 §58）**：
```jsonc
{
  "kind": "derivation_check|citation|latex_compare|formula_check|translate|polish|latex_error|suggestion",
  "status": "verified|supported|likely|needs_conditions|insufficient|conflict|failed",
  "summary": "字符串",
  "findings": [{"label":"", "detail":"", "math":""}],
  "evidence": [{"paper_id":"","title":"","authors":"","year":0,"page":0,"section":"","locator":{}}],
  "source_locations": [{"file":"main.tex","line":28}],
  "patches": [{"file":"main.tex","line":28,"before":"","after":"","reason":""}],
  "actions": ["locate","view_evidence","add_note","insert_citation","apply_patch","recompile"],
  "confidence": 0.0
}
```

**Preset → 后端能力映射（必须落到真实能力，禁止写死模板）**

| preset | 落点 |
|---|---|
| `derivation_check`（推导校验） | `claims:propose` + `claims/{k}/verifications:run`（sympy via sandbox）+ evidence locator |
| `citation`（引用补全） | `core/citation/` + `literature_record`（项目内优先）→ 未核验必须标「未验证引用」（宪法 §37） |
| `latex_compare`（LaTeX 对照） | 取当前 paper 结构/公式 + 当前 `.tex`，做一致性比对 |
| `formula_check`（公式检查） | `verification-verifiers` + 沙箱符号验证 |
| `translate` / `polish` | `POST /wb/translate`、`core/writing/`；**必须保护 `\cite{}`/`\ref{}`/`\label{}`/math block/macro**（宪法 §41） |
| `latex_error`（LaTeX 错误解释） | 读 `compile-runs/latest` 的 error 行 → LLM 解释 + 生成 patch |
| `suggestion`（改进建议） | `core/writing/` + 项目知识 |
- 所有 `patches` **必须经用户确认**才写入（宪法 §32/§40）；写入走 `PUT /files/{path}` 并记 `paper_edit_audit`。
- 长任务必须是 **Task**（`queued/running/completed/failed/cancelled`），关闭 Accordion 后不消失（宪法 §54）。
- Activity 状态只显示有意义的过程（`正在读取当前段落` 等），**禁止暴露 chain-of-thought**（宪法 §55）。

### 3.6 项目知识检索（不引入向量设施）

```
POST /projects/{project_id}/knowledge/search  {query, limit} → [{paper_id,title,page,section,snippet,score}]
```
- 基于已解析文本 + PG 全文检索 / trigram。
- 前端文案只出现「项目知识」「相关文献」，**不得出现 RAG/embedding/top-k**（宪法 §45/§85）。
- 回答需可披露「基于当前项目 N 篇文献和 M 条笔记」（宪法 §45）。

### 3.7 项目概览（AI 项目概览卡的真数据）

```
GET /projects/{project_id}/overview → {
  summary: "研究有限元方法的误差估计理论，探索椭圆型偏微分方程的数值解法",  // Project Memory Summary，来自项目数据，非固定串
  stats: { papers: 12, drafts: 3, notes: 4, data: 2 },
  updated_at
}
```
- `summary` 必须由项目数据生成（LLM 或规则），**禁止固定字符串**（宪法 §70/§80）。
- 统计四项文案以黄金图为准：`文献` `草稿` `笔记` `数据`。

### 3.8 历史 / 状态恢复

```
GET /projects/{project_id}/recent  → {workspace:"read|write", paper_id, page, file_path, line, updated_at}
PUT /projects/{project_id}/recent
GET /projects/{project_id}/history → 最近打开项目/论文/写作/Agent Session（宪法 §50）
```
- 重新进入项目须恢复 READ/WRITE、最近文献、最近页码、最近 tex file（宪法 §84）。

---

## 4. 本地运行环境（已实测可用，2026-09-16）

| 组件 | 地址 | 启动方式 |
|---|---|---|
| PostgreSQL 15.18 | **127.0.0.1:54329** | `D:\PostgreSQL\pgsql\bin\postgres.exe -D "C:/Users/<user>/pgdata/data" -p 54329`（**必须显式 `-p 54329`**，postgresql.conf 默认 5432；需常驻前台进程，用 `run_in_background`） |
| Valkey | 127.0.0.1:6380 | `docker run -d --name ma-valkey-6380 -p 6380:6379 valkey/valkey:9.1.2-alpine` |
| MinIO (S3) | **127.0.0.1:9002** | `docker run -d --name ma-minio-9002 -p 9002:9000 -p 9003:9001 -e MINIO_ROOT_USER=$AK -e MINIO_ROOT_PASSWORD=$SK minio/minio server /data`（凭据取 `services/research-vnext/.env` 的 `RESEARCH_S3_*`，**不得回显**） |
| research-vnext 8300 | 127.0.0.1:8300 | `cd services/research-vnext && PYTHONPATH=. RESEARCH_ENV=prod .venv/Scripts/python.exe -m uvicorn research_vnext.core.api.app:app --port 8300` |
| 通用 API 8000 | 127.0.0.1:8000 | `cd services/api && DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:54329/math_arena RESEARCH_GATEWAY_HMAC_SECRET=$(grep ... research-vnext/.env) JWT_SECRET=... .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000` |
| 前端 dev | 127.0.0.1:5176 | `cd D:\frontend && pnpm dev`（Vite） |
| LLM | 火山 ARK `doubao-seed-2.0-mini` | 凭据在 `services/research-vnext/.env` 的 `RESEARCH_LLM_*` |
| LaTeX 镜像 | `math-arena-tex:0.1.0` | 已存在，无需构建 |

**迁移状态（务必先跑）**
- `research_vnext`：`alembic upgrade head` → 已到 **0016_rv16_project_stream**
- `math_arena`：`cd services/api && DATABASE_URL=... alembic upgrade head` → 已到 **trial_001_feedback**（缺它会 500：`users.phone_verified_at` 不存在、`tasks` 表不存在）

**登录（本地 E2E 用）**：`bash tmp/rw_login.sh 13800138000` → 写 `tmp/.rw_token`。
短信验证码在 dev 模式由接口直接返回 `demo_code`；同号 60s 限流，重试用 `retry_after`。

---

## 5. 前端落点（配合 VISUAL-SPEC）

- **新建干净命名空间**：`D:\frontend\src\features\research-workspace\`
  - `tokens/research-tokens.css`（`--rw-*`，唯一 token 源）
  - `shell/`（GlobalHeader / WorkspaceBar / ResearchWorkspaceShell）
  - `read/`（ProjectSidebar / PdfReader / SelectionToolbar）
  - `write/`（FileTree / Outline / LatexEditor / PdfPreview / CompileLog）
  - `agent/`（ResearchAgentPanel / ContextChips / Accordion / ResultCard / DiffBlock / Composer）
  - `ui/`（Result Design System 原子：Finding/Evidence/Source/Diff/Warning/Success/Action）
  - `api/`（统一走 `/api/research-vnext`，**复用 8000 会话，不自造鉴权头**）
- **路由**：科研端入口指向新命名空间；旧 `paper-workbench`（表单式面板，即视频里被投诉的形态）**退出路由**，文件保留不删（删除由人工执行）。
- **复用**：`pdfjs-dist`（PDF 渲染）、`@codemirror/*` + `stex`（编辑器）、`katex`（公式）、`marked`（Markdown）。
- **不得**复用 `--ma-*` / `--re-*` 两套旧 token（主色与黄金图不符，且存在双源冲突）。

---

## 6. 交付纪律

- 单主题提交；`git add -- <明确路径>`，**禁止 `git add .`**；提交前做密钥扫描。
- 秘密只从环境变量/`.env` 读取，**禁止**出现在代码、日志、截图、commit。
- 每轮结束输出 `STOP — 等待人工验收，不开始下一轮。`（人工将按其节奏推进）
- 任何「功能/UI」改动必须附**真实浏览器截图**与命令输出证据，禁止只声称完成。
