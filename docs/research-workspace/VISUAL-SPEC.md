# READ / WRITE 视觉还原规格（VISUAL-SPEC）

> 本文件是科研端**前端视觉的唯一可执行验收标准**。
> 上游真源：`RESEARCH_PRODUCT_CONTRACT.md`（产品行为）+ `golden/READ-GOLDEN.png` / `golden/WRITE-GOLDEN.png`（视觉，1672×941）。
> 数值由像素分析从黄金图提取（脚本：`tmp/golden_analyze.py` / `golden_bands.py` / `golden_palette.py` / `golden_type.py`）。
> **视觉层冲突时，黄金图 > 本文件 > 其它文档。**

---

## 0. 验收方法（硬性）

1. 以 **viewport 1672×941、devicePixelRatio=1** 真实浏览器截图，与对应 GOLDEN 并排对比。
2. 必须逐区域核对：`Global Header` / 栏宽比例 / 工具条高度 / 密度 / 字号 / 圆角 / 边框 / 主蓝 / 层级。
3. 允许的差异：示例文本内容（论文名、作者、页码可不同）、字体族（本机无黄金字体，取最接近的系统族）。
4. **不允许的差异**：栏宽比例偏差 > 2%、字号偏差 > 1px、主蓝色相偏离、出现渐变/玻璃拟态/大圆角/大卡片/装饰插画、右栏退化成表单。
5. 组件状态必须覆盖（不得只截默认态）：见 §7。

---

## 1. 布局网格（1672×941）

### 1.1 Global Header

| 项 | 值 |
|---|---|
| 高度 | **56px**（含 1px 底边框） |
| 背景 | `#FEFEFE` |
| 底边框 | 1px `#E8EEF5` |
| 左 | Logo（蓝色方块簇图标 24px）+ `科研工作台` + 1px 竖分隔 + 标语 |
| 右 | READ：`☁ 已同步` `今天 10:24` ｜ `🕘 历史` ｜ `🔔`(红点) ｜ 头像 + 姓名 + chevron |
| 中 | 见 1.2 |

> 禁止在科研端顶部出现「学生端/教师端/科研端」切换；禁止全局大搜索框（宪法 §5、§81）。

### 1.2 Header 中槽（两种工作区不同 —— 必须分别实现）

- **READ**：中槽 = 项目选择器胶囊
  - 尺寸 ≈ 300×34px，radius 8px，bg `#F0F7FE`，border 1px `#DCEBFA`
  - 内容：项目缩略图 24×24（radius 4，渐变/封面图）+ 项目名 14px/600 `#101828` + chevron-down
- **WRITE**：中槽右段 = 编译控制条（同一行，右对齐）
  - `XeLaTeX ⌄`（白底描边下拉，h28，radius 6）
  - `自动编译 ⌄`（同上）
  - `⟳ 重新编译`（**主蓝实心**，h28，radius 6）
  - `✅ 编译成功 10:24`（绿字 + 图标；失败态见 §5）
  - `⋯`（描边方形按钮 28×28）

### 1.3 工作区条（Header 之下，h ≈ 44px）

- **READ**：左侧 `阅读 | 写作` 分段控件 + 右侧 PDF 工具条（同一条）
- **WRITE**：左侧 `阅读 | 写作` 段控件 + 右侧（空，编译已在 header）

**READ/WRITE 分段控件（两页共用同一组件）**
- 容器：h 30px，radius 8px，bg `#E8F1FE`，内边距 2px
- 选中项：**主蓝实心** `#0A6CFE`，白字 14px/600，radius 6，h26，左右 padding 18px
- 未选中：透明底，字 14px/500 `#5A6F93`
- 切换必须保持同一 Project 与 Agent 上下文（宪法 §6）

### 1.4 READ 三栏（宽度 px，总和 1672）

| 栏 | 宽度 | 占比 | 背景 |
|---|---|---|---|
| 左 Project Sidebar | **323** | 19.3% | `#F5FAFE`，右边框 1px `#E8EEF5` |
| 中 PDF Reader | **742** | 44.4% | 阅读区底 `#F1F8FD`，纸张 `#FFFFFF` |
| 右 Research Agent | **607** | 36.3% | `#FFFFFF`，左边框 1px `#E8EEF5` |

