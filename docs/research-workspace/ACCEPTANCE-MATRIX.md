# 科研端验收矩阵（ACCEPTANCE-MATRIX）

> 每个功能必须有**真实体验链路的截图 + 命令输出**双重证据。编码者与验收者必须是不同主体（交叉验证）。
> 证据落盘：`deliverables/research-workspace/<round>/`
> 视觉类证据：**viewport 1672×941、DPR=1** 的真实浏览器截图，命名 `feature-<name>.png`。

---

## A. 视觉还原（与 GOLDEN 1:1）

| # | 验收项 | 证据 | 判定 |
|---|---|---|---|
| A1 | READ 整页与 `golden/READ-GOLDEN.png` 并排 | `read-full.png` + 并排对比图 | 栏宽比例 20/44/36 ±2%；无可禁元素 |
| A2 | WRITE 整页与 `golden/WRITE-GOLDEN.png` 并排 | `write-full.png` + 并排对比图 | Agent 栏必须窄于 READ（≈27%）；Editor/Preview 并排 |
| A3 | Global Header 两种形态 | `header-read.png` / `header-write.png` | READ 中槽=项目胶囊；WRITE 中槽=编译控制条 |
| A4 | 左栏 READ 完整（项目卡/概览卡/导航/文献列表/最近笔记） | `read-left.png` | 五项俱全，层级与黄金一致 |
| A5 | 左栏 WRITE 完整（项目条/图标轨/文件树/论文结构） | `write-left.png` | 图标轨 64px；文件树真实多文件；Outline 带层级 |
| A6 | Research Agent 面板（两页） | `agent-read.png` / `agent-write.png` | 头/上下文 chips/Accordion/Composer 俱全；**无任何参数表单** |
| A7 | 展开态结果卡（推导校验）与 WRITE 错误解释卡 | `card-derivation.png` / `card-latexerror.png` | 状态药丸/关键信息块/证据来源/动作行/Diff 全部到位 |
| A8 | 编译日志栏 | `compile-log.png` | 四个 Tab + 计数药丸 + 彩色分级 + 时间戳等宽 |
| A9 | 主色与 token 一致 | 取色报告 | 主蓝 `#0A6CFE`；无字面色值（除 token 文件） |
| A10 | 无禁止元素 | 代码扫描 + 截图 | 无渐变/玻璃/大圆角/Emoji 装饰/Hero |

## B. READ 真实能力（宪法 §71）

| # | 验收项 | 证据 | 判定 |
|---|---|---|---|
| B1 | 建立科研项目 | `curl` 创建 + 前端刷新后仍在 | DB 有 `research_project` 行 |
| B2 | 导入 PDF 到项目 | 上传截图 + `GET /papers` 返回条目 | 元数据（标题/作者/年份）尽力而为；解析失败仍可读 |
| B3 | 打开 PDF 阅读 | `read-pdf.png` | 真实 PDF 渲染（非图片），可翻页 |
| B4 | 缩放 / 页码 / 适宽 | `read-zoom.png` | 数值真实变化 |
| B5 | 全文搜索 | `read-search.png` | 命中数真实 |
| B6 | 选中文本 → 浮动工具条 | `read-selection.png` | 仅选中时出现；`Esc` 关闭 |
| B7 | 点「问 AI」自动带上下文 | `read-ask-ai.png` + 网络请求截图 | 请求体含 paper/page/section/selected_text；**用户未复制原文** |
| B8 | 获得证据并可「定位原文」 | `read-evidence.png` → 点击后 `read-locate.png` | 阅读器跳回真实页码/位置 |
| B9 | 形成笔记并回跳 | `note-created.png` → 点击 `note-reopen.png` | 回到 paper/page/selection |
| B10 | 切换 WRITE 后仍是同项目、上下文不丢 | `switch-write.png` | 项目名一致；Agent 上下文保留 |

## C. WRITE 真实能力

