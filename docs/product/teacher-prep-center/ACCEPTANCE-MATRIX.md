# 备课中心 ACCEPTANCE-MATRIX（逐功能验收与证据要求）

> 证据一律归档 `deliverables/teacher-prep-center/<round>/`。截图口径：1672×941、DPR=1、真实登录态、真实后端。
> 截图由实现方子代理以真实链路自动化截取；视觉裁决由主控（非实现者）逐图对照黄金图执行；不达标打回重做。
> 每行结论：PASS / FAIL+证据。FAIL 项必须修复后重验。

## R1 布局骨架与视觉系统

| # | 功能 | 验收标准 | 证据 |
|---|---|---|---|
| 1.1 | 会话工作台 Shell | 顶栏（品牌/课程上下文/三 Tab/资料/历史/教师）与黄金图区域级一致；无套娃 | R1/shell.png |
| 1.2 | 三 Tab 切换 | 点击真实切换 课堂设计/PPT/教案，URL 同步（/prep/session/:id/:tab） | R1/tabs.png ×3 |
| 1.3 | 视觉 token | 颜色/圆角/发丝线/阴影符合 VISUAL-SPEC §0 | R1/tokens-vs-golden.png |

## R2 备课首页 + 新建备课（自然语言入口）

| # | 功能 | 验收标准 | 证据 |
|---|---|---|---|
| 2.1 | 首页形态 | 最近备课（真实 session 列表：名称/教材/班级/时间/状态）+ 新建备课输入框 + 从哪里开始；无 KPI 大盘 | R2/home.png |
| 2.2 | NL 新建 | 输入「准备高二椭圆第一课时，45分钟，人教A版」→ 创建真实 session → 跳入工作台；解析结果（教材/章节/课时）真实入库 | R2/home-create.png、R2/session-after-create.png |
| 2.3 | 继续备课 | 点击最近备课卡片 → 回到同一会话、数据还原 | R2/home-reentry.png |
| 2.4 | AI 定位教材 | 新建后 AI 回复含教材依据（真实 textbook_v2 检索命中：章节+页码），点击可查看 | R2/locate-textbook.png |

## R3 课堂设计页（黄金图 1）

| # | 功能 | 验收标准 | 证据 |
|---|---|---|---|
| 3.1 | 教学流程时间轴 | 45 分钟总控；节点行 = 序号/名称/时长/摘要/chevron；布局对齐黄金图 | R3/classroom-timeline.png |
| 3.2 | 节点展开 | 展开卡蓝描边浅蓝底；教师提问/学生活动/板书要点（KaTeX）+ 教材依据卡（真实封面/章节/引文）+ 教学提示 | R3/classroom-expanded.png |
| 3.3 | 在线编辑 | 每组「编辑」真实落库（重进还原） | R3/classroom-edit.png、R3/classroom-edit-after-reload.png |
| 3.4 | AI 定向修改 | 底部条选中「正在修改: 03 椭圆的定义」→ 说「先让学生观察轨迹再归纳定义」→ AI SSE 回复 + 该节点真实更新 + 其它节点不动 | R3/ai-modify-before.png、R3/ai-modify-after.png |
| 3.5 | 时长约束 | 环节时长和 >45 分钟时系统提示；改时长显示剩余 | R3/time-budget.png |
| 3.6 | 教材查看 | 「查看教材」打开真实切片原文（章节+页码+LaTeX 原文） | R3/textbook-viewer.png |
| 3.7 | 资料与历史 | 「教材与资料 5」数字真实；「历史」进入真实历史版本列表 | R3/materials-drawer.png、R3/history-panel.png |

## R4 PPT 页（黄金图 2）

| # | 功能 | 验收标准 | 证据 |
|---|---|---|---|
| 4.1 | 缩略图栏 | 真实微缩渲染 + 环节名；选中态蓝描边；页码=真实页数 | R4/ppt-strip.png |
| 4.2 | 画布渲染 | 黄金图概念页要素全齐（页眉/标题徽标/定义带/参数化图形/公式胶囊/知识卡/页脚），KaTeX 正确 | R4/ppt-canvas.png |
| 4.3 | 参数化数学图形 | 右面板改 a=5,b=3→c=4 联动；改 a=6 → 图形/焦点/公式实时重算且数学正确 | R4/param-before.png、R4/param-after.png |
| 4.4 | 显隐开关 | 焦点/坐标轴/辅助线/数值标注开关真实作用于图形 | R4/toggles.png |
| 4.5 | AI 快捷操作 | 「生成相似练习题」「让本页更简洁」执行真实任务并落盘 | R4/ai-quick-ops.png |
| 4.6 | 页操作 | 添加页/删除页/排序真实持久化；页码与缩略图同步 | R4/page-ops.png |
| 4.7 | AI 单页修改 | 选中页 3 说「公式放大一点」→ 只改该页，其余页不动 | R4/ai-single-page.png |
| 4.8 | 保存状态 | 「✓已保存 HH:MM」来自真实持久化时间 | R4/save-state.png |

## R5 教案页（黄金图 3）