> 比例必须落在 20/44/36 ±2%。PDF 阅读空间不得被挤压（宪法 §7）。
> Agent 面板可折叠；折叠后中栏自然扩展。

### 1.5 WRITE 四栏 + 底部日志

| 区域 | 宽度/高度 | 背景 |
|---|---|---|
| 左 Sidebar（含 64px 图标轨） | **306** | `#F4F9FD` |
| 中左 LaTeX Editor | **590** | `#FFFFFF` |
| 中右 PDF Preview | **462** | 底 `#F1F8FD`，纸张 `#FFFFFF` |
| 右 Research Agent | **314**（18.8%，**必须显著窄于 READ 的 607**） | `#FFFFFF` |
| 底部 Compile Log | 横跨 Editor+Preview 宽，**默认展开高 ≈ 150px**，可收起 | `#FFFFFF`，顶边框 1px |

> **关键**：WRITE 的 Agent 栏只有 READ 的 **约一半宽（314 vs 607）**。Agent 面板必须按此双宽度自适应：
> chips 换行、结果卡纵向堆叠、动作按钮可能折行、Diff 用等宽小字号、Composer 压缩。
> 这是 §4.4 的硬性约束，不是可选优化。

> ⚠️ **本节数值由像素测量校正（2026-09-16）**：早前一版误记为 355/435/428/454。
> 校正依据两条独立信号：① 背景色分类扫描 → READ 的 center|agent 边界落在 x=1065（⇒ agent 607）、WRITE 的 preview|agent 边界落在 x≈1352–1366（⇒ agent ≈306–320）；② 1px 边框检测 → 编辑器|预览 分割线落在 x=896（⇒ 306+590）。
> 复算脚本：`tmp/golden_borders.py`、`tmp/golden_panels.py`。**验收以本节为准。**

---

## 2. Design Tokens（`--rw-*` 命名空间，禁止复用 `--ma-*` / `--re-*`）

主蓝与学生端平台 token（`#2563EB`）**不同**，必须独立。

```
/* 品牌与主操作 —— 取自黄金图高饱和蓝像素众数 #006BFD / #0E6BFE */
--rw-primary:        #0A6CFE;
--rw-primary-hover:  #0B5CE0;
--rw-primary-soft:   #E5F2FE;   /* 选中行 / 导航选中 / Segmented 容器 */
--rw-primary-softer: #F0F7FE;   /* Agent 头部底 / 项目选择器胶囊 */

/* 表面 */
--rw-app-bg:      #F5FAFE;   /* 左栏 / 应用底色（蓝调中性色） */
--rw-app-bg-alt:  #F4F9FD;   /* WRITE 图标轨 */
--rw-surface:     #FFFFFF;   /* 卡片 / 面板 */
--rw-surface-2:   #F7FBFE;   /* 项目卡 / 概览卡 */
--rw-inset:       #F2F7FB;   /* 关键信息块 / 文件位置块（内嵌浅块） */
--rw-page-bg:     #F1F8FD;   /* PDF 阅读区底 */

/* 边框与分割 */
--rw-border:        #E8EEF5;
--rw-border-strong: #D8E3EF;

/* 文本 */
--rw-text:      #101828;   /* 主文本（近黑偏蓝，禁止纯 #000） */
--rw-text-2:    #5A6F93;   /* 次级（作者·年份·说明） */
--rw-text-3:    #8B9CB8;   /* 三级（时间戳·占位符） */

/* 语义 */
--rw-success:      #16A34A;  --rw-success-soft: #E7F7EE;
--rw-warning:      #B45309;  --rw-warning-soft: #FEF3E2;
--rw-danger:       #E5484D;  --rw-danger-soft:  #FDECEC;
--rw-pdf-badge:    #F5222D;  /* 文献列表 PDF 徽标 */

/* 几何 */
--rw-radius-sm: 4px;  --rw-radius: 6px;  --rw-radius-md: 8px;  --rw-radius-lg: 10px;
--rw-border-w: 1px;
```

