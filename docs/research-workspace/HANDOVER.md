# 科研端全栈重构 · 会话交接书（HANDOVER）

> 生成：2026-09-16 11:45（上一会话因子代理额度 429 中断，额度重置时间 **2026-09-17 01:27 UTC+8**）
> 交接对象：接手本任务的下一个会话/团队
> 写作人：上一会话的主理人（齐活林角色）

---

## ⚠️ 第一章 · 给接手者的第一指令（必读）

**不要盲信本文档。** 本文记录的是"上一会话声称做到的程度"，其中大部分有真实证据，但也包含我犯过的错误与未复验的转述。你的第一轮工作不是继续写代码，而是：

### 1.1 先独立复验以下断言（每条都给了复验方法）

| # | 上一会话的断言 | 复验方法 | 预期 |
|---|---|---|---|
| C1 | 服务全在线 | `C:/python13/python.exe D:/math-arena/tmp/detach_services.py --check` | PG/8300/8000/5176/Valkey/MinIO 全 UP |
| C2 | 网关链路通（前端→8000→8300） | `bash D:/math-arena/tmp/rw_login.sh 13800138000` 取 token 后 `curl -H "Authorization: Bearer $(cat D:/math-arena/tmp/.rw_token)" http://127.0.0.1:8000/api/research-vnext/projects` | 200 + 项目列表 |
| C3 | 后端 P0 真实可用（编译/文献/笔记/文件树/Agent） | 见 `deliverables/research-workspace/verify-lead/LEAD-VERIFICATION.md` 的 A 表，挑 3 条自己重跑 | 与记录一致 |
| C4 | 中文检索已修 | 6 条查询（`误差/误差估计/有限元/收敛/FE/Elliptic`）都应 ≥1 命中 | 全部 ≥1 |
| C5 | READ 页已具雏形 | `node D:/math-arena/tmp/rw_shot.mjs --route=/research-workspace/e9b7c1e7-31b4-46eb-99ce-987732b8ef95/read --out=verify-read` 然后看截图 | 三栏 + 真实 PDF |
| C6 | 黄金图栏宽 = READ 323/742/607、WRITE 306/590/462/314 | `C:/python13/python.exe D:/math-arena/tmp/golden_borders.py` 与 `golden_panels.py` | 边界 x≈1065(READ agent 左界)、x≈896(WRITE 编辑器右界) |
| C7 | 前端未提交改动确实存在且可构建 | `cd D:/frontend && node_modules/.bin/vite build` | 退出码 0 |

### 1.2 辩证地看待上一会话（我的自我审查，诚实版）

上一会话**犯过这些错**（都已在当时纠正，但你要知道它们的模式，避免重犯）：

1. **我把错误数字写进了验收标准**：`VISUAL-SPEC.md` 第一版 WRITE 栏宽写成 355/435/428/454（来自粗裁剪目测），**真实值是 306/590/462/314**。是 WRITE 工程师发现并质疑，我用两种独立测量方法（1px 边框检测 + 背景色分类）才确认他对我错。
   → **教训：凡是"验收标准"类的数字，必须有可复算的脚本依据，不接受目测。**
2. **协调并发失败**：我给三方工程师都写了"只加自己分支"，但共享文件 `shell/ResearchWorkspaceShell.vue` 没设"某一时刻唯一编辑者"，导致 WRITE 一路整体重写覆盖了 READ 的接线，且 WRITE 与 Agent 两路各造了一份 Agent 结果卡（违反宪法 §59/§74）。
   → **教训：共享文件必须显式串行化，口头边界不足以防覆盖。**
3. **误判过两次"缺陷"**：(a) 把工程师遗留的失败 fixture 当成 outline 缺陷；(b) 把 `PATCH` 的 409（正确乐观并发）当成缺陷——根因是我漏传 `expected_revision`。两次都是**先读契约/查现场后**才澄清。
   → **教训：判缺陷前先读契约、先查数据现场。**
4. **测量/脚本伪影**：`bash` 的 `PPID` 是只读变量（变量名撞了）；`MSYS_NO_PATHCONV=1` 会让原生程序看不到 `/tmp`、`/d/...` 这类 POSIX 路径（曾因此误报"math-arena 无 git 改动"）；`up.sh` 的 alive() 第一版有 bug。
5. **我从工程师报告"转述"而未亲自复验的部分**：T01/T02 的 typecheck/build/test 结果（工程师自述 187 用例通过）；T03 READ 的 B 区功能链（选区浮条/定位原文/笔记回跳——**尚无截图证据**）。这些你要么自己复跑，要么当作"未验证"。

