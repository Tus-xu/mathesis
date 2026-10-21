# 前端（智学树妍 v4 / Vue 3）

本目录规则在父级（如有）AGENTS.md 之上生效，近目录规则优先。根目录没有仓库级 AGENTS.md，本文件即权威前端规则。

## 技术栈与命令

- Vue 3、Vite、TypeScript、Pinia、Vue Router、Naive UI、Vitest、Playwright；数学可视化依赖 KaTeX、MathLive、ECharts、Three.js、JSXGraph
- 包管理器：pnpm（以锁文件为准），仅 `npm`/`pnpm` 二选一，禁止混用
- 关键命令：
  - `pnpm dev` 启动开发服务器
  - `pnpm build` 生产构建（vite build）
  - `pnpm typecheck` TypeScript 类型检查（vue-tsc --noEmit）
  - `pnpm test` Vitest 单元测试
  - `pnpm e2e` Playwright 端到端测试（需 mock 时用 `pnpm e2e:mock`）
- 测试文件约定：`test/**/*.test.ts`、`test/**/*.spec.ts`（以 vitest.config 实际 include 为准）
- 模拟身份只在 `VITE_USE_MOCK=1` 时允许，禁止默认开启

## 核心规则

1. **先调查后修改**：修改文件前必须先读完整文件；组件单体过大时禁止盲目重构（见下方 Frozen 域）
2. **最小 diff + 不得覆盖 dirty**：`teacher-v3` 现有 dirty 页面不得被格式化、重排、覆盖；发现目标文件已有不属于本轮的改动，立即 STOP 报告
3. **禁止顺手重构**：一个提交一个主题；不修无关 lint、不改无关命名、不"顺手"格式化整个文件
4. **秘密脱敏**：API key、token 只从环境变量读取；不得 commit `.env`；不得在控制台/截图/commit 中回显真实密钥或 presigned URL
5. **验证门**：任何 UI/行为改动必须通过 `pnpm typecheck` + 相关 `pnpm test`；涉及构建必须 `pnpm build` 通过
6. **浏览器证据**：涉及 UI 必须以 teacher 身份真实浏览器走完整路径（至少 1440×900），交付截图 + console/network 无新增 error；photo/模板预览不得仅用色块

## LEGACY/FROZEN 域

以下文件/结构为 LEGACY/FROZEN，只修复阻断性问题，禁止重构、格式化、改写接口：

- `src/pages/teacher-v3/SlidesView.vue`
- `src/components/teacherV3/SlideCanvasV3.vue`
- `src/types/teacherV3.ts` 的 V3 deck/slide/element 结构
- `src/api/teacherV3.ts` 现有 PPT/Plan 方法
- 旧模板颜色卡 fallback、旧生成 wizard、旧导出交互

## PPT Studio V2 规则

- 新功能只允许在 `src/features/ppt-studio-v2/` 独立命名空间；禁止在该目录外零散堆叠 V2 代码
- `src/features/ppt-studio-v2/**` 禁止 import `SlidesView.vue`（由边界测试守卫）
- EditorAdapter 只能通过 bridge 映射到旧编辑器能力；V2 编辑回写必须是 TeachingDeckSpec 的显式 patch，禁止把 V3 deck JSON 反客为主
- V2 展示层不得展示 presigned URL、模型内部响应、任何 secret

## 科研端（Research Workspace）

**科研端产品行为与视觉必须遵循 `docs/research-workspace/RESEARCH_PRODUCT_CONTRACT.md`（最高产品规范）。**

- 视觉验收标准：`docs/research-workspace/VISUAL-SPEC.md`；唯一视觉真相源：同目录 `golden/READ-GOLDEN.png` / `golden/WRITE-GOLDEN.png`（1672×941）
- 新代码只允许在 `src/features/research-workspace/` 命名空间；禁止复用 `--ma-*` / `--re-*` 旧 token（主色与黄金图不符）
- 科研端请求必须经 8000 网关（`/api/research-vnext/*` → 8000 → 8300）；**禁止**直连 8300、禁止自造 `X-Research-Access-*` 头
- 验收须给出 **1672×941、DPR=1** 真实浏览器截图，并逐区域对照黄金图

## 教师端备课中心（V2 重构轮）

- 遵循 `docs/product/teacher-prep-center/TEACHER_PREP_CENTER_PRODUCT_CONTRACT.md`（主契约）与 `docs/product/teacher-prep-center/VISUAL-SPEC-V2.md`（视觉/工程判据）。
- `docs/product/teacher-prep-center/golden/` 下三张 Golden（CLASSROOM-GOLDEN.png / PPT-GOLDEN.png / LESSONPLAN-GOLDEN.png）为备课链路视觉基线（判定口径 1440×900，见 `docs/product/teacher-prep-center/ACCEPTANCE-MATRIX.md`）。
- 既有内核（teacher_prep / ppt_v2 / 教案导出 / 旧可编辑 SlidesView）必须服务该体验：KEEP > ADAPT > REPLACE > REMOVE，禁止平行内核。

## STOP

每轮改动完成并验证后，输出 `STOP — 等待人工验收，不开始下一轮。`