**阴影**：极弱。卡片最多 `0 1px 2px rgba(16,24,40,.04)`；禁止大投影。

---

## 3. 字体与字阶

- CJK：`"PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif`
- Latin/数字：`"Inter","Segoe UI Variable","Segoe UI",sans-serif`
- LaTeX 编辑器 / 编译日志：`"JetBrains Mono","Cascadia Mono","Consolas",monospace`
- 数学：KaTeX（PDF 内由 PDF 自带字体）；**禁止把公式渲染成普通 Unicode 字符串**（宪法 §76）

| Token | size/weight | 用途 |
|---|---|---|
| `--rw-fs-h1` | 22 / 700 | header 产品名 `科研工作台` |
| `--rw-fs-h2` | 15 / 600 | 面板主标题（`Research Agent`、项目名） |
| `--rw-fs-h3` | 14 / 600 | 分区标题（`AI 项目概览`、`文献列表`、任务卡标题） |
| `--rw-fs-body` | 13 / 400 | 正文、文献条目、文件树、导航 |
| `--rw-fs-sm` | 12 / 400 | 元信息（作者·年份、时间戳、chip） |
| `--rw-fs-xs` | 11 / 400 | 微信息（徽标、副标题） |
| `--rw-fs-log` | 12 / 400 mono | 编译日志 |

行高：正文 1.6；标签 1.4。**禁止单词断行成两行**（用户硬规则）。

---

## 4. READ 组件规格

### 4.1 左栏 Project Sidebar

1. **项目身份卡**（h ≈ 62px，bg `#F7FBFE`，radius 8，padding 10）
   - 缩略图 44×44 radius 6（封面/渐变）
   - 项目名 15/600 `--rw-text`
   - 描述 12/400 `--rw-text-2`（如「基于变分方法的椭圆问题数值解」，单行省略）
2. **AI 项目概览卡**（bg `#F6FBFE`，border 1px，radius 8，padding 12）
   - 头：✦ 蓝色图标 + `AI 项目概览` 14/600 `--rw-primary` + 右侧 `›`
   - 正文：2 行 12/400 `--rw-text-2`（**Project Memory Summary**，来自服务端，非固定字符串 —— 宪法 §8/§70）
   - 统计条：4 列，列间 1px 竖分割线；数字 15/700 `--rw-text`，标签 11/400 `--rw-text-3`
     - 标签顺序与文案：`文献` `草稿` `笔记` `数据`（**以黄金图为准**；数值必须是真实计数）
3. **一级导航**（4 项，每项 h 40px，radius 6，图标 18px + 标签 14/500）
   - `文献`（READ 默认选中）/ `笔记` / `项目` / `设置`
   - 选中：bg `--rw-primary-soft`，文字与图标 `--rw-primary`
   - 未选中：文字 `--rw-text-2`
4. **文献列表**（分区）
   - 分区头：`⌄` + `文献列表` 13/600 + 右侧 `＋` 与 `🔍`（h 24 图标按钮）
   - 条目（h ≈ 44px，radius 6，padding 6 8）：
     - 左：PDF 徽标 26×26（bg `#F5222D`，白字 `PDF` 9/700，radius 4）
     - 上：标题 13/500，**当前打开= `--rw-primary`，其它= `--rw-text`**，单行省略
     - 下：`作者 · 年份` 12/400 `--rw-text-2`
   - 当前打开：bg `--rw-primary-soft`
   - 真实能力：导入 PDF / 打开 / 切换 / 删除 / 元信息（宪法 §10）
5. **最近笔记**（分区，左侧底部）
   - 分区头：`最近笔记` 13/600 + 右侧 `更多 ›`
   - 条目（h 30px）：📄 图标 + 标题 12/400 `--rw-text` + 右对齐时间 11/400 `--rw-text-3`
   - 点击必须能**回到 paper/page/selection 定位**（宪法 §11）

### 4.2 中栏 PDF Reader