### 1.3 上一会话没做完的事（诚实清单）

- **T05 Agent 面板未接线**：`agent/`(8 文件) 与 `ui/`(7 文件) 已写完但**没有接进 Shell**（我要求的 `AGENT-SLOT` 标记数 = 0）。READ 页右栏现在还是 T01 时期的「AI 协作区 › 建议队列」壳，**不是黄金图的 Research Agent 面板**。
- **T04 WRITE 未验收**：`write/`(11 文件) 已存在、Shell 的 WRITE 分支已接 `WriteSidebar`，但**没有任何 t04 截图**，编译 UI 链路/错误跳转/失败保留预览均未举证。
- **T03 READ 功能链未举证**：页面有了，但 B 区（导入/缩放/搜索/选区浮条/问 AI 带上下文/定位原文/笔记回跳）没有逐项截图。
- **`roleHome('researcher')` 未改**：研究者登录后仍落到旧丑页面。
- **旧 `paper-workbench`（表单式面板）仍在路由中**（因 3 个既有单测强依赖 `registerResearchSurface` 的严格断言，退役需先解耦测试）。
- **QA 全量验收（ACCEPTANCE-MATRIX A~F 区）未做**。
- **所有代码均未提交**（按项目惯例由人工做原子提交；前端仓还有大量**不属于本任务的预存 dirty 文件**，提交时必须甄别，禁止 `git add .`）。
- 测试数据脏：库里堆了 ~11 个工程师造的重复测试项目。

---

## 第二章 · 使命与真相源

把"丑陋、表单式、不可用"的科研端重构成 **AI-native Research Workspace**：前端与两张黄金图 **1:1**，功能全真实（真实编译/真实 PDF/真实用户/真实持久化）。

