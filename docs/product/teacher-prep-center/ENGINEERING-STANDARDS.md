# 备课中心 ENGINEERING-STANDARDS（主控自用工程准则）

> 这是主控（裁决者）对实现团队（子代理）的执行准则。冲突时服从产品 Contract 与 VISUAL-SPEC。

## A. 前端视觉准则

1. **命名空间**：新代码只允许在 `src/features/teacher-prep-center/`（D:\frontend）；路由经 `route/registerPrepCenter.ts` 加法式注册；禁止改科研端/学生端/PPT Studio 域；`pages/teacher-v3/prep/` 旧链路只读不写。
2. **Token**：本域自持 `tokens/prep.css`（`--pc-*` 前缀）；禁止复用 `--ma-*`/`--re-*`/`--tv3-*` 旧 token。
3. **数学渲染**：一切数学内容走 KaTeX（复用 `components/LatexText.vue`、`utils/latex.js`）；参数化图形必须由 MathObject 参数驱动实时生成 SVG/Canvas，数据→图形→公式同源。
4. **组件边界**：单文件 ≤600 行；PreparationShell / ClassroomDesign / DeckEditor / LessonPlanEditor / AiActionBar 职责单一；API 层独立 `api/prepCenterApi.ts`（Bearer + 信封解包 + AbortSignal，直连 `/api/teacher-v3/*` 经 5176 代理）。
5. **状态**：pinia store 单一会话状态源（session/blueprint/deck/plan/conversation）；SSE 事件驱动更新；乐观更新必须带失败回滚。
6. **反空壳自检（提交前逐条）**：每个按钮回答 Trigger/State/Result；每个数字回答数据来源；未实现能力不渲染。
7. **验证门**：`pnpm typecheck` + 相关 vitest 通过；UI 证据 = 1672×941 DPR1 真实截图（实现方截图，主控复审）；console 无新增 error。

## B. 后端内核准则

1. **单一真相源**：Teaching Blueprint（tp_blueprints）→ 确定性投影出 lesson plan spec + deck spec；禁止 PPT/教案各自重新 LLM 生成；AI 修改一律走 spec patch（局部、可回滚、记录 revision）。
2. **用例注册红线**：任何 `use_case` 调用必须存在于 `app/llm/config.py::USE_CASE_ROUTES`（已知缺陷 `lesson_plan_regen` 未注册，R6 修复并用单测锁定）。
3. **渐进生成**：蓝图 propose 用 LLM（流式 SSE）；投影无 LLM（快路径）；导出走 taskiq。教师可见的每一步进度必须映射真实任务状态（queued/running/completed/failed）。
4. **教师编辑优先**：块级编辑记录 edit origin；AI regen 不得覆盖教师手工块（走 confirm/diff 流程）。
5. **教材事实**：教材引用必须来自 textbook_v2 检索（SourceLocator：book/section/page/chunk_id→产品层只显示 书/章节/页/原文）；禁止编造页码。
6. **例题来源**：教材例题（textbook_problems）/题库（quiz_questions）/AI 生成三源分标签；禁止 AI 生成冒充教材例题。
7. **观测**：LLM 调用落 ai_runs（耗时/用量）；失败降级路径（超时→确定性兜底）必须留痕；教师界面只见产品化错误文案。
8. **验证门**：`uv run pytest tests/unit/teacher_prep tests/unit/ppt_v2 tests/unit/textbook_v2 -q` 全绿；边界守卫测试不放松。

## C. 团队工作模式（每轮）

1. 主控下发任务书：范围（文件白名单）+ 验收行（来自 ACCEPTANCE-MATRIX）+ 禁改清单。
2. 子代理实现 + 自跑真实链路截图 + 自检清单交付。
3. 主控逐图裁决（对照黄金图/VISUAL-SPEC），FAIL 打回时给出区域级偏差描述。
4. 原子提交：`git add -- <明确路径>`；提交信息 `feat(prepc): ...`；前做 masked secret 扫描（密钥/token/presigned URL 不得入库与截图）。
5. 仓库红线：D:\math-arena 与 D:\frontend 均有他人 dirty 文件——只动白名单文件，遇到目标文件被他人改动立即 STOP 上报。