| # | 验收项 | 证据 | 判定 |
|---|---|---|---|
| C1 | 多文件项目树可增删改移 | `files-crud.png` + `GET /files` | 不再只有 main.tex |
| C2 | 编辑 LaTeX（高亮/行号/撤销/搜索） | `editor.png` | 非 textarea；CodeMirror 级 |
| C3 | 自动保存与保存态 | `save-states.png` | 显示 未保存/保存中/已保存 |
| C4 | Outline 点击定位 | `outline-nav.png` | Editor 跳到对应行 |
| C5 | **真实编译** | `compile-run.png` + `POST /compile` 200 + 产物 id | 产出真实 PDF（非模拟图），用时与页数真实 |
| C6 | Preview 显示真实编译产物 | `preview.png` | Editor↔Preview 常驻并排 |
| C7 | **制造编译错误**→日志定位 | `compile-error.png` | 错误分级；点击错误 Editor 跳到 file+line |
| C8 | 编译失败 Preview 不清空 | `compile-fail-preview.png` | 仍显示上次成功产物 |
| C9 | Agent 解释编译错误 + 生成 diff | `agent-latex-error.png` | 错误类型/文件/行号/原因/建议/diff 全有 |
| C10 | 应用修复 → 重新编译成功 | `apply-fix.png` → `recompile-ok.png` | 确认后写入；重编译成功 |
| C11 | 引用补全 → 插入 `\cite{}` + 更新 bib | `citation-insert.png` + `refs.bib` 内容 | 复用已有 key，**不重复造条目** |
| C12 | 语言润色/翻译保护 LaTeX | `polish.png` | `\cite{}`/`\ref{}`/`\label{}`/math 未被破坏 |

## D. Research Agent 与证据

| # | 验收项 | 证据 | 判定 |
|---|---|---|---|
| D1 | 普通对话（不点能力，直接说话） | `agent-chat.png` | 真实 LLM 回答 |
| D2 | 上下文自动装载（非手选 Filter） | `agent-context.png` | chips 与实际阅读/编辑位置一致 |
| D3 | 点能力**不弹表单**，直接跑 | `agent-preset-run.png` | 出现 activity 状态→结果卡 |
| D4 | 任务状态可见且不神秘消失 | `agent-task-states.png` | queued/running/completed/failed/cancelled |
| D5 | 置信状态用中文（已验证/基本成立/需补充条件/证据不足/存在冲突） | `agent-status.png` | 无绝对化措辞 |
| D6 | 引用不伪造 | `citation-unverified.png` | 无法核验时明确标「未验证引用」 |
| D7 | Evidence 可回溯（Paper/Page/Section/位置） | `evidence-detail.png` | 点击可达来源 |
| D8 | 修改论文必须确认 | `diff-confirm.png` | 有接受/拒绝；无静默写入 |

## E. 可靠性 / 安全 / 持久化

| # | 验收项 | 证据 | 判定 |
|---|---|---|---|
| E1 | AI 不可用时人话降级 | `ai-down.png` | 如「AI 服务暂时不可用。你的论文已保存。[重新尝试]」；**无** traceback/JSON |
| E2 | 分区域 loading（无整页 spinner） | `loading-pdf.png` / `loading-agent.png` | 各区域独立 |
| E3 | Empty state 齐全 | `empty-*.png` | 无项目/无文献/无笔记/无草稿 均给出下一步 |
| E4 | 刷新/重登不丢数据 | `reload-persist.png` | 项目/文献/草稿/笔记/会话仍在 |
| E5 | 状态恢复 | `resume-state.png` | 回到上次 READ/WRITE、页码、tex 文件 |
| E6 | 项目隔离（跨用户不可读） | 两个账号 curl 互访 → 403/404 | **不泄露他人论文** |
| E7 | 密钥不泄露 | 全仓扫描 + 截图检查 | 无 key/token/presigned URL 出现在 UI 或日志 |
| E8 | 一键链路健康 | `health.json` | 54329/6380/9002/8000/8300 全绿 |

## F. 工程门禁

| # | 验收项 | 命令 |
|---|---|---|
| F1 | 前端类型检查 | `pnpm typecheck` |
| F2 | 前端构建 | `pnpm build` |
| F3 | 前端单测 | `pnpm test` |
| F4 | 后端单测（8300） | `uv run pytest tests/unit -q` 或 venv 等价命令 |
| F5 | 反模式扫描 | `test/platform/p01AntiPatternScan.test.ts` 不退化 |
| F6 | 无 key 提交 | 提交前 masked secret 扫描 |

---

## 判定规则

- **A 区任一项不过 → 直接打回**（视觉是用户第一诉求）。
- **B/C 区任一项无真实证据 → 判为未完成**（不得用"代码看着对"替代）。
- 出现 §7 明令禁止项 → 打回并记录。
- 每轮验收输出：通过项 / 打回项（附截图路径与差异描述）/ 下轮修复清单。