| 文件 | 作用 |
|---|---|
| `D:\math-arena\docs\research-workspace\RESEARCH_PRODUCT_CONTRACT.md` | 产品行为最高规范（用户冻结，92 条） |
| `D:\math-arena\docs\research-workspace\golden\READ-GOLDEN.png` `WRITE-GOLDEN.png` | **视觉唯一真相源**（1672×941；`D:\科研端重构产品宪法\` 有原件） |
| `D:\math-arena\docs\research-workspace\VISUAL-SPEC.md` | 从黄金图提取的可执行视觉标准（**§1.4/1.5 栏宽已经过像素校正，以它为准**） |
| `D:\math-arena\docs\research-workspace\BACKEND-CONTRACT.md` | 架构决策 + API 契约 + 本地运行手册 |
| `D:\math-arena\docs\research-workspace\ACCEPTANCE-MATRIX.md` | 逐功能验收矩阵（A 视觉/B READ/C WRITE/D Agent/E 可靠性/F 工程门禁） |
| `D:\math-arena\deliverables\research-workspace\A01\FRONTEND-ARCH.md` | 前端施工图（文件树/布局 CSS/store/任务 T01~T05） |
| `D:\math-arena\deliverables\research-workspace\verify-lead\LEAD-VERIFICATION.md` | 主理人独立验证记录（含两轮复验 + 运维结论） |
| 根/前端 `AGENTS.md` | 已加入"科研端必须遵循此 Contract"约束行 |

**冲突裁决顺序：黄金图 > VISUAL-SPEC > 其它文档。**

黄金图 2× 放大局部图（逐区精读用，务必看）：`D:\math-arena\tmp\golden-out\`（`read_crop_header/left/right`、`write_crop_header/left/center_top/right/bottom`）。

---

## 第三章 · 交接时的硬状态（2026-09-16 11:45 实测）

### 3.1 服务（全部在线；由本会话常驻后台任务持有，**本会话结束后可能全部退出**）

| 服务 | 地址 | 状态 | 备注 |
|---|---|---|---|
| PostgreSQL 15.18 | 127.0.0.1:54329 | UP | `postgres.exe -D "C:/Users/<user>/pgdata/data" -p 54329`（**必须显式 -p**，conf 默认 5432） |
| research-vnext | 127.0.0.1:8300 | UP | 代码已含全部后端修复（0018） |
| 通用 API（网关） | 127.0.0.1:8000 | UP | 需 `DATABASE_URL` 指 54329 + HMAC 与 8300 一致 |
| 前端 dev | 127.0.0.1:5176 | UP | `cd D:/frontend && node_modules/.bin/vite` |
| Valkey / MinIO | 6380 / 9002 | UP | docker 容器 `ma-valkey-6380` / `ma-minio-9002`（凭据在 research-vnext/.env） |

**一键体检/拉起**：`C:/python13/python.exe D:/math-arena/tmp/detach_services.py`（`--check` 只体检）。bash 版：`bash D:/math-arena/tmp/up.sh`。

### 3.2 数据

- `research_vnext` 库 schema `rv8300`，alembic **= 0018_rv18_search_tokens**（head）
- `math_arena` 库 = `trial_001_feedback`（head）
- **演示项目**：`e9b7c1e7-31b4-46eb-99ce-987732b8ef95`（"FE 误差估计"）——含真实多文件论文（`main.tex`+`chapters/intro.tex`+`refs.bib`，**实测编译通过 2 页 86,477 bytes，`\cite` 渲染为 `[1]`**）+ 1 篇文献（1 页 PDF，`parse_status=parsed`，asset `5219e020-4879-4324-a7ce-2a82e834003f`）+ 1 条带 locator 的笔记。**这是前端/QA 的演示数据，不要破坏。**
- 账号：`13800138000`（dev 模式短信验证码由接口直接返回 `demo_code`；同号 60s 限流，`retry_after` 在**响应顶层**）。researcher 角色登录自动批准（`review_enabled=False`）。
- 库里另有 ~11 个工程师测试项目（可清理）。

### 3.3 代码（全部**未提交**，按项目惯例由人工原子提交）

**后端**（`D:\math-arena\services\research-vnext\`，已由主理人独立验证通过）：
- 新包 `research_vnext/core/research_workspace/`（13 文件：models/service/pdfs/outline/logparse/papers/notes/files/compile/knowledge/overview/agent）
- 新 `core/api/workspace_routes.py`（40+ 端点，双前缀）
- 新迁移 `0017_rv17_research_workspace.py`（6 表）、`0018_rv18_search_tokens.py`（CJK bigram 检索）
- 改 `core/api/deps.py`（ValidationError→422 共享 helper）、`core/api/project_routes.py`、`core/api/app.py`（+2 行挂载）
- 新 `core/research_workspace/search_tokenize.py`（CJK 重叠 2-gram，索引/查询同一函数）

**前端**（`D:\frontend\src\features\research-workspace\`，命名空间 62 文件）：
- `tokens/`(1) `shell/`(7) `route/`(1) `api/`(8) `stores/`(6) `ui/`(7) `read/`(13) `write/`(11) `agent/`(8)
- 共享文件改动（本任务范围）：`vite.config.js`（**proxy 8300→8000，最关键修复**）、`main.js`、`router/index.js`、`App.vue`、`AGENTS.md`（均为加法式）
- ⚠️ 前端仓另有 ~39 个**预存 dirty 文件**（teacher-prep-v2 等，**不属于本任务**）——提交时必须逐一甄别
- Shell 现状：READ 分支已接 `read/ReadWorkspace.vue`（line 31）；WRITE 分支已接 `write/WriteSidebar.vue`（line 43）；**`AGENT-SLOT` 标记数 = 0（我的裁决未被执行完，Agent 面板未接线）**

### 3.4 证据

- `deliverables/research-workspace/shots/`：`baseline-workbench.png`（改造前基线，全图差 15.60）、`t01-skeleton/read/write.png`、**`t03-read.png`（READ 雏形，全图差 15.40）**
- `deliverables/research-workspace/compare/`：并排/叠加/热力图（`read-sbs.png` 等 6 张）
- `deliverables/research-workspace/probe/`：T01 证据（含 proxy 前后对比）
- `deliverables/research-workspace/verify-lead/`：主理人两轮独立验证记录 + JSON 证据

### 3.5 T03 READ 雏形的视觉判定（主理人人工读图 + 分区比对）

已达成：顶栏（项目胶囊"FE 误差估计"）/ 左栏五件套（项目卡、AI 概览卡含 `文献1/草稿3/笔记1/数据0`、导航、文献列表含 PDF 徽标与 `Ciarlet · 2002`、最近笔记）/ 中栏真实 PDF 渲染（文献 Tab + `1/1` 页码 + 140% 缩放 + 工具条）/ 三栏比例。
未达成：**右栏仍是 T01 的「AI 协作区 › 建议队列」壳**（分区差异 `agent-header` 26.26 / `agent-result-card` 20.52 偏差明显）——这是 T05 未落地的直接后果，也是当前最大的视觉缺口。

---

## 第四章 · 剩余工作清单（按优先级）

### P0-α · T05 Agent 面板接线（最大视觉缺口）
1. 在 Shell 的 READ 与 WRITE 右栏加 `<!-- AGENT-SLOT -->` 占位并替换为 `agent/ResearchAgentPanel.vue`
2. **`ResearchAgentPanel` 必须在 Shell 只渲染一次**（两分支共用，切工作区不 remount、不丢 `agent.context`；若与布局冲突，提为兄弟节点）
3. 宽度绑定：READ `--rw-w-agent: 607px` / WRITE `--rw-w-agent-w: 314px`（token 值若未设对应改 `tokens/research-tokens.css`）
4. **面板必须双宽度自适应**（607/314 差近一倍）：chips 换行、Diff 等宽 11–12px、动作按钮可折行、314px 下不得藏内容（黄金图 `write_crop_right.png` 证明放得下）
5. 验收：`ACCEPTANCE-MATRIX` A7 + D1~D8；点击能力**禁止弹参数表单**；置信状态中文映射（verified→已验证/supported→证据充分/likely→基本成立/needs_conditions→需要补充条件/insufficient→证据不足/conflict→存在冲突）；`patches` 必须用户确认后才 `PUT`；`roleHome('researcher') → /research-workspace`
6. **去重**：READ 工程师曾做过一版 §4.4 视觉壳（未接线，文件还在），WRITE 工程师曾在 Shell 内联过约 40 行结果卡（应已删）——统一以 `agent/`+`ui/` 为唯一实现，必要时从被废弃版本里抄好的细节

### P0-β · T04 WRITE 完成与举证
- 真实编译三态截图（running/success/failure）；`POST /projects/{id}/compile` **是同步阻塞 ~15–25s**（返回已完成 run，字段在 `compile_run` 信封内）
- 编译产物用 `GET /projects/{id}/compile-runs/{run_id}/pdf`（按 **run_id**）渲染真实 PDF
- 失败时用**上次成功 run_id** 继续显示（`last_successful_pdf_artifact_id`）
- 错误链路：日志行带真实 `file+line` → 点击 → 编辑器跳转 → gutter 标记
- 409 冲突（`PUT /files/{path}` 传错 `expected_revision`）→ 中文提示 + 静默重拉
- 自动保存三态（未保存/保存中/已保存）
- Outline 点击定位（`GET /outline` 返回 `{items:[{title,level,line,kind}]}`；**项目无 main.tex 时 404 → 诚实空态**）

### P0-γ · T03 READ 功能链举证（B 区 B1~B10）
- 导入 PDF / 翻页缩放 / 全文搜索 / **选中文本 → 浮动工具条**（`高亮/添加笔记/引用/问 AI`，`问 AI` 主蓝实心，Esc 关闭）/ 问 AI 请求体含 `{paper_id,page,bbox,selected_text,section}` / 证据定位原文 / 笔记回跳
- 关键实现已在 `read/usePdfDocument.ts`（pdfjs `fetch→Uint8Array→getDocument({data})`，**禁用** `getDocument({url})`）；bbox 的 DPR×scale 折算是已知易错点

### P1 · 收尾
- 旧 `paper-workbench` 退役路由 + 解耦 3 个单测（`rv11-route`/`rv08-workspace-ia`/`rv16-route-convergence` 对 `registerResearchSurface` 有严格断言）
- 旧 `research.css`(61KB)/`research-end.css`(31KB) 改惰性 import（本轮不删文件）
- `mapResearchError` 中文映射 + store 边界补单测
- 测试项目清理；**最终人工原子提交**（前端须甄别预存 dirty；禁 `git add .`）
- QA 全量验收（A~F 区）+ 视觉终审（1672×941 DPR=1 逐区对黄金图）

---

## 第五章 · 环境与坑（runbook）

```bash
# bash 每条命令的前缀（dirname/ls 否则 command not found）
export PATH="/c/Users/<user>/.workbuddy/binaries/PortableGit/versions/1.2.0/usr/bin:$PATH"