- **工具条**（h 40px，白底，底边框）：`‹ › | [8] / 24 | − 140% + |` 选择工具、高亮、笔记、复制、搜索、全屏 等图标，均为 28×28 图标按钮
  - 紧凑；**禁止占据大量纵向空间的大按钮**（宪法 §12）
- **文档 Tab 条**（h 36px）：多个文献 Tab（`📄 The Finite Element Method… ×`，激活页浅蓝底）+ `＋`
- 纸张居中，两侧留 `#F1F8FD` 底；支持分页/缩放/页码/适宽/选文/复制/全文搜索/滚动

### 4.3 选区浮动工具条（宪法 §13）

- 仅在**选中文本时**出现于选区上方，**非常驻**
- h 32px，白底，radius 8，1px border，弱阴影
- 按钮（图标 + 文字 12px）：`高亮` `添加笔记` `引用` `问 AI`（`问 AI` 为**主蓝实心**）
- `Esc` 关闭
- 点击 `问 AI` → Agent 自动装载 paper/page/section/selected text，**用户无需复制原文**

### 4.4 右栏 Research Agent（两页共用组件，宽度不同）

自上而下：

1. **面板头**（h ≈ 64px，bg `--rw-primary-softer`）
   - 圆形蓝色 Logo 36px
   - `Research Agent` 15/700 `--rw-text` + `科研助理智能体` 15/700 `--rw-primary` + `● 在线` 12/400 `--rw-success`
   - 副标题 11/400 `--rw-text-3`：READ=`基于文献、代码与数学工具的专业研究助手`；WRITE=`你的学术写作助手`
   - 右侧 `⋯` 描边方形按钮 28×28
2. **当前上下文**（宪法 §26 —— 系统自动装载，**不是用户选的 Filter**）
   - 头：🔒/▣ 图标 + `当前上下文` 12/600 `--rw-primary` + `（基于选定文献篇）` 11/400 `--rw-text-3`
   - chips 行（可换行）：每 chip h 26px，radius 6，padding 0 8，bg `--rw-primary-soft`，字 12/500 `--rw-primary`，左侧小图标
     - READ 示例：`＋ 当前选定 §2.3` ｜ `Céa 1971` ｜ `main.tex L89` ｜ `项目知识库`
     - WRITE 示例：`main.tex L28` ｜ `当前 Section` ｜ `refs.bib` ｜ `相关论文`
3. **能力 Accordion**（宪法 §29）
   - 每个能力 = 一张卡（白底，1px border，radius 10，**收起时 h 46px**）
   - 收起态：左侧 28×28 圆角图标（各家不同色）+ 标题 15/600 + 右侧 `⌄`
   - **展开态**：当前执行/刚完成的能力展开显示结果，**其他全部收起**；禁止同时铺开 6 张大卡
   - READ 能力（顺序固定）：`推导校验` `引用补全` `LaTeX 对照` `公式检查` `翻译与改写`
   - WRITE 能力（顺序固定）：`LaTeX 错误解释` `改进建议` `引用补全` `公式检查` `语言润色` `翻译成英文`
   - **点击后不得弹出参数表单**（宪法 §30）；直接显示 `正在检查当前公式……` 之类的 activity 状态
