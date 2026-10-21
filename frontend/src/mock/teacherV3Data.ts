/**
 * 教师工作台 V3 · Mock 数据（/api/teacher-v3/* 的响应种子）
 * 作用：前端原型数据源 + 后端智能体的契约样例（返回结构与 src/types/teacherV3.ts 严格一致）
 * 深链路验收：
 *  - 拍照 deck：原图锚定（anchorPhoto 元素）→ 识别块可编辑 → 长解答自动分页（anchor_bar）
 *  - 批改聚类：按题聚类视图数据完整（correct/partial/wrong/blank 四类 + 原图区域）
 */

/* ==================== 基础 ==================== */

export const V3_TEACHER = { name: '李文澜', subject: '高中数学', grade_group: '高二年级' }

export const V3_CLASSES = [
  { class_id: 'c2-03', name: '高二(3)班', students: 46 },
  { class_id: 'c2-05', name: '高二(5)班', students: 44 },
]

export const V3_TODAY: {
  teacher: typeof V3_TEACHER
  schedule: { time: string; class_name: string; topic: string; status: 'done' | 'next' | 'later'; missing?: string[] }[]
  todos: { id: string; time: string; text: string; kind: 'grade' | 'prep' | 'review' | 'meeting' }[]
  class_brief: { class_id: string; name: string; avg: number; submit_rate: number; trend: number[]; weak_kp: string }[]
} = {
  teacher: V3_TEACHER,
  schedule: [
    { time: '08:00', class_name: '高二(3)班', topic: '椭圆及其标准方程（第1课时）', status: 'done' },
    { time: '09:55', class_name: '高二(3)班', topic: '椭圆及其标准方程（第2课时）', status: 'next', missing: ['课件未最终确认'] },
    { time: '14:00', class_name: '高二(5)班', topic: '椭圆及其标准方程（第1课时）', status: 'later', missing: ['缺少 2 道分层变式题'] },
  ],
  todos: [
    { id: 't1', time: '10:40', text: '批改高二(3)班《导数单调性》作业（剩 9 份）', kind: 'grade' },
    { id: 't2', time: '11:30', text: '确认「椭圆标准方程」课件 V3 终稿', kind: 'prep' },
    { id: 't3', time: '16:00', text: '生成《导数单调性》讲评课件并上传', kind: 'review' },
    { id: 't4', time: '16:40', text: '备课组长会：期中考试命题分工', kind: 'meeting' },
  ],
  class_brief: [
    { class_id: 'c2-03', name: '高二(3)班', avg: 78.4, submit_rate: 0.96, trend: [72, 74, 73, 76, 78], weak_kp: '利用导数研究函数极值' },
    { class_id: 'c2-05', name: '高二(5)班', avg: 81.2, submit_rate: 0.91, trend: [77, 76, 79, 80, 81], weak_kp: '椭圆离心率与几何性质' },
  ],
}

export const V3_TASKS = [
  { task_id: 'task-photo-1', title: '拍照识别 →《圆锥曲线例题》课件', capability: 'recognition', status: 'running' as const, progress: 62, stage: '分页引擎：解答第 3 步换页' },
  { task_id: 'task-export-2', title: '导出《椭圆及其标准方程》PPTX', capability: 'generation', status: 'succeeded' as const, progress: 100, stage: '完成' },
]

/* ==================== 课件模板（生成前选择，自渲染样张） ==================== */

export const V3_DECK_TEMPLATES = [
  {
    id: 'tpl-academic-blue', name: '学术蓝·严谨版', style: 'academic' as const,
    swatch: { bg: '#0a3568', primary: '#0f4787', accent: '#c99735', light: true },
    page_kinds: ['cover', 'review', 'definition', 'derivation', 'example', 'summary'],
    recommended_for: '新授课·概念课', sample_topic: '椭圆及其标准方程',
  },
  {
    id: 'tpl-chalkboard', name: '黑板绿·手写感', style: 'chalkboard' as const,
    swatch: { bg: '#1e3a2f', primary: '#2d5546', accent: '#e8c56a', light: false },
    page_kinds: ['cover', 'derivation', 'example', 'solution-flow', 'keypoints'],
    recommended_for: '推导课·习题课', sample_topic: '导数与单调性',
  },
  {
    id: 'tpl-geometric', name: '几何灰·图纸感', style: 'geometric' as const,
    swatch: { bg: '#f4f4f2', primary: '#37474f', accent: '#e65100', light: true },
    page_kinds: ['cover', 'definition', 'variation', 'blank'],
    recommended_for: '立体几何·图形密集课', sample_topic: '空间中直线与平面',
  },
  {
    id: 'tpl-classic-navy', name: '经典藏青·正式', style: 'classic' as const,
    swatch: { bg: '#1a2340', primary: '#283593', accent: '#ffb300', light: true },
    page_kinds: ['cover', 'review', 'summary', 'solution-flow'],
    recommended_for: '公开课·示范课', sample_topic: '数列求和',
  },
  {
    id: 'tpl-minimal-white', name: '极简白·留白', style: 'minimal' as const,
    swatch: { bg: '#ffffff', primary: '#16233b', accent: '#0e9488', light: true },
    page_kinds: ['cover', 'definition', 'example', 'summary', 'blank'],
    recommended_for: '复习课·概念梳理', sample_topic: '圆锥曲线小结',
  },
  {
    id: 'tpl-warm-orange', name: '暖橙·活力', style: 'minimal' as const,
    swatch: { bg: '#ea580c', primary: '#ea580c', accent: '#f59e0b', light: true },
    page_kinds: ['cover', 'example', 'variation', 'summary'],
    recommended_for: '活动课·兴趣课', sample_topic: '数学建模入门',
  },
]

export const V3_LESSON_TEMPLATES = [
  {
    id: 'lt-explorer', name: '探究式（概念生成）', style_tag: '情境引入 → 学生活动 → 概念生成 → 变式应用',
    sections: ['情境引入', '动手实验', '概念生成', '性质探究', '例题变式', '课堂小结'],
    recommended_for: '新授课·椭圆/双曲线等概念课', sample_topic: '椭圆及其标准方程',
    source: 'builtin' as const,
  },
  {
    id: 'lt-lecture-drill', name: '讲练结合', style_tag: '复习铺垫 → 精讲 → 分层练习 → 纠错',
    sections: ['复习铺垫', '典型例题', '方法提炼', '分层练习', '纠错小结', '作业布置'],
    recommended_for: '习题课·方法课', sample_topic: '导数与函数单调性',
    source: 'builtin' as const,
  },
  {
    id: 'lt-review', name: '复习讲评', style_tag: '错因分布 → 典型错例 → 变式再练',
    sections: ['整体回顾', '错因分析', '典型错例', '变式再练', '方法归纳', '课后巩固'],
    recommended_for: '讲评课·单元复习', sample_topic: '期中试卷讲评',
    source: 'builtin' as const,
  },
  // V3.1「我的模板」：上传 2025 秋公开课教案提炼（个人知识库档，仅本人可见）
  {
    id: 'lt-mine-ellipse', name: '我的模板 · 探究详案（椭圆公开课）', style_tag: '三栏写法 · 例题密度高 · 素养目标逐条对应',
    sections: ['教材分析·学情', '教学目标（素养线）', '重难点与突破', '情境引入', '新知探究', '例题变式', '课堂检测', '分层作业'],
    recommended_for: '个人模板 · 提炼自 2025 秋《椭圆》公开课', sample_topic: '双曲线及其标准方程',
    source: 'teacher_upload' as const,
    quality_report: {
      file: '2025秋_椭圆标准方程_公开课教案.docx', score: 86,
      board_coverage: ['教材分析', '学情分析', '教学目标', '重难点', '教学过程三栏', '板书设计'],
      missing_boards: ['教后反思'], cliche_hits: [],
      recommended: true, suggestion: '栏目齐全、套话率低，适合作为个人模板',
    },
  },
]

/* ==================== 教案（P3：十板块重构） ==================== */

/**
 * 十板块框架（P3 定案）：高中新授课教案的规范板块骨架。
 * attachable=true 的板块支持「挂例题」（结构化题，可复用到试卷）。
 * 教案生成与编辑均以本框架为骨架；课型（习题/讲评）只裁剪内容，不替换骨架。
 */
// 十板块框架已迁出为共享常量（M3 审计：应用代码禁 import mock 文件）；此处再导出保持 mock server/测试引用不变
export { V3_TEN_BOARDS } from '../pages/teacher-v3/tenBoards'