# 传路径给原生程序（node/python/curl 的 @file）必须：
export MSYS_NO_PATHCONV=1
#   ⚠️ 代价：/tmp、/d/... 这类 POSIX 路径对原生程序失效 → 一律用 D:/ 盘符路径
#   ⚠️ bash 只读变量不能用：PPID 是只读的，别拿它当论文ID变量名

# 服务体检/拉起（幂等）
C:/python13/python.exe D:/math-arena/tmp/detach_services.py          # 拉起缺的
C:/python13/python.exe D:/math-arena/tmp/detach_services.py --check  # 只体检
bash D:/math-arena/tmp/up.sh

# 取 researcher JWT（写入 tmp/.rw_token，不打明文）
bash D:/math-arena/tmp/rw_login.sh 13800138000

# 截图（1672×941 DPR=1，真实登录，采 console/网络错误；须在 D:/frontend 下）
export MSYS_NO_PATHCONV=1 && cd /d/frontend
node "D:/math-arena/tmp/rw_shot.mjs" --route=/research-workspace/e9b7c1e7-31b4-46eb-99ce-987732b8ef95/read  --out=<name>

# 与黄金图比对（并排/叠加/热力图/分区差异表/栏宽核对）
C:/python13/python.exe D:/math-arena/tmp/rw_compare.py read  D:/math-arena/deliverables/research-workspace/shots/<name>.png
C:/python13/python.exe D:/math-arena/tmp/rw_compare.py write D:/math-arena/deliverables/research-workspace/shots/<name>.png