| # | 功能 | 验收标准 | 证据 |
|---|---|---|---|
| 5.1 | 文档结构 | 六章结构 + 元信息条 + 教学过程表格（KaTeX）+ 板书三栏；对齐黄金图 | R5/lessonplan-doc.png |
| 5.2 | 大纲导航 | 左侧大纲点击滚动定位 + 激活态双向联动 | R5/outline-nav.png |
| 5.3 | 富文本编辑 | 标题/列表/表格单元格/公式真实可编辑并保存（重进还原） | R5/lessonplan-edit.png、R5/lessonplan-edit-reload.png |
| 5.4 | 智能辅助 | 当前定位正确；「精简语言」等建议执行真实局部修改 | R5/assistant-panel.png |
| 5.5 | 导出 | 导出真实文件（PDF/Markdown 落 MinIO + 下载票据）；文件可打开、排版含 KaTeX 公式 | R5/export.png、exported 文件 |

## R6 生成链路速度与质量（内核）

| # | 功能 | 验收标准 | 证据 |
|---|---|---|---|
| 6.1 | 渐进生成 | 蓝图确认后：教学路径先出现；PPT/教案逐步出现（真实页级进度 n/N） | R6/progressive-1.png、progressive-2.png |
| 6.2 | 首响时间 | 发送后 AI 首反馈 ≤ 数秒（流式）；全程无长时间静止白屏 | R6/first-token.png |
| 6.3 | 内容推进 | 各页教学任务不同（不重复卡片填充）；版式随教学任务变化 | R6/deck-narrative.png ×N |
| 6.4 | 数学正确 | 椭圆 a/b/c/焦点/方程全链一致；抽查抛物线 | R6/math-check.png |
| 6.5 | 同步状态 | 课堂设计改动 → PPT/教案出现「未同步」提示；一键同步收口 | R6/sync-state.png |

## R7 端到端验收链路（宪法 §127 全链）

新建 → 定位教材 → 对话 → 教学目标 → 教学路径 → 确认 → 例题 → 数学图形 → 生成课件 → 编辑课件 → 生成教案 → 编辑教案 → 改路径 → 同步 → 保存 → 重进还原 → 导出可用。
证据：R7/full-chain-XX.png（按步骤编号）+ 后端 ai_runs 台账摘录（时延）+ 导出文件实体。

## R7+ 最终反幻觉验收（Real Artifact Inspection，终审门）

禁止以「页面显示出来 / PPTX 已生成 / API 200 / AI 返回 completed」判断成功。必须抽样做真实产物检查：

| # | 检查 | 通过标准 | 证据 |
|---|---|---|---|
| A1 | PPT 页面截图 | 每类版式至少 1 页真实渲染截图，无溢出/重叠/空白页 | R7/ppt-pages/*.png |
| A2 | PPT 元素结构 | deck spec 元素结构与渲染一致（抽查 3 页：文本/公式/图形元素数与内容） | R7/spec-vs-render.md |
| A3 | 真实导出 | 导出 PPTX 实体落盘（MinIO+下载），非截图伪装；文字可编辑（解包 XML 验证 `<a:t>` 文本存在） | R7/export/*.pptx + unzip 检查输出 |
| A4 | PowerPoint/WPS 打开 | 导出文件真实打开无修复弹窗（截图或 XML 结构校验记录） | R7/open-proof.png |
| A5 | 数学公式 | 抽查 5 处公式与教材/数学事实一致（LaTeX 源 + 渲染双查） | R7/math-formula-check.md |
| A6 | 数学图形 | 参数化对象：改参→图形/焦点/公式联动正确（a²=b²+c²） | R7/math-figure-check.png |
| A7 | 教学流程 | 流程时间合计 ≤45min、环节推进合理、与蓝图一致 | R7/flow-check.png |
| A8 | 教案内容 | 六章齐全、教学过程表与蓝图节点一致、无空章节 | R7/plan-check.png |
| A9 | PPT/教案一致性 | 同一知识点在两产物中数值/术语/时间一致（抽查 3 处） | R7/consistency-check.md |
| A10 | 教师修改再保存导出 | 教师编辑→保存→再导出→重进：修改保留且进入导出物 | R7/edit-persist-check.png |

## 最终红线（任一成立即不得宣布完成）

老师最终还得重新做一遍 PPT ｜ 还得重新写一遍教案 ｜ 数学图形只是图片 ｜ PPT 只是截图 ｜ 生成只是 mock ｜ 点击后只是 toast ｜ PPT 和教案互相不一致 ｜ 公式错误 ｜ 数学图形错误 ｜ 页面大量重复 ｜ 页面大量空白 ｜ 内容大量堆字 ｜ 生成后无法局部修改 ｜ AI 修改会破坏教师修改。

## 裁决流程

1. 实现方子代理跑真实链路截图 → 交付图组 + 自检清单。
2. 主控逐图对照黄金图与 VISUAL-SPEC 裁决：PASS / FAIL(具体区域+偏差)。
3. FAIL → 打回限定范围修复 → 重新截图 → 重审。编码者与裁决者不得为同一主体。