export const V3_LESSON_PLANS = [
  {
    id: 'plan-ellipse', topic: '椭圆及其标准方程（第1课时）', class_id: 'c2-05', lesson_type: '新授课', template_id: 'lt-explorer',
    objectives: [
      '理解椭圆的定义，能说出定义中两个定值与一个距离关系',
      '掌握椭圆标准方程的推导过程，理解 $a>b>0$ 与 $a^2=c^2+b^2$ 的由来',
      '会根据条件求椭圆的标准方程',
    ],
    key_points: [
      '定义中「距离之和为定值 $2a$（$2a>2c$）」的必要性',
      '焦点在 $x$ 轴与 $y$ 轴时标准方程的判别',
    ],
    sections: [
      { id: 'bd-context', name: '课标与学情', minutes: 2, teacher_activity: '出示本节目标，结合本班学情提示核心是「把椭圆定义翻译成代数方程」，并点出多数学生的易错点：忽略 $2a>2c$。', student_activity: '明确本节课任务，回看上一节圆的标准方程笔记。', design_intent: '目标先行，把「学什么」与「可能错在哪」前置。' },
      { id: 'bd-objectives', name: '教学目标', minutes: 0, teacher_activity: '通过探究椭圆的定义与标准方程，激发学生学习数学的兴趣，培养学生分析问题与解决问题的能力。', student_activity: '体会知识的形成过程，增强学好数学的信心。', design_intent: '基于操作经验与代数推导，让学生自然获得椭圆的标准方程，并学会用方程研究曲线。', cliche: true, cliche_hits: ['空泛的「激发……兴趣」，未说清用什么激', '口号式能力表述，缺具体题例', '空泛的「体验形成过程」', '空泛的「增强信心」'] },
      { id: 'bd-keypoints', name: '教学重难点', minutes: 0, teacher_activity: '重点：定义中 $2a>2c$ 的必要性与 $a^2=b^2+c^2$ 由来；难点：焦点在 $x/y$ 轴标准方程的判别与两次平方化简。', student_activity: '明确重难点做好笔记标注。', design_intent: '把重难点显性化，供学生听课对标。' },
      { id: 'bd-intro', name: '情境引入', minutes: 4, teacher_activity: '演示行星轨道与圆的压扁动画，提问：圆的定义中「到定点距离等于定长」，若改成「距离之和」会得到什么曲线？', student_activity: '观察动画，回忆圆的定义。', design_intent: '从圆到椭圆的最近发展区引入。' },
      { id: 'bd-explore', name: '新知探究', minutes: 12, teacher_activity: '先发放细绳与图钉做画椭圆实验，再板书定义 $|MF_1|+|MF_2|=2a\\;(2a>2c)$，随后建系设点，演示 $\\sqrt{(x+c)^2+y^2}+\\sqrt{(x-c)^2+y^2}=2a$ 两次平方化简。', student_activity: '两人一组画椭圆（改变两定点距离观察变化），跟随推导完成课本填空。', design_intent: '生成「距离之和为定值」的直观经验后，固化定义并经历根式方程到标准方程的代数简化。' },
      { id: 'bd-examples', name: '例题精讲', minutes: 14, teacher_activity: '讲例 1：焦点在 $x$ 轴、$a=5$、$b=3$ 求椭圆方程，板书分三步书写并指明所用依据。', student_activity: '精听并同步演算例 1，核对每一步的符号依据。', design_intent: '标准方程的正用：已知参数求方程。',
        examples: [
          { id: 'ex-eg1', label: '例 1', q_type: 'solve', difficulty: 'easy', stem_latex: '焦点在 x 轴、a=5、b=3 的椭圆，求它的标准方程。', answer: '\\frac{x^{2}}{25}+\\frac{y^{2}}{9}=1', source: '校本' },
        ] },
      { id: 'bd-variation', name: '变式训练', minutes: 8, teacher_activity: '给变式：焦点在 $y$ 轴、$c=4$，长轴顶点纵坐标为 $5$，求方程；提示先定位焦点所在轴。', student_activity: '独立完成变式，同桌互批。', design_intent: '自动定位能力与焦轴判别。',
        examples: [
          { id: 'ex-var1', label: '变式 1', q_type: 'solve', difficulty: 'medium', stem_latex: '焦点在 y 轴、焦点距 2c=8，长轴长 2a=10，求椭圆标准方程。', answer: '\\frac{y^{2}}{25}+\\frac{x^{2}}{9}=1', source: '校本' },
        ] },
      { id: 'bd-pitfalls', name: '易错辨析', minutes: 5, teacher_activity: '对比两道易错结构：焦点未明时不能用 $a^2=b^2+c^2$ 直接代 $c$；$2a=2c$ 时曲线退化为线段。让学生判断错例。', student_activity: '判断下列写法的对错并说明理由。', design_intent: '用反例固化定义中的不等式与焦轴判别。' },
      { id: 'bd-summary', name: '课堂小结与检测', minutes: 3, teacher_activity: '梳理「定义 → 方程 → 判别」主线，布置一道当堂检测：已知 $a=6$、$c=4$ 求标准方程（焦轴自判）。', student_activity: '完成当堂检测并订正。', design_intent: '结构化收束并即时回收掌握度。' },
      { id: 'bd-homework', name: '分层作业', minutes: 2, teacher_activity: '按基础/巩固/挑战三层布置，挑战题需证明中点轨迹仍是椭圆。', student_activity: '按层次选题完成。', design_intent: '分层落实，挑战层衔接后继轨迹方程。',
        examples: [
          { id: 'ex-hw1', label: '挑战题', q_type: 'solve', difficulty: 'hard', stem_latex: '从圆 $x^{2}+y^{2}=25$ 上任一点向 x 轴作垂线段，证明垂足为中点的点的轨迹是椭圆。', answer: '提示：设点并消参，对比标准方程', source: '校本' },
        ] },
    ],
    board_design_note: '主板书：左侧定义（图形+符号），中间推导主线，右侧例题与变式；副板书留学生画图区',
    homework_tiers: [
      { tier: '基础', items: ['课本 P42 习题 2-1 第 1、2 题'] },
      { tier: '巩固', items: ['已知椭圆 $\\frac{x^2}{m+4}+\\frac{y^2}{9}=1$ 焦点在 $y$ 轴，求 $m$ 范围'] },
      { tier: '挑战', items: ['从圆 $x^2+y^2=25$ 上任一点向 $x$ 轴作垂线段，中点轨迹是什么？证明你的结论'] },
    ],
    refs: ['人教A版选择性必修一 P38-41', '2007 课标 · 圆锥曲线', '校本资源：细绳实验视频'],
  },
  {
    id: 'plan-derivative', topic: '导数与函数单调性（习题课）', class_id: 'c2-03', lesson_type: '习题课', template_id: 'lt-lecture-drill',
    objectives: ['熟练运用 $f\'(x)>0$ 判定单调区间', '掌握含参单调性问题的分类讨论'],
    key_points: ['定义域优先', '分类讨论的边界与完备性'],
    sections: [
      { id: 's1', name: '复习铺垫', minutes: 5, teacher_activity: '提问单调性与导数符号的关系，板书判定链', student_activity: '口答', design_intent: '激活先行知识' },
      { id: 's2', name: '典型例题', minutes: 15, teacher_activity: '例：$f(x)=ax^3-3x^2+1$ 讨论 $a$ 的取值对单调性的影响', student_activity: '先独立尝试 5 分钟再听讲', design_intent: '覆盖「分类不完」高频错误' },
      { id: 's3', name: '方法提炼', minutes: 8, teacher_activity: '总结含参三步：定域 → 求导 → 定界分类', student_activity: '笔记', design_intent: '方法显性化' },
      { id: 's4', name: '分层练习', minutes: 12, teacher_activity: 'A 组换数字 / B 组换条件 / C 组综合', student_activity: '按层次选题完成', design_intent: '分层落实' },
    ],
    board_design_note: '左：判定链；中：例题全过程（保留分类树）；右：分层题组',
    homework_tiers: [
      { tier: '基础', items: ['课本 P90 第 3 题'] },
      { tier: '挑战', items: ['$f(x)=x\\cdot e^{ax}$ 的单调性讨论'] },
    ],
    refs: ['人教A版选择性必修二 P86-90'],
  },
  {
    id: 'plan-hyperbola', topic: '双曲线及其标准方程（第1课时）', class_id: 'c2-05', lesson_type: '新授课', template_id: 'lt-explorer',
    objectives: [
      '理解双曲线的定义，能说出定义中两个定点与距离差的定值关系（$2a<|F_1F_2|$）',
      '掌握双曲线标准方程的推导过程，会区分焦点在 $x$ 轴与 $y$ 轴时的两种形式',
      '能根据条件求双曲线的标准方程，并与椭圆方程进行对照',
    ],
    key_points: [
      '定义中「距离之差的绝对值为定值 $2a$（$0<2a<|F_1F_2|$）」的必要性',
      '两种标准方程的判别：看 $x^2$、$y^2$ 项系数的正负（与椭圆"看分母大小"不同）',
    ],
    sections: [
      { id: 'bd-context', name: '课标与学情', minutes: 2, teacher_activity: '出示本节目标，结合本班学情提示核心是「把双曲线定义翻译成代数方程」，并点出与椭圆的最大差异：差为定值 vs 和为定值。', student_activity: '回看椭圆定义与标准方程笔记，准备对照学习。', design_intent: '用"和 vs 差"的认知冲突先行，对照椭圆学习双曲线。' },
      { id: 'bd-objectives', name: '教学目标', minutes: 0, teacher_activity: '通过拉链实验探究双曲线的定义，激发学生学习数学的兴趣，培养学生分析问题与解决问题的能力。', student_activity: '体会知识的形成过程，增强学好数学的信心。', design_intent: '基于操作经验与代数推导，让学生自然获得双曲线的标准方程。', cliche: true, cliche_hits: ['空泛的「激发……兴趣」，未说清用什么激', '口号式能力表述，缺具体题例'] },
      { id: 'bd-keypoints', name: '教学重难点', minutes: 0, teacher_activity: '重点：定义中 $|MF_1|-|MF_2|=\\pm 2a$ 与 $0<2a<|F_1F_2|$ 的必要性；难点：两种标准方程的判别（看项系数正负，不是看分母大小）。', student_activity: '明确重难点，做好与椭圆判别法的对照笔记。', design_intent: '把"判别方式反转"这一高频易错点前置。' },
      { id: 'bd-intro', name: '情境引入', minutes: 4, teacher_activity: '演示拉链实验：拉开拉链时笔尖轨迹；提问：椭圆是"距离之和为定值"，若改成"距离之差的绝对值为定值"，会得到什么曲线？', student_activity: '观察拉链实验，猜想轨迹形状。', design_intent: '从椭圆定义的反向操作引入，建立"差为定值"的直观。' },
      { id: 'bd-explore', name: '新知探究', minutes: 12, teacher_activity: '用拉链/图钉复现定义实验，板书定义 $\\big||MF_1|-|MF_2|\\big|=2a\\;(0<2a<|F_1F_2|)$；随后建系，演示 $\\sqrt{(x+c)^{2}+y^{2}}-\\sqrt{(x-c)^{2}+y^{2}}=\\pm 2a$ 的化简（对比椭圆：一次平方即可）。', student_activity: '两人一组操作拉链（改变 $2a$ 观察开口变化），跟随推导完成填空。', design_intent: '经历"差为定值"的生成过程，对比椭圆化简体会结构差异。' },
      { id: 'bd-examples', name: '例题精讲', minutes: 14, teacher_activity: '讲例 1：焦点在 $x$ 轴、$a=4$、$b=3$ 求双曲线方程，板书分三步书写，并当场与椭圆版本（$\\frac{x^{2}}{16}+\\frac{y^{2}}{9}=1$）对照。', student_activity: '精听并同步演算例 1，亲手写出对照的椭圆方程。', design_intent: '标准方程正用 + 与椭圆对照固化判别。',
        examples: [
          { id: 'ex-hg1', label: '例 1', q_type: 'solve', difficulty: 'easy', stem_latex: '焦点在 x 轴、a=4、b=3 的双曲线，求它的标准方程。', answer: '\\frac{x^{2}}{16}-\\frac{y^{2}}{9}=1', source: '校本' },
        ] },
      { id: 'bd-variation', name: '变式训练', minutes: 8, teacher_activity: '给变式：焦点在 $y$ 轴、$c=5$、$a=3$，求方程；提示先定位焦点所在轴，再看 $y^2$ 项系数为正。', student_activity: '独立完成变式，同桌互批并互述判别理由。', design_intent: '焦轴定位 + 项系数判别。',
        examples: [
          { id: 'ex-hvar1', label: '变式 1', q_type: 'solve', difficulty: 'medium', stem_latex: '焦点在 y 轴、c=5、a=3 的双曲线，求标准方程。', answer: '\\frac{y^{2}}{9}-\\frac{x^{2}}{16}=1', source: '校本' },
        ] },
      { id: 'bd-pitfalls', name: '易错辨析', minutes: 5, teacher_activity: '对比三道易错结构：$2a\\ge|F_1F_2|$ 时轨迹不存在或退化；误用椭圆"分母大小"判别双曲线焦轴；漏掉差的绝对值导致只画一支。让学生判断错例。', student_activity: '判断下列写法的对错并说明理由。', design_intent: '用反例固化定义条件与判别方式。' },
      { id: 'bd-summary', name: '课堂小结与检测', minutes: 3, teacher_activity: '用对照表梳理「定义 → 方程 → 判别」，布置一道当堂检测：已知 $a=2$、$c=4$ 求标准方程（焦轴自判）。', student_activity: '完成当堂检测并订正，说出一处与椭圆的差异。', design_intent: '结构化收束并即时回收掌握度。' },
      { id: 'bd-homework', name: '分层作业', minutes: 2, teacher_activity: '按基础/巩固/挑战三层布置，挑战题探究双曲线渐近线对开口的刻画。', student_activity: '按层次选题完成。', design_intent: '分层落实，挑战层衔接几何性质。',
        examples: [
          { id: 'ex-hw1', label: '挑战题', q_type: 'solve', difficulty: 'hard', stem_latex: '双曲线 \\frac{x^{2}}{9}-\\frac{y^{2}}{16}=1 的渐近线方程是什么？改变 a、b 观察渐近线张角，写一条发现。', answer: 'y=±\\frac{4}{3}x（提示：开口随 b/a 增大而变大）', source: '校本' },
        ] },
    ],
    board_design_note: '主板书：左侧对照表（椭圆 vs 双曲线），中间推导主线，右侧例题与变式；副板书留拉链实验图',
    homework_tiers: [
      { tier: '基础', items: ['课本 P48 习题 2-2 第 1、2 题'] },
      { tier: '巩固', items: ['焦点在 $y$ 轴、$c=6$、$b=4$ 的双曲线标准方程'] },
      { tier: '挑战', items: ['探究 $\\frac{x^{2}}{9}-\\frac{y^{2}}{16}=1$ 的渐近线，并说明 $b/a$ 对开口的影响'] },
    ],
    refs: ['人教A版选择性必修一 P46-49', '2007 课标 · 圆锥曲线', '校本资源：拉链实验视频'],
  },
]