# 黄金图测量复算（栏宽/边界/配色/字号）
C:/python13/python.exe D:/math-arena/tmp/golden_borders.py   # 1px 边框 → 面板边界
C:/python13/python.exe D:/math-arena/tmp/golden_panels.py    # 背景色分类 → 面板边界
```

**关键坑**：
1. **服务由"常驻后台任务"持有，会话结束即全部退出** —— 接手后第一件事就是跑 `detach_services.py` 把 54329/8300/8000/5176 全拉起来。
2. **`Start-Process` 与 WMI `Win32_Process.Create` 均被本平台安全策略拦截**（项目自带的 `scripts/dev-up.ps1` 用的是后者，不可用）。唯一可用机制是 bash 常驻后台任务。
3. **禁止多方各自重启服务**：曾因多路工程师各自"重启 8300"抢端口，造成窗口期全队 502。规则：服务单一持有者；需要加载后端新代码时由持有者统一重启。
4. **alembic 双库都要 head**：`research_vnext`（rv8300 schema）与 `math_arena`。落后会导致 8300 启动失败（缺 `research_project`）或登录 500（缺 `users.phone_verified_at`/`tasks`）。
5. **前端 `pnpm` symlink 坑**：本机 pnpm 12 的 symlink 静默失败；恢复工具 `D:/math-arena/tmp/rv13_relink.py`。vite 启动前先 `rm -rf node_modules/.vite`。
6. **跨用户隔离已验证**：另一账号访问演示项目 → 404（不泄露存在性）。未鉴权 → 401。
7. **PG 必须显式 `-p 54329`**；5176 可能落 Windows 保留端口段（5141-5240），绕行 5350。

---

## 第六章 · 团队状态与复用建议

上一会话的团队 `research-workspace-rebuild` 有 6 名成员，全部因 **429 额度耗尽**中断（重置 2026-09-17 01:27 UTC+8）。它们的 transcript 保存在 `C:\Users\<user>\.workbuddy\projects\d-math-arena\48162ace-...\subagents\agent-*.jsonl`。

| 成员 | 状态 | 建议 |
|---|---|---|
| software-architect | 待命（设计已交付 FRONTEND-ARCH.md） | 额度恢复后可用于**设计符合性审查**（对照 FRONTEND-ARCH/VISUAL-SPEC 核偏差） |
| software-engineer（后端） | **任务全部完成并经主理人独立验收** | 不需要复活；如需后端改动再指派 |
| fe-engineer-a | T01+T02 完成 | 待命；可用于 T02 单测补齐 |
| fe-engineer-read | T03 进行中（页面已接线，功能链未举证） | **优先复活**：继续 B 区举证 |
| fe-engineer-write | T04 进行中（组件齐，未集成验收） | **优先复活**：继续 P0-β |
| fe-engineer-agent | T05 进行中（组件齐，**未接线**） | **优先复活**：P0-α 是最大视觉缺口 |

> 若额度未恢复，主会话可直接自己实现 P0-α（Agent 面板接线只有几处小改动：Shell 两处占位替换 + token 宽度 + 单实例渲染），这是性价比最高的一步。

---

## 第七章 · 工作方式约定（用户明令）

- **自主迭代，不等用户放行**；战斗到最后一刻
- 工作流 = 主理人决策/分析/审核 + 子代理施工 + 主理人视觉打回；**编码与验收不同主体**
- 每个功能都要**真实体验链路截图**验收（1672×941 DPR=1）；禁止只说"代码看着对"
- 每轮结束输出 `STOP — 等待人工验收，不开始下一轮。`（人工按自己节奏推进）
- 秘密只从 env/.env 读；禁止出现在代码/日志/截图/commit
- 原子提交由人工执行；**禁止 `git add .`**