4. **结果卡内容结构**（展开态；统一 Result Design System，宪法 §58/§59）
   - **状态行**：左 20px 圆形状态图标（绿✓/黄!/红!/蓝i）+ 能力名 15/700；右侧状态药丸（h 24，radius 12，bg `--rw-success-soft`，字 12/600 `--rw-success`）
     - 中文状态词：`已验证` `证据充分` `基本成立` `需要补充条件` `证据不足` `存在冲突`（宪法 §35）
   - **正文**：13/400 `--rw-text`，行高 1.7
   - **关键信息块**（bg `--rw-inset`，radius 8，padding 10）：标题 `关键信息` 13/600 + 有序列表 13/400（数学用 KaTeX 行内渲染）
   - **证据来源行**：`证据来源` 12/400 `--rw-text-3` + PDF 徽标 + 链接 13/500 `--rw-primary` + 右侧 `p. 267` 12/400 `--rw-text-3`
   - **动作行**：`定位原文`(描边) `查看证据`(描边) `加入笔记`(主蓝实心，带图标) —— h 32，radius 6，字 13/500
   - **WRITE 的 LaTeX 错误解释卡**（额外）
     - 错误标题行：红圈 `!` + `未定义的引用标识符` 15/700 `--rw-danger`
     - 解释正文 13/400
     - `文件位置` 块（bg `--rw-inset`）：`main.tex · 第 28 行`，行号部分 `--rw-primary`
     - `建议修改` 块：**Diff**
       - 删除行：bg `--rw-danger-soft`，字 `--rw-danger`，被改片段加深底
       - 新增行：bg `--rw-success-soft`，字 `--rw-success`，被改片段加深底
       - 等宽字体 12px
     - 补充建议 13/400
     - 动作行：`定位到第 28 行`(主蓝实心) `应用修复`(描边) `加入笔记`(描边)
   - **所有会修改论文的动作必须用户确认**，禁止静默写入（宪法 §32/§40）
5. **Composer**（始终存在，宪法 §31）
   - 白卡，1px border，radius 10
   - textarea 占位：READ=`直接告诉我你想完成什么：解释推导、找证据、写入笔记、生成引用…`；WRITE=`有什么可以帮你的吗？`
   - 底部行：左 `📎` `@` `📖` 图标按钮（灰）；右 **主蓝圆形发送按钮** 32px（纸飞机图标）

---

## 5. WRITE 组件规格

### 5.1 左栏

- 顶部项目条（h ≈ 56px）：缩略图 + `有限元误差分析` 14/600 + `学术论文项目 · 进行中` 12/400 `--rw-text-3` + 右侧 `×`
- 其下分两列：
  - **图标轨 64px**（bg `--rw-app-bg-alt`）：`项目`(选中，蓝) / `文献` / `笔记` / `设置`，每项图标 20px + 标签 11px，纵向排列
  - **内容列**：
    - Tab 行：`文件树 | 论文结构`（选中项浅蓝底 `--rw-primary-soft`，字 13/600 `--rw-primary`）+ 右侧 2 个图标按钮
    - **文件树**：缩进层级，文件夹 `▸/▾` + 图标 16px + 名称 13/400；文件按类型着色（.tex 蓝、.bib 蓝、图片/PDF 各自图标）；当前文件选中浅蓝底
      - 必须支持真实多文件：`main.tex` / `sections/{introduction,method,theory,experiment,conclusion}.tex` / `figures/*` / `tables/` / `refs.bib` / `appendix.tex` / `notation.tex` / `style.cls`
      - 可创建/重命名/删除/移动（宪法 §17）；**禁止伪造"永远只有 main.tex"**
    - **论文结构 Outline**：由 LaTeX 结构实时生成，层级缩进；点击 **Editor 自动定位**（宪法 §18，非静态目录）
      - 形如：`摘要` `1 引言`（`1.1 研究背景` `1.2 相关工作` `1.3 本文贡献`）`2 预备知识` `3 有限元误差估计` `4 数值实验` `5 结论` `参考文献` `附录`

### 5.2 LaTeX Editor

- 文件 Tab 条（h 36px）：`main.tex ×`（激活浅蓝）+ 其它已打开文件 + `＋`
  - 保存态必须显式可见：`未保存 / 保存中 / 已保存`（宪法 §20/§51）
- 格式工具条（h 36px）：`↺ ↻ ｜ B I {} π ⌐ ｜ 🔗 🖼 ⊞ ｜ 🔍 ｜ 行号 [开关] ｜ ⇤`；右侧页码分页控件
- 编辑器：**行号栏** + 语法高亮（命令/环境/注释/math 分色）+ 当前行高亮 + 自动保存
- **禁止使用裸 textarea**（宪法 §19/§70）
- `行号` 开关为**主蓝实心** toggle（开态）

### 5.3 PDF Preview