/* ==================== 课件（核心：数学元素全部结构化） ==================== */

let _eid = 0
const eid = () => `e${++_eid}`
const el = (o: Partial<Record<string, unknown>> & { type: string; left: number; top: number; width: number; height: number }) =>
  ({ id: eid(), z: 1, ...o } as any)

/** 拍照原图（SVG 手写题照片 mock：原始锚定图，永不删除） */
export const V3_PHOTO_STEM = 'data:image/svg+xml;utf8,' + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 260"><rect width="360" height="260" fill="#fbf7ec"/>` +
  `<text x="24" y="42" font-family="KaiTi,STKaiti,serif" font-size="17" fill="#2d2a24">例：过椭圆 x²/4+y²/3=1 的右焦</text>` +
  `<text x="24" y="70" font-family="KaiTi,STKaiti,serif" font-size="17" fill="#2d2a24">点 F 作倾斜角为 60° 的直线 l，</text>` +
  `<text x="24" y="98" font-family="KaiTi,STKaiti,serif" font-size="17" fill="#2d2a24">交椭圆于 A、B 两点，求：</text>` +
  `<text x="24" y="126" font-family="KaiTi,STKaiti,serif" font-size="17" fill="#2d2a24">(1) |AB|；(2) △AOB 的面积。</text>` +
  `<ellipse cx="150" cy="200" rx="70" ry="26" fill="none" stroke="#8a6d3b" stroke-width="1.6"/>` +
  `<circle cx="220" cy="200" r="2.5" fill="#8a6d3b"/><circle cx="80" cy="200" r="2.5" fill="#8a6d3b"/>` +
  `<line x1="185" y1="200" x2="235" y2="170" stroke="#8a6d3b" stroke-width="1.4"/>` +
  `<text x="150" y="6" font-size="10" fill="#b9ac8f" text-anchor="middle">学生作业拍照 · 高二(3)班 · 例题</text></svg>`)

export const V3_DECKS = [
  {
    id: 'deck-ellipse', title: '椭圆及其标准方程（第1课时）', template_id: 'tpl-academic-blue',
    source: 'topic' as const,
    slides: [
      {
        id: 'sl1', layout: 'cover' as const, elements: [
          el({ type: 'text', left: 90, top: 210, width: 760, height: 90, html: '椭圆及其标准方程', font_size: 48, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 92, top: 306, width: 620, height: 40, html: '人教A版选择性必修一 · 第二章 §2.2 · 第 1 课时', font_size: 18, color: '#4a5568' }),
          el({ type: 'formula', left: 92, top: 380, width: 420, height: 60, latex: '\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1\\;(a>b>0)', font_size: 22, display: true }),
          el({ type: 'geometry', left: 830, top: 170, width: 360, height: 320, preset_id: 'conic/ellipse-coordinate', params: { a: 2, b: 1.4 } }),
        ],
      },
      {
        id: 'sl2', layout: 'review' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 400, height: 52, html: '复习回顾 · 圆的定义', font_size: 28, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 70, top: 140, width: 560, height: 44, html: '平面内到定点（圆心）的距离等于定长（半径）的点的轨迹', font_size: 20 }),
          el({ type: 'formula', left: 70, top: 210, width: 260, height: 46, latex: '|MF| = r', font_size: 22 }),
          el({ type: 'text', left: 70, top: 300, width: 560, height: 120, html: '<b>改变条件：</b>若「到定点的距离」改为「到两个定点的距离之和」，轨迹会是什么样子？', font_size: 21, color: '#a87b24' }),
          el({ type: 'geometry', left: 700, top: 130, width: 500, height: 420, preset_id: 'plane/circle-line', params: { r: 3, d: 2.4 } }),
        ],
      },
      {
        id: 'sl3', layout: 'definition' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 500, height: 52, html: '椭圆的定义', font_size: 28, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 70, top: 140, width: 620, height: 110, html: '平面内与两个定点 $F_1$、$F_2$ 的距离之和等于常数（大于 $|F_1F_2|$）的点的轨迹叫做椭圆。两个定点叫做椭圆的<b>焦点</b>，两焦点间的距离叫做椭圆的<b>焦距</b>。', font_size: 21 }),
          el({ type: 'formula', left: 70, top: 288, width: 380, height: 56, latex: '|MF_{1}|+|MF_{2}|=2a\\;(2a>2c=|F_{1}F_{2}|)', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 372, width: 620, height: 44, html: '思考：当 2a = 2c 时，轨迹是什么？（线段） 当 2a &lt; 2c 呢？（不存在）', font_size: 19, color: '#b45309' }),
          el({ type: 'geometry', left: 740, top: 140, width: 460, height: 400, preset_id: 'conic/ellipse', params: { a: 3, b: 2 } }),
        ],
      },
      {
        id: 'sl4', layout: 'derivation' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 560, height: 52, html: '标准方程的推导', font_size: 28, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 70, top: 130, width: 640, height: 40, html: '建系：以 $F_1F_2$ 所在直线为 $x$ 轴，线段 $F_1F_2$ 的垂直平分线为 $y$ 轴', font_size: 20 }),
          el({ type: 'formula', left: 70, top: 192, width: 620, height: 56, latex: '\\sqrt{(x+c)^{2}+y^{2}}+\\sqrt{(x-c)^{2}+y^{2}}=2a', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 264, width: 640, height: 36, html: '两次平方、整理（移项后平方可去根号）：', font_size: 20 }),
          el({ type: 'formula', left: 70, top: 306, width: 620, height: 56, latex: '(a^{2}-c^{2})x^{2}+a^{2}y^{2}=a^{2}(a^{2}-c^{2})', font_size: 22, display: true }),
          el({ type: 'formula', left: 70, top: 380, width: 620, height: 56, latex: 'b^{2}=a^{2}-c^{2}\\;(b>0)\\;\\Rightarrow\\;\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 456, width: 640, height: 40, html: '焦点在 y 轴时：标准方程交换 x、y 位置，判别看分母大小', font_size: 20, color: '#0e9488' }),
          el({ type: 'geometry', left: 750, top: 140, width: 450, height: 380, preset_id: 'conic/ellipse-coordinate', params: { a: 3, b: 2.2 } }),
        ],
      },
      {
        id: 'sl5', layout: 'example' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 500, height: 52, html: '例 1 · 求标准方程', font_size: 28, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 70, top: 132, width: 1000, height: 66, html: '椭圆的焦点在 $x$ 轴上，$a=5$，$b=3$，求它的标准方程。', font_size: 21 }),
          el({ type: 'text', left: 70, top: 240, width: 120, height: 40, html: '解：', font_size: 21, bold: true }),
          el({ type: 'formula', left: 150, top: 238, width: 500, height: 46, latex: 'c^{2}=a^{2}-b^{2}=25-9=16', font_size: 22 }),
          el({ type: 'text', left: 70, top: 320, width: 1000, height: 40, html: '焦点在 x 轴，直接代入：', font_size: 20 }),
          el({ type: 'formula', left: 150, top: 364, width: 460, height: 52, latex: '\\frac{x^{2}}{25}+\\frac{y^{2}}{9}=1', font_size: 24, display: true }),
          el({ type: 'text', left: 70, top: 460, width: 1000, height: 60, html: '<b>变式：</b>若改为「焦点在坐标轴上，a=5，b=3」，答案有几个？', font_size: 20, color: '#a87b24' }),
        ],
      },
      {
        id: 'sl6', layout: 'summary' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 500, height: 52, html: '课堂小结', font_size: 28, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 70, top: 140, width: 1000, height: 44, html: '① 定义：$|MF_1|+|MF_2|=2a\\;(2a>2c)$　② 方程：焦点位置看分母', font_size: 22 }),
          el({ type: 'text', left: 70, top: 204, width: 1000, height: 44, html: '③ 关系：$a^2=b^2+c^2$（$a$ 最长）　④ 方法：定义法 + 待定系数法', font_size: 22 }),
          el({ type: 'geometry', left: 360, top: 300, width: 560, height: 340, preset_id: 'conic/ellipse-coordinate', params: { a: 3, b: 2 } }),
        ],
      },
    ],
    updated_at: '2026-09-03 08:12',
  },
  {
    id: 'deck-photo', title: '椭圆焦点弦例题（拍照生成）', template_id: 'tpl-chalkboard',
    source: 'photo' as const,
    photo_context: { config: { scope: 'stem+solution', mode: 'full-solution', template_id: 'tpl-chalkboard', font_tier: 'standard', margin_notes: true }, photos: 1, question_label: '高二(3)班作业 · 例题' },
    slides: [
      {
        id: 'ps1', layout: 'definition' as const, elements: [
          el({ type: 'text', left: 70, top: 44, width: 620, height: 48, html: '例题（拍照识别 · 原图已锚定）', font_size: 26, bold: true, color: '#1e3a2f' }),
          el({ type: 'anchorPhoto', left: 70, top: 110, width: 330, height: 240, src: V3_PHOTO_STEM, upgrade_state: 'rebuilt', upgrade_target: 'conic/ellipse-coordinate' }),
          el({ type: 'text', left: 440, top: 110, width: 700, height: 46, html: '过椭圆 $\\frac{x^2}{4}+\\frac{y^2}{3}=1$ 的右焦点 $F$ 作倾斜角为 $60°$ 的直线 $l$，交椭圆于 $A$、$B$ 两点，求：', font_size: 20 }),
          el({ type: 'text', left: 440, top: 170, width: 700, height: 40, html: '(1) $|AB|$；(2) $\\triangle AOB$ 的面积。', font_size: 20 }),
          el({ type: 'geometry', left: 440, top: 236, width: 380, height: 300, preset_id: 'conic/ellipse-coordinate', params: { a: 2, b: 1.73 } }),
          el({ type: 'text', left: 70, top: 400, width: 330, height: 100, html: '<b>边注：</b>识别块均已在右侧转为可编辑公式；原图保留供对照核验。', font_size: 15, color: '#64748b' }),
        ],
      },
      {
        id: 'ps2', layout: 'solution-flow' as const, anchor_bar: '接上页 · 过 F(1,0) 作 l：y=√3(x−1)',
        elements: [
          el({ type: 'text', left: 70, top: 44, width: 500, height: 44, html: '解答 · 第 1 页 / 共 2 页', font_size: 22, bold: true, color: '#1e3a2f' }),
          el({ type: 'text', left: 70, top: 110, width: 1060, height: 40, html: '设直线 $l: y=\\sqrt{3}(x-1)$，与椭圆方程联立：', font_size: 20 }),
          el({ type: 'formula', left: 110, top: 160, width: 640, height: 52, latex: '\\begin{cases} y=\\sqrt{3}(x-1) \\\\ \\frac{x^{2}}{4}+\\frac{y^{2}}{3}=1 \\end{cases}', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 240, width: 1060, height: 40, html: '代入消元得：', font_size: 20 }),
          el({ type: 'formula', left: 110, top: 290, width: 560, height: 52, latex: '7x^{2}-8x-8=0', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 372, width: 1060, height: 40, html: '设 $A(x_1,y_1)$、$B(x_2,y_2)$，由韦达定理：', font_size: 20 }),
          el({ type: 'formula', left: 110, top: 424, width: 460, height: 52, latex: 'x_{1}+x_{2}=\\frac{8}{7},\\quad x_{1}x_{2}=-\\frac{8}{7}', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 508, width: 1060, height: 36, html: '弦长公式 →（下页续）', font_size: 19, color: '#b45309' }),
        ],
      },
      {
        id: 'ps3', layout: 'solution-flow' as const, anchor_bar: '接上页 · 弦长 |AB| 与面积',
        elements: [
          el({ type: 'text', left: 70, top: 44, width: 500, height: 44, html: '解答 · 第 2 页 / 共 2 页', font_size: 22, bold: true, color: '#1e3a2f' }),
          el({ type: 'formula', left: 70, top: 120, width: 760, height: 56, latex: '|AB|=\\sqrt{1+k^{2}}\\,\\cdot|x_{1}-x_{2}|=\\sqrt{1+3}\\cdot\\sqrt{\\frac{64}{49}+\\frac{32}{7}}=\\frac{24}{7}', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 210, width: 1060, height: 40, html: '原点到直线 $\\sqrt{3}x-y-\\sqrt{3}=0$ 的距离：', font_size: 20 }),
          el({ type: 'formula', left: 110, top: 262, width: 520, height: 56, latex: 'd=\\frac{\\sqrt{3}}{2}', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 350, width: 1060, height: 40, html: '所以：', font_size: 20 }),
          el({ type: 'formula', left: 110, top: 402, width: 560, height: 56, latex: 'S_{\\triangle AOB}=\\frac{1}{2}\\cdot\\frac{24}{7}\\cdot\\frac{\\sqrt{3}}{2}=\\frac{6\\sqrt{3}}{7}', font_size: 24, display: true }),
          el({ type: 'text', left: 70, top: 510, width: 1060, height: 40, html: '检验：换弦长公式第二形式 |AB|=2p... 亦可，结果一致。✓', font_size: 18, color: '#0e9488' }),
        ],
      },
    ],
    updated_at: '2026-09-03 09:48',
  },
  {
    id: 'deck-hyperbola', title: '双曲线及其标准方程（第1课时）', template_id: 'tpl-academic-blue',
    source: 'topic' as const,
    slides: [
      {
        id: 'hl1', layout: 'cover' as const, elements: [
          el({ type: 'text', left: 90, top: 210, width: 760, height: 90, html: '双曲线及其标准方程', font_size: 48, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 92, top: 306, width: 620, height: 40, html: '人教A版选择性必修一 · 第二章 §2.3 · 第 1 课时', font_size: 18, color: '#4a5568' }),
          el({ type: 'formula', left: 92, top: 380, width: 460, height: 60, latex: '\\frac{x^{2}}{a^{2}}-\\frac{y^{2}}{b^{2}}=1\\;(a>0,b>0)', font_size: 22, display: true }),
          el({ type: 'geometry', left: 830, top: 170, width: 360, height: 320, preset_id: 'conic/hyperbola', params: { a: 2 } }),
        ],
      },
      {
        id: 'hl2', layout: 'review' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 480, height: 52, html: '复习对照 · 椭圆的定义', font_size: 28, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 70, top: 140, width: 560, height: 44, html: '椭圆：到两定点距离<b>之和</b>为定值（$2a>|F_1F_2|$）', font_size: 20 }),
          el({ type: 'formula', left: 70, top: 210, width: 340, height: 46, latex: '|MF_{1}|+|MF_{2}|=2a', font_size: 22 }),
          el({ type: 'text', left: 70, top: 300, width: 620, height: 120, html: '<b>改变条件：</b>把「距离之和」改成「距离之差的绝对值」为定值，轨迹会是什么样子？', font_size: 21, color: '#a87b24' }),
        ],
      },
      {
        id: 'hl3', layout: 'definition' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 500, height: 52, html: '双曲线的定义', font_size: 28, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 70, top: 140, width: 640, height: 110, html: '平面内与两个定点 $F_1$、$F_2$ 的距离的<b>差的绝对值</b>等于常数（小于 $|F_1F_2|$）的点的轨迹叫做双曲线。两个定点叫做双曲线的<b>焦点</b>。', font_size: 21 }),
          el({ type: 'formula', left: 70, top: 288, width: 420, height: 56, latex: '\\big||MF_{1}|-|MF_{2}|\\big|=2a\\;(0<2a<|F_{1}F_{2}|)', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 372, width: 640, height: 66, html: '思考：当 $2a=|F_1F_2|$ 时？（两条射线）　当 $2a>|F_1F_2|$ 时？（不存在）　若不加绝对值？（只有一支）', font_size: 19, color: '#b45309' }),
          el({ type: 'geometry', left: 760, top: 140, width: 440, height: 400, preset_id: 'conic/hyperbola', params: { a: 2 } }),
        ],
      },
      {
        id: 'hl4', layout: 'derivation' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 560, height: 52, html: '标准方程的推导', font_size: 28, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 70, top: 130, width: 640, height: 40, html: '建系同椭圆：$F_1(-c,0)$、$F_2(c,0)$，设点 $M(x,y)$', font_size: 20 }),
          el({ type: 'formula', left: 70, top: 192, width: 660, height: 56, latex: '\\sqrt{(x+c)^{2}+y^{2}}-\\sqrt{(x-c)^{2}+y^{2}}=\\pm 2a', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 264, width: 640, height: 36, html: '一次平方即可去根号（对比椭圆的两次平方）：', font_size: 20 }),
          el({ type: 'formula', left: 70, top: 306, width: 620, height: 56, latex: '(c^{2}-a^{2})x^{2}-a^{2}y^{2}=a^{2}(c^{2}-a^{2})', font_size: 22, display: true }),
          el({ type: 'formula', left: 70, top: 380, width: 660, height: 56, latex: 'b^{2}=c^{2}-a^{2}\\;(b>0)\\;\\Rightarrow\\;\\frac{x^{2}}{a^{2}}-\\frac{y^{2}}{b^{2}}=1', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 456, width: 660, height: 40, html: '<b>判别反转：</b>双曲线看 $x^2$、$y^2$ 项系数的<b>正负</b>（正者为实轴），不是看分母大小！', font_size: 20, color: '#0e9488' }),
        ],
      },
      {
        id: 'hl5', layout: 'example' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 500, height: 52, html: '例 1 · 求标准方程', font_size: 28, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 70, top: 132, width: 1000, height: 66, html: '双曲线的焦点在 $x$ 轴上，$a=4$，$b=3$，求它的标准方程。', font_size: 21 }),
          el({ type: 'text', left: 70, top: 240, width: 120, height: 40, html: '解：', font_size: 21, bold: true }),
          el({ type: 'formula', left: 150, top: 238, width: 520, height: 46, latex: 'b^{2}=c^{2}-a^{2}=9 \\Rightarrow c=5,\\; F_{1}(-5,0),F_{2}(5,0)', font_size: 22 }),
          el({ type: 'text', left: 70, top: 320, width: 1000, height: 40, html: '焦点在 x 轴，$x^2$ 项系数为正，直接写出：', font_size: 20 }),
          el({ type: 'formula', left: 150, top: 364, width: 460, height: 52, latex: '\\frac{x^{2}}{16}-\\frac{y^{2}}{9}=1', font_size: 24, display: true }),
          el({ type: 'text', left: 70, top: 460, width: 1000, height: 60, html: '<b>变式：</b>若改为「焦点在 y 轴，c=5，a=3」，方程变成什么？判别依据是什么？', font_size: 20, color: '#a87b24' }),
        ],
      },
      {
        id: 'hl6', layout: 'summary' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 500, height: 52, html: '课堂小结 · 与椭圆对照', font_size: 28, bold: true, color: '#0a3568' }),
          el({ type: 'text', left: 70, top: 140, width: 1000, height: 44, html: '① 定义：和为定值 $\\Leftrightarrow$ 差的绝对值为定值（$0<2a<|F_1F_2|$）', font_size: 22 }),
          el({ type: 'text', left: 70, top: 204, width: 1000, height: 44, html: '② 方程：$\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1$　vs　$\\frac{x^{2}}{a^{2}}-\\frac{y^{2}}{b^{2}}=1$', font_size: 22 }),
          el({ type: 'text', left: 70, top: 268, width: 1000, height: 44, html: '③ 判别：椭圆看分母大小；双曲线看项系数<b>正负</b>　④ 关系：$b^2=c^2-a^2$（$c$ 最大）', font_size: 22 }),
          el({ type: 'geometry', left: 360, top: 320, width: 560, height: 320, preset_id: 'conic/hyperbola', params: { a: 2.4 } }),
        ],
      },
    ],
    updated_at: '2026-09-05 20:10',
  },
  {
    id: 'deck-derivative', title: '导数与函数单调性（第1课时）', template_id: 'tpl-chalkboard',
    source: 'topic' as const,
    slides: [
      {
        id: 'dr1', layout: 'cover' as const, elements: [
          el({ type: 'text', left: 90, top: 210, width: 760, height: 90, html: '导数与函数单调性', font_size: 48, bold: true, color: '#1e3a2f' }),
          el({ type: 'text', left: 92, top: 306, width: 620, height: 40, html: '人教A版选择性必修二 · 第五章 · 第 1 课时', font_size: 18, color: '#4a5568' }),
          el({ type: 'formula', left: 92, top: 380, width: 420, height: 60, latex: "f'(x)>0 \\Rightarrow f(x) \\text{ 单调递增}", font_size: 22, display: true }),
        ],
      },
      {
        id: 'dr2', layout: 'review' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 520, height: 52, html: '情境引入 · 瞬时变化率', font_size: 28, bold: true, color: '#1e3a2f' }),
          el({ type: 'text', left: 70, top: 140, width: 620, height: 88, html: '高台跳水：运动员离水面高度 $h(t)$ 随时间变化——哪一段在上升、哪一段在下降？由什么量决定？', font_size: 20 }),
          el({ type: 'formula', left: 70, top: 260, width: 420, height: 52, latex: "v(t)=h'(t)", font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 350, width: 620, height: 66, html: '<b>核心问题：</b>能否用导数的符号，直接判断函数的单调性？', font_size: 21, color: '#e8c56a' }),
        ],
      },
      {
        id: 'dr3', layout: 'derivation' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 560, height: 52, html: '判定链的推导', font_size: 28, bold: true, color: '#1e3a2f' }),
          el({ type: 'text', left: 70, top: 130, width: 660, height: 44, html: '在区间 $(a,b)$ 上任取 $x_1<x_2$，用导数定义作差：', font_size: 20 }),
          el({ type: 'formula', left: 70, top: 192, width: 660, height: 56, latex: '\\frac{f(x_{2})-f(x_{1})}{x_{2}-x_{1}}=f\'(\\xi)>0', font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 264, width: 660, height: 66, html: '由拉格朗日中值定理，$f(x_2)-f(x_1)=f\'(\\xi)(x_2-x_1)>0$，故 $f$ 在 $(a,b)$ 递增。', font_size: 20 }),
          el({ type: 'formula', left: 70, top: 356, width: 520, height: 56, latex: "f'(x)>0 \\Rightarrow \\text{增}；\\quad f'(x)<0 \\Rightarrow \\text{减}", font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 440, width: 660, height: 40, html: '注意：逆命题不成立（$f\'\\ge 0$ 且零点离散亦可增，如 $y=x^3$）。', font_size: 19, color: '#e8c56a' }),
        ],
      },
      {
        id: 'dr4', layout: 'example' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 500, height: 52, html: '例 1 · 求单调区间', font_size: 28, bold: true, color: '#1e3a2f' }),
          el({ type: 'text', left: 70, top: 132, width: 1000, height: 66, html: '已知 $f(x)=x^{3}-3x$，求它的单调区间。', font_size: 21 }),
          el({ type: 'text', left: 70, top: 226, width: 120, height: 40, html: '解：', font_size: 21, bold: true }),
          el({ type: 'formula', left: 150, top: 224, width: 480, height: 46, latex: "f'(x)=3x^{2}-3=3(x-1)(x+1)", font_size: 22 }),
          el({ type: 'formula', left: 150, top: 300, width: 620, height: 52, latex: "f'(x)>0 \\Rightarrow x\\in(-\\infty,-1)\\cup(1,+\\infty)", font_size: 22, display: true }),
          el({ type: 'text', left: 70, top: 380, width: 1000, height: 44, html: '单调递增区间 $(-\\infty,-1)$、$(1,+\\infty)$；单调递减区间 $(-1,1)$。', font_size: 20 }),
          el({ type: 'text', left: 70, top: 452, width: 1000, height: 60, html: '<b>变式：</b>求 $f(x)=x^{3}-3x^{2}+1$ 的单调递减区间（注意先写定义域）。', font_size: 20, color: '#e8c56a' }),
        ],
      },
      {
        id: 'dr5', layout: 'summary' as const, elements: [
          el({ type: 'text', left: 70, top: 52, width: 500, height: 52, html: '课堂小结', font_size: 28, bold: true, color: '#1e3a2f' }),
          el({ type: 'text', left: 70, top: 140, width: 1000, height: 44, html: '① 三步链：定定义域 → 求导 → 解 $f\'(x)>0\\ / <0$', font_size: 22 }),
          el({ type: 'text', left: 70, top: 204, width: 1000, height: 44, html: '② 区间之间用「和」不用「并」　③ 逆命题不成立：$y=x^3$', font_size: 22 }),
        ],
      },
    ],
    updated_at: '2026-09-05 20:12',
  },
]