- 工具条（h 36px）：`‹ [1] / 24 › ｜ − 100% + ｜ ⛶ ⛶ ↗`
- 纸张白底居中，两侧 `#F1F8FD`
- 编译成功 → 自动更新；**编译失败 → 保留上次成功产物**，不得清空（宪法 §23）

### 5.4 Compile Log（底部）

- Tab 头：`编译日志 [3]`（选中，蓝下划线 2px）+ `错误 [0]` + `警告 [2]`(橙药丸) + `信息 [1]`(蓝药丸) + 右侧 `清空日志`
  - 药丸：h 18px，radius 9，字 11/600，bg 语义 soft 色
- 日志行：等宽 12px，`时间戳` `--rw-text-3` + 消息正文
  - warning 用 `--rw-warning`；error 用 `--rw-danger`；成功 `✅ 编译完成，用时 2.3 秒。` 用 `--rw-success`
- **点击某条错误 → Editor 定位到 file + line**（宪法 §24）
- 默认紧凑，正常时可自动收起

---

## 6. 状态与容错（宪法 §56/§60/§61）

- **禁止整页 spinner**；各区域独立 loading：PDF → skeleton；Agent → Task 状态条；文献列表 → 列表 skeleton；编译 → 工具条状态
- **失败文案必须是人类语言**：`AI 服务暂时不可用。你的论文已保存。 [重新尝试]`
  - **禁止**出现 `WS_LLM_TIMEOUT` / `500 traceback` / 裸 JSON 错误
- **Empty State** 必须给出下一步：无项目 / 无文献 / 无笔记 / 无草稿 / 无引用 / 无对话
- 统一错误对象：`{ code, status, message, taskId?, retryable? }`（宪法 §87）

### 6.1 WRITE 状态必须覆盖
`默认` `editing` `saving` `compile running` `compile success` `compile warning` `compile failure` `Agent diff` `citation insertion`

### 6.2 READ 状态必须覆盖
`默认` `选择文本` `Agent running` `Agent completed` `Citation result` `Formula warning` `Empty project`

---

## 7. 明令禁止（违反即打回）

1. 大渐变 / 玻璃拟态 / 大圆角（>10px 的通用容器）/ 巨大卡片 / 装饰插画 / 品牌 Hero / 大量 Emoji / 卡通 AI 机器人
2. 彩色 Dashboard 图表式首页
3. **右侧边栏任何形态的参数表单**（输入框 + 模型选择 + top_k + 运行按钮）
4. PDF 只是图片；LaTeX Editor 是 textarea；编译按钮只改状态文字
5. 历史按钮没有历史；通知是假红点；项目选择器永远只有一个项目；AI 项目概览是固定字符串
6. 向用户暴露 `RAG` / `embedding` / `top-k` / `tool call` / `chunk` / `vector DB` / model ID / temperature（宪法 §27/§85）
7. 一级 Workspace 出现「审查」；把 翻译/润色/引用/公式 变成一级页面（宪法 §3）
8. 新增宪法 §77 之外的领域（会议管理 / 经费 / 课题申报 / 绩效 / Notebook / 模型市场）
9. 组件用字面色值（必须 `var(--rw-*)`）
10. 纯 `#000` / 纯 `#fff`（用 `--rw-text` / `--rw-surface`）

---

## 8. 黄金图局部放大参考（自查用）

`tmp/golden-out/` 下已切好 2× 放大图，工程与验收均可直接对照：

| 文件 | 内容 |
|---|---|
| `read_crop_header.png` | READ 顶栏全宽 |
| `read_crop_left.png` | READ 左栏（项目卡/概览卡/导航/文献列表/最近笔记） |
| `read_crop_right.png` | READ Research Agent 全高 |
| `write_crop_header.png` | WRITE 顶栏 |
| `write_crop_left.png` | WRITE 左栏（项目条/图标轨/文件树/论文结构） |
| `write_crop_center_top.png` | WRITE 阅读写作 Tab + 文件 Tab + 格式条 |
| `write_crop_right.png` | WRITE Agent + LaTeX 错误解释卡 + Diff + 预览工具条 |
| `write_crop_bottom.png` | WRITE 编译日志 |