/* ==================== 批改（三视图数据） ==================== */

const steps = (arr: [string, 'ok' | 'ai-flag' | 'corrected'][]) =>
  arr.map(([latex, status]) => ({ latex, status }))

export const V3_GRADING_ASSIGNMENTS = [
  { id: 'ga-1', title: '《导数与单调性》课后作业', class_id: 'c2-03', class_name: '高二(3)班', submitted: 44, total: 46, graded: 37, updated_at: '2026-09-02', is_sample: true },
  { id: 'ga-2', title: '《椭圆标准方程》随堂练习', class_id: 'c2-05', class_name: '高二(5)班', submitted: 42, total: 44, graded: 42, updated_at: '2026-09-01', is_sample: true },
]

export const V3_GRADING_DATA = {
  id: 'ga-1', title: '《导数与单调性》课后作业', class_id: 'c2-03',
  submitted: 44, total: 46, graded: 37,
  questions: [
    {
      q_no: 1, stem_latex: 'f(x)=x^{3}-3x^{2}+1 的单调递减区间为\\underline{\\qquad}', full_score: 5, accuracy: 0.78,
      error_dist: [{ tag: '计算错误' as const, count: 5 }, { tag: '概念混淆' as const, count: 2 }],
      clusters: [
        { id: 'q1c1', kind: 'correct' as const, count: 31, tag: undefined,
          members: [{ name: '王雨桐', score: 5 }, { name: '林小满', score: 5 }, { name: '周可欣', score: 5 }, { name: '郑好', score: 5 }, { name: '冯天佑', score: 5 }],
          sample: [{ student: '王雨桐', score: 5, photo_region: { x: 0.1, y: 0.1, w: 0.5, h: 0.3 }, recognized_steps: steps([['f\'(x)=3x^{2}-6x=3x(x-2)', 'ok'], ['0<x<2', 'ok']]) }] },
        { id: 'q1c2', kind: 'partial' as const, tag: '计算错误' as const, count: 5,
          members: [{ name: '陈子豪', score: 3 }, { name: '吴宇轩', score: 3 }],
          sample: [{ student: '陈子豪', score: 3, photo_region: { x: 0.1, y: 0.45, w: 0.5, h: 0.3 }, recognized_steps: steps([['f\'(x)=3x^{2}-6x', 'ok'], ['0<x<-2', 'corrected']]), feedback: '解 3x(x−2)<0 时不等号方向处理出错，建议复习一元二次不等式' }] },
        { id: 'q1c3', kind: 'blank' as const, count: 3,
          members: [{ name: '刘一鸣', score: 0 }],
          sample: [{ student: '刘一鸣', score: 0, photo_region: { x: 0.1, y: 0.8, w: 0.5, h: 0.15 }, recognized_steps: [] }] },
      ],
    },
    {
      q_no: 2, stem_latex: '讨论 a 取值对 f(x)=ax^{3}-3x^{2}+1 单调性的影响', full_score: 8, accuracy: 0.52,
      standard_answer: "f'(x)=x(3ax-6)。a=0 时 f=-3x²+1 在 R 递减；a>0 时极值点 x=0,2/a，按 0<2/a 即 a<2 与 a≥2 讨论；a<0 时递增区间 (2/a,0)",
      rubric: [{ point: '求导并提取公因式 x', score: 2 }, { point: 'a=0 单独讨论', score: 2 }, { point: 'a>0 含参分类完整（比较 2/a 与 0,2）', score: 3 }, { point: 'a<0 情形', score: 1 }],
      error_dist: [{ tag: '步骤缺失' as const, count: 9 }, { tag: '方法选择' as const, count: 4 }, { tag: '概念混淆' as const, count: 3 }],
      clusters: [
        { id: 'q2c1', kind: 'correct' as const, count: 18,
          members: [{ name: '林小满', score: 8 }, { name: '王雨桐', score: 8 }],
          sample: [{ student: '林小满', score: 8, photo_region: { x: 0.6, y: 0.1, w: 0.35, h: 0.4 }, recognized_steps: steps([['a=0\\Rightarrow f=-3x^{2}+1', 'ok'], ['a>0 分 0<a<2,a\\ge 2 讨论', 'ok']]) }] },
        { id: 'q2c2', kind: 'partial' as const, tag: '步骤缺失' as const, count: 9,
          members: [{ name: '赵启铭', score: 5 }, { name: '陈子豪', score: 5 }, { name: '吴宇轩', score: 5 }],
          sample: [{ student: '赵启铭', score: 5, photo_region: { x: 0.6, y: 0.55, w: 0.35, h: 0.4 }, recognized_steps: steps([['f\'(x)=3ax^{2}-6x', 'ok'], ['a>0 时 f\'(x)\\ge 0 恒成立？', 'ai-flag']]), feedback: '漏掉 a>0 时需比较判别式的分支；「步骤缺失」为 AI 预标注，请核对原图确认' }] },
        { id: 'q2c3', kind: 'wrong' as const, tag: '概念混淆' as const, count: 4,
          members: [{ name: '孙浩然', score: 2 }, { name: '刘一鸣', score: 2 }],
          sample: [{ student: '孙浩然', score: 2, photo_region: { x: 0.1, y: 0.55, w: 0.4, h: 0.35 }, recognized_steps: steps([['a<0 时 f 在 R 上递减', 'ai-flag']]), feedback: 'a<0 时 3ax²−6x 的开口向下，但仍有极值点，不能直接判定全程递减' }] },
      ],
    },
    {
      q_no: 3, stem_latex: 'f(x)=x\\cdot e^{-x} 的极大值点为\\underline{\\qquad}', full_score: 7, accuracy: 0.86,
      error_dist: [{ tag: '计算错误' as const, count: 4 }],
      clusters: [
        { id: 'q3c1', kind: 'correct' as const, count: 34,
          members: [{ name: '周可欣', score: 7 }, { name: '林小满', score: 7 }],
          sample: [{ student: '周可欣', score: 7, photo_region: { x: 0.6, y: 0.6, w: 0.35, h: 0.3 }, recognized_steps: steps([["f'(x)=(1-x)e^{-x}", 'ok'], ['x=1', 'ok']]) }] },
        { id: 'q3c2', kind: 'partial' as const, tag: '计算错误' as const, count: 4,
          members: [{ name: '吴宇轩', score: 4 }],
          sample: [{ student: '吴宇轩', score: 4, photo_region: { x: 0.1, y: 0.45, w: 0.4, h: 0.3 }, recognized_steps: steps([["f'(x)=e^{-x}-xe^{-x}", 'ok'], ["f'(x)=0 \\Rightarrow x=-1", 'corrected']]) }] },
      ],
    },
  ],
  tiers: [
    { tier: 'A' as const, label: '掌握扎实（≥85%）', students: [{ name: '林小满', avg: 92, weak_tags: [] }, { name: '周可欣', avg: 89, weak_tags: [] }, { name: '王雨桐', avg: 88, weak_tags: [] }], accuracy: 0.91 },
    { tier: 'B' as const, label: '基本掌握（70–85%）', students: [{ name: '赵启铭', avg: 78, weak_tags: ['步骤缺失'] }, { name: '陈子豪', avg: 74, weak_tags: ['计算错误'] }, { name: '吴宇轩', avg: 71, weak_tags: ['计算错误'] }], accuracy: 0.76 },
    { tier: 'C' as const, label: '需重点辅导（<70%）', students: [{ name: '孙浩然', avg: 58, weak_tags: ['概念混淆', '方法选择'] }, { name: '刘一鸣', avg: 52, weak_tags: ['步骤缺失', '概念混淆'] }], accuracy: 0.54 },
  ],
}

/* ==================== 组卷 / 资源 / 学情 / 配方 ==================== */

export const V3_QUIZ_QUESTIONS = [
  { id: 'q-ell-1', kp_name: '椭圆标准方程', kp_code: 'YD-01', q_type: 'choice' as const, difficulty: 'easy' as const, stem_latex: '椭圆 \\frac{x^{2}}{25}+\\frac{y^{2}}{16}=1 的焦点坐标为', options: ['(\\pm 3, 0)', '(\\pm 5, 0)', '(0, \\pm 3)', '(0, \\pm 5)'], answer: 'A', source: '校本' as const, year: '2024', analysis: '先由 a²=25、b²=16 得 c²=9，焦点在长轴（x 轴）上。', folder_refs: ['fld-1'], usage_count: 14 },
  { id: 'q-ell-2', kp_name: '椭圆几何性质', kp_code: 'YD-02', q_type: 'fill' as const, difficulty: 'easy' as const, stem_latex: '椭圆 9x^{2}+4y^{2}=36 的离心率 e=\\underline{\\qquad}', answer: 'e=\\frac{\\sqrt{5}}{3}', source: '区库' as const, year: '2023', analysis: '化标准方程得 a=3、b=2，e=c/a=√5/3。', usage_count: 9 },
  { id: 'q-ell-3', kp_name: '椭圆焦点弦', kp_code: 'YD-03', q_type: 'solve' as const, difficulty: 'hard' as const, stem_latex: '过椭圆 \\frac{x^{2}}{4}+\\frac{y^{2}}{3}=1 右焦点且斜率为 1 的直线交椭圆于 A、B，求 |AB|', answer: '\\frac{24}{7}', source: '拍照入库' as const, analysis: '联立直线与椭圆，韦达定理 + 弦长公式。', folder_refs: ['fld-1'], usage_count: 21 },
  { id: 'q-ell-4', kp_name: '椭圆标准方程', kp_code: 'YD-01', q_type: 'solve' as const, difficulty: 'medium' as const, stem_latex: '焦点在 y 轴，a=5，c=3，求椭圆标准方程', answer: '\\frac{y^{2}}{25}+\\frac{x^{2}}{16}=1', source: '校本' as const, analysis: '焦轴判别 + b²=a²−c²。', usage_count: 11 },
  { id: 'q-drv-1', kp_name: '导数与单调性', kp_code: 'DR-02', q_type: 'choice' as const, difficulty: 'easy' as const, stem_latex: "f(x)=x^{3}-3x 的单调递增区间为", options: ['(-\\infty,-1)', '(-1,1)', '(1,+\\infty)', '(-1,1)\\cup(1,+\\infty)'], answer: 'B', source: '区库' as const, year: '2025', analysis: "f'(x)=3x²−3>0 ⇒ −1<x<1。", usage_count: 17 },
  { id: 'q-drv-2', kp_name: '含参讨论', kp_code: 'DR-05', q_type: 'solve' as const, difficulty: 'hard' as const, stem_latex: '讨论 a 的取值对 f(x)=ax^{3}-3x^{2}+1 单调性的影响', answer: '分类讨论（a=0 / 0<a<2 / a≥2 / a<0）', source: '校本' as const, analysis: '含参三步：定域 → 求导 → 定界分类。', folder_refs: ['fld-2'], usage_count: 8 },
  { id: 'q-drv-3', kp_name: '极值问题', kp_code: 'DR-03', q_type: 'fill' as const, difficulty: 'medium' as const, stem_latex: 'f(x)=x\\cdot e^{-x} 的极大值为\\underline{\\qquad}', answer: '\\frac{1}{e}', source: '区库' as const, analysis: "f'(x)=(1−x)e^{−x}=0 ⇒ x=1。", folder_refs: ['fld-2'], usage_count: 5 },
  { id: 'q-sol-1', kp_name: '立体截面', kp_code: 'LT-04', q_type: 'solve' as const, difficulty: 'hard' as const, stem_latex: '正方体 ABCD-A_{1}B_{1}C_{1}D_{1} 棱长为 2，E、F、G 分别为 AB、CC_{1}、DD_{1} 中点，求截面 EFG 的面积', answer: '\\frac{3\\sqrt{3}}{2}', source: '拍照入库' as const, analysis: '截面为六边形补全法：延长找平行交点。', folder_refs: ['fld-1'], usage_count: 13 },
  // P4 图片题型：题干为一张图（拍照/扫描原图入库），照常可选入试卷
  { id: 'q-img-1', kp_name: '立体截面（图片题）', kp_code: 'LT-05', q_type: 'image' as const, difficulty: 'medium' as const, stem_latex: '（图片题）根据右图正方体的截面对话，判断截面形状。', stem_image: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="150" height="110"><rect x="2" y="2" width="146" height="106" fill="#f7f6f1" stroke="#c99735"/><text x="10" y="22" font-size="11" fill="#8a6d1d">图片题型 · 扫描原图</text><polygon points="40,70 90,70 110,95 60,95" fill="#dbe7f5" stroke="#0f4787"/><line x1="40" y1="70" x2="50" y2="45" stroke="#0f4787"/><line x1="90" y1="70" x2="100" y2="45" stroke="#0f4787"/><line x1="50" y1="45" x2="100" y2="45" stroke="#0f4787"/><text x="42" y="88" font-size="10" fill="#0f4787">正方体截面示意</text></svg>', answer: '梯形', source: '拍照入库' as const },
  // V3.1 来源细分样例：自编题（教师手输）+ AI 配题（配题前先检索题库相似题做参考）
  { id: 'q-self-1', kp_name: '椭圆标准方程', kp_code: 'YD-01', q_type: 'fill' as const, difficulty: 'medium' as const, stem_latex: '与椭圆 \\frac{x^{2}}{4}+\\frac{y^{2}}{3}=1 有相同焦点，且过点 (2,\\sqrt{3}) 的椭圆方程为\\underline{\\qquad}', answer: '\\frac{x^{2}}{8}+\\frac{y^{2}}{6}=1', source: '自编' as const, analysis: '同焦点 ⇒ c=1 不变，待定系数解 a²、b²。', folder_refs: ['fld-1'], usage_count: 6 },
  { id: 'q-ai-1', kp_name: '椭圆焦点弦', kp_code: 'YD-03', q_type: 'solve' as const, difficulty: 'medium' as const, stem_latex: '过椭圆 \\frac{x^{2}}{4}+\\frac{y^{2}}{3}=1 右焦点 F 的弦 AB 中点为 (\\frac{1}{2}, \\frac{3}{4})，求直线 AB 斜率', answer: 'k=\\frac{3}{2}', source: 'AI配题' as const, analysis: '点差法：中点坐标代入作差。', folder_refs: ['fld-1'], usage_count: 3 },
]

/** V3.1：我的专题夹种子（引用式：题目挂树上，夹子存引用） */
export const V3_FOLDERS = [
  { id: 'fld-1', name: '圆锥曲线压轴', desc: '模考与近年高考真题 · 弦长/点差/联立', count: 6, updated_at: '2026-09-02' },
  { id: 'fld-2', name: '易错题集', desc: '作业批改聚类沉淀 · 分类讨论跳步类', count: 2, updated_at: '2026-08-30' },
]

/** V3.1：五件套级联数据源（教材版本 → 章节路径，绑定 kp-tree；V3.3 标记为「知识库目录」来源，可上传教材重建/编辑替换写死示例） */
export const V3_TEXTBOOK_CHAPTERS = {
  provenance: 'preset',
  textbooks: [
    {
      name: '人教A版（2019）', editable: true,
      chapters: [
        { id: 'ch-pick1-1', path: '选择性必修一 ▸ 圆锥曲线 ▸ 椭圆 ▸ 椭圆及其标准方程' },
        { id: 'ch-pick1-2', path: '选择性必修一 ▸ 圆锥曲线 ▸ 椭圆 ▸ 椭圆的简单几何性质' },
        { id: 'ch-pick1-3', path: '选择性必修一 ▸ 圆锥曲线 ▸ 双曲线 ▸ 双曲线及其标准方程' },
        { id: 'ch-pick1-4', path: '选择性必修一 ▸ 圆锥曲线 ▸ 双曲线 ▸ 双曲线的简单几何性质' },
        { id: 'ch-pick1-5', path: '选择性必修一 ▸ 圆锥曲线 ▸ 抛物线 ▸ 抛物线及其标准方程' },
        { id: 'ch-pick1-6', path: '选择性必修一 ▸ 圆锥曲线 ▸ 抛物线 ▸ 抛物线的简单几何性质' },
        { id: 'ch-pick1-7', path: '选择性必修二 ▸ 一元函数的导数及其应用 ▸ 导数的概念及其意义' },
        { id: 'ch-pick1-8', path: '选择性必修二 ▸ 一元函数的导数及其应用 ▸ 导数的运算' },
        { id: 'ch-pick1-9', path: '选择性必修二 ▸ 一元函数的导数及其应用 ▸ 导数在研究函数中的应用' },
      ],
    },
    {
      name: '北师大版（2019）', editable: true,
      chapters: [
        { id: 'ch-bn-1', path: '选择性必修一 ▸ 直线与圆 ▸ 直线与直线相交的关系利用成角' },
        { id: 'ch-bn-2', path: '选择性必修一 ▸ 圆锥曲线 ▸ 椭圆及其方程' },
        { id: 'ch-bn-3', path: '选择性必修一 ▸ 圆锥曲线 ▸ 双曲线及其方程' },
      ],
    },
    {
      name: '苏教版（2019）', editable: true,
      chapters: [
        { id: 'ch-sj-1', path: '选择性必修一 ▸ 圆锥曲线与方程 ▸ 椭圆' },
        { id: 'ch-sj-2', path: '选择性必修一 ▸ 圆锥曲线与方程 ▸ 双曲线' },
        { id: 'ch-sj-3', path: '选择性必修一 ▸ 圆锥曲线与方程 ▸ 抛物线' },
      ],
    },
  ],
}

/** V3.3：mock——上传教材后 AI 重建的知识库目录（real 实现 = 教材 PDF/图片 → 文档版面分析 → 目录树）。章节从此驱动五件套与题目归属。 */
export function buildRebuiltTextbook(fileName: string, version = '人教A版（2019）') {
  return {
    provenance: 'knowledge_base',
    rebuilt_from: fileName,
    textbooks: [
      {
        name: version, editable: true,
        chapters: [
          { id: `kb-${version.slice(0, 2)}-1`, path: '选择性必修一 ▸ 圆锥曲线 ▸ 椭圆 ▸ 椭圆及其标准方程' },
          { id: `kb-${version.slice(0, 2)}-2`, path: '选择性必修一 ▸ 圆锥曲线 ▸ 椭圆 ▸ 直线与椭圆的位置关系' },
          { id: `kb-${version.slice(0, 2)}-3`, path: '选择性必修一 ▸ 圆锥曲线 ▸ 双曲线 ▸ 双曲线及其标准方程' },
          { id: `kb-${version.slice(0, 2)}-4`, path: '选择性必修一 ▸ 圆锥曲线 ▸ 抛物线 ▸ 抛物线及其标准方程' },
          { id: `kb-${version.slice(0, 2)}-5`, path: '选择性必修二 ▸ 一元函数的导数及其应用 ▸ 导数在研究函数中的应用' },
        ],
      },
    ],
    note: `已按《${fileName}》目录重建：识别出 ${5} 个章节节点；目录可在「知识库设置」中调整（改命名/增删/排序）。`,
  }
}

/** P4：题库知识点分类树（模块 → 章 → 知识点；知识点挂 kp_codes 供筛选） */
export const V3_QUIZ_KP_TREE: { id: string; name: string; children: { id: string; name: string; children?: { id: string; name: string; kp_codes: string[] }[]; kp_codes?: string[] }[] }[] = [
  {
    id: 'mod-conic', name: '圆锥曲线', children: [
      { id: 'ch-ellipse', name: '椭圆', children: [
        { id: 'kp-yd1', name: '椭圆标准方程', kp_codes: ['YD-01'] },
        { id: 'kp-yd2', name: '椭圆几何性质', kp_codes: ['YD-02'] },
        { id: 'kp-yd3', name: '焦点弦', kp_codes: ['YD-03'] },
      ] },
      { id: 'ch-hyper', name: '双曲线', children: [{ id: 'kp-sx1', name: '双曲线标准方程', kp_codes: ['SX-01'] }] },
      { id: 'ch-para', name: '抛物线', children: [{ id: 'kp-pw1', name: '抛物线定义与方程', kp_codes: ['PW-01'] }] },
    ],
  },
  {
    id: 'mod-deriv', name: '导数', children: [
      { id: 'ch-deriv', name: '导数应用', children: [
        { id: 'kp-dr2', name: '导数与单调性', kp_codes: ['DR-02'] },
        { id: 'kp-dr3', name: '极值与最值', kp_codes: ['DR-03'] },
        { id: 'kp-dr5', name: '含参讨论', kp_codes: ['DR-05'] },
      ] },
    ],
  },
  {
    id: 'mod-solid', name: '立体几何', children: [
      { id: 'ch-solid', name: '空间几何体', children: [
        { id: 'kp-lt4', name: '截面问题', kp_codes: ['LT-04'] },
        { id: 'kp-lt5', name: '截面判定（图片题）', kp_codes: ['LT-05'] },
      ] },
    ],
  },
]

export const V3_RESOURCES = [
  { id: 'r1', name: '椭圆及其标准方程 · 完整课件', kind: 'deck' as const, subject: '圆锥曲线', chapter: '椭圆及其标准方程', updated_at: '2026-09-02', owner: '李文澜', shared: true },
  { id: 'r2', name: '椭圆细绳实验 · 演示动画', kind: 'deck' as const, subject: '圆锥曲线', chapter: '椭圆及其标准方程', updated_at: '2026-08-28', owner: '李文澜', shared: false },
  { id: 'r3', name: '配方：正方体三点截面（可拖）', kind: 'figure-recipe' as const, subject: '立体几何', chapter: '空间几何体', updated_at: '2026-08-30', owner: '李文澜', shared: true },
  { id: 'r4', name: '配方：椭圆焦点弦动图', kind: 'figure-recipe' as const, subject: '圆锥曲线', chapter: '椭圆及其标准方程', updated_at: '2026-09-01', owner: '备课组', shared: true },
  { id: 'r5', name: '2025 期中试卷 · 高清扫描', kind: 'photo-bank' as const, subject: '综合', chapter: '期中复习', updated_at: '2026-04-20', owner: '教研组', shared: true },
  { id: 'r6', name: '导数单元 · 教案（讲练结合）', kind: 'plan' as const, subject: '导数', chapter: '导数及其应用', updated_at: '2026-08-15', owner: '李文澜', shared: false },
  { id: 'r7', name: '双曲线几何性质 · 课件', kind: 'deck' as const, subject: '圆锥曲线', chapter: '双曲线', updated_at: '2026-09-01', owner: '备课组', shared: true },
  { id: 'r8', name: '含参讨论专题 · 教案', kind: 'plan' as const, subject: '导数', chapter: '导数及其应用', updated_at: '2026-08-29', owner: '李文澜', shared: false },
  { id: 'r9', name: '抛物线光学性质 · 演示', kind: 'deck' as const, subject: '圆锥曲线', chapter: '抛物线', updated_at: '2026-08-25', owner: '教研组', shared: true },
]

export const V3_INSIGHTS = {
  class_id: 'c2-03', class_name: '高二(3)班', avg: 78.4, trend: [72, 74, 73, 76, 78],
  kp_heat: [
    { kp: 'YD-01', name: '椭圆标准方程', mastery: 0.86, delta: 0.04 },
    { kp: 'YD-02', name: '椭圆几何性质', mastery: 0.71, delta: -0.02 },
    { kp: 'YD-03', name: '焦点弦与弦长公式', mastery: 0.58, delta: -0.06 },
    { kp: 'DR-02', name: '导数与单调性', mastery: 0.74, delta: 0.07 },
    { kp: 'DR-03', name: '极值与最值', mastery: 0.69, delta: 0.03 },
    { kp: 'DR-05', name: '含参分类讨论', mastery: 0.47, delta: -0.05 },
    { kp: 'LT-04', name: '空间截面', mastery: 0.62, delta: 0.01 },
  ],
  error_tags: [
    { tag: '步骤缺失', count: 21, trend: 3 },
    { tag: '计算错误', count: 17, trend: -4 },
    { tag: '概念混淆', count: 12, trend: 1 },
    { tag: '审题错误', count: 6, trend: -1 },
    { tag: '方法选择', count: 8, trend: 2 },
    { tag: '表达不规范', count: 9, trend: -2 },
  ],
  watchlist: [
    { name: '孙浩然', note: '连续 3 次作业「概念混淆」，建议安排面批', weak: ['含参分类讨论', '导数与单调性'] },
    { name: '刘一鸣', note: '空白率上升，课堂互动参与度低', weak: ['极值与最值'] },
    { name: '赵启铭', note: '思路正确但书写跳步，适合 B→A 提升', weak: ['焦点弦与弦长公式'] },
  ],
}

export const V3_RECIPES = [
  { id: 'rcp-1', name: '正方体三点截面（中点）', category: 'solid' as const, author: '李文澜', school_shared: true, params: [{ key: 't1', label: 'A→B 点位', min: 0.15, max: 0.85, step: 0.01, def: 0.5 }, { key: 't2', label: 'CC₁ 点位', min: 0.15, max: 0.85, step: 0.01, def: 0.5 }, { key: 't3', label: 'DD₁ 点位', min: 0.15, max: 0.85, step: 0.01, def: 0.5 }], usage_count: 12, note: '讲授「截面形状判断」时用，先让学生猜形状再拖点验证' },
  { id: 'rcp-2', name: '椭圆焦点弦动图', category: 'conic' as const, author: '备课组', school_shared: true, params: [{ key: 'a', label: '半长轴', min: 1, max: 4, step: 0.1, def: 2 }, { key: 'k', label: '直线斜率', min: -3, max: 3, step: 0.1, def: 1 }], usage_count: 31, note: '焦点弦长随斜率变化的动态演示，配弦长公式推导' },
  { id: 'rcp-3', name: '正弦函数振幅相位', category: 'function' as const, author: '李文澜', school_shared: false, params: [{ key: 'a', label: '振幅', min: 0.5, max: 3, step: 0.1, def: 1 }, { key: 'b', label: '角频率', min: 0.5, max: 3, step: 0.1, def: 1 }], usage_count: 8, note: 'y=a·sin(bx+c) 三参数联动课堂引入' },
]

/* ==================== 绘图工作台 · 图形库（P1） ==================== */

export const V3_DRAW_LIBRARY = [
  {
    id: 'fig-1', name: '抛物线焦点弦模型', kind: 'free' as const, author: '备课组', shared: true, updated_at: '2026-04-18 10:22',
    thumb: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80" style="width:100%;height:100%"><line x1="10" y1="68" x2="110" y2="68" stroke="#8b95a7" stroke-width="0.9"/><line x1="60" y1="6" x2="60" y2="74" stroke="#8b95a7" stroke-width="0.9"/><path d="M40,68 Q60,8 80,68" fill="none" stroke="#0f4787" stroke-width="1.8"/><line x1="60" y1="68" x2="72" y2="34" stroke="#c99735" stroke-width="1.4"/><circle cx="60" cy="68" r="2.2" fill="#c99735"/></svg>',
    records: [
      { id: 'r1', kind: 'preset' as const, preset_id: 'conic/parabola', params: { p: 1 }, color: '#0f4787', width: 2.4 },
      { id: 'r2', kind: 'line' as const, a: [1, 0], b: [4, 6], color: '#c99735', width: 2 },
      { id: 'r3', kind: 'point' as const, pos: [1, 0], color: '#c99735', width: 2 },
    ] as import('@/types/teacherV3').V3DrawRecord[],
  },
  {
    id: 'fig-2', name: '正方体截面示意', kind: 'free' as const, author: '李文澜', shared: true, updated_at: '2026-04-15 16:40',
    thumb: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80" style="width:100%;height:100%"><polygon points="38,60 72,60 72,26 38,26" fill="none" stroke="#c3cad6" stroke-width="1"/><polygon points="55,43 89,43 89,9 55,9" fill="none" stroke="#0f4787" stroke-width="1.4"/><line x1="38" y1="60" x2="55" y2="43" stroke="#c3cad6" stroke-width="1"/><line x1="72" y1="60" x2="89" y2="43" stroke="#c3cad6" stroke-width="1"/><line x1="72" y1="26" x2="89" y2="9" stroke="#c3cad6" stroke-width="1"/><polygon points="46,52 82,35 65,17 33,34" fill="rgba(201,151,53,0.18)" stroke="#c99735" stroke-width="1.6"/></svg>',
    records: [
      { id: 'r1', kind: 'preset' as const, preset_id: 'solid/cube-section', params: { t1: 0.5, t2: 0.55, t3: 0.45 }, color: '#0f4787', width: 1.6 },
    ] as import('@/types/teacherV3').V3DrawRecord[],
  },
  {
    id: 'fig-3', name: 'y=a·sin(bx+c)+d', kind: 'fx' as const, author: '李文澜', shared: false, updated_at: '2026-04-12 09:15',
    thumb: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80" style="width:100%;height:100%"><line x1="6" y1="40" x2="114" y2="40" stroke="#8b95a7" stroke-width="0.9"/><path d="M10,40 C20,10 30,10 40,40 S60,70 70,40 90,10 100,40 110,40" fill="none" stroke="#0f4787" stroke-width="1.8"/></svg>',
    expr: 'y=a\\cdot\\sin(bx+c)+d',
  },
]

/* ==================== 识别块（拍照链路 SSE 样例） ==================== */

export const V3_RECOGNIZE_BLOCKS = [
  { type: 'stem' as const, text: '过椭圆 x²/4+y²/3=1 的右焦点 F 作倾斜角为 60° 的直线 l，交椭圆于 A、B 两点，求：(1) |AB|；(2) △AOB 的面积。', confidence: 0.97, editable: true },
  { type: 'figure' as const, image_region: { x: 0.28, y: 0.62, w: 0.45, h: 0.3 }, confidence: 0.88, editable: true },
  { type: 'solution-step' as const, latex: 'l: y=\\sqrt{3}(x-1)', confidence: 0.95, editable: true },
  { type: 'solution-step' as const, latex: '7x^{2}-8x-8=0', confidence: 0.93, editable: true },
  { type: 'solution-step' as const, latex: 'x_{1}+x_{2}=\\frac{8}{7}', confidence: 0.94, editable: true },
  { type: 'solution-step' as const, latex: '|AB|=\\frac{24}{7}', confidence: 0.91, editable: true },
  { type: 'solution-step' as const, latex: 'S=\\frac{6\\sqrt{3}}{7}', confidence: 0.9, editable: true },
]
