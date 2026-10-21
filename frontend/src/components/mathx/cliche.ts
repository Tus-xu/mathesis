/**
 * mathx/cliche —— 教案"反套话"检测纯逻辑（P3，可单测，不依赖 DOM）
 * 背景：AI 起草教案最常见的问题是灌"套话"——话题无关的通用空话
 * （"培养核心素养""激发学习兴趣"），占篇幅却不含任何可执行的数学内容。
 * 本模块：识别命中 → 标记 → 提供"一键去套话"的确定性改写（原型为本），
 * 把空话替换为围绕课题的可检验具体表述。
 */

export interface ClicheRule {
  /** 命中该空话短语的正则 */
  re: RegExp
  /** 给教师看的命中说明 */
  label: string
}

/** 高中教案高频套话库（按"话题无关空话"标准沉淀） */
export const CLICHE_RULES: ClicheRule[] = [
  { re: /核心素养/, label: '空泛的「核心素养」提法，未落到本课可检验行为' },
  { re: /激发.{0,8}(兴趣|热情|积极性)/, label: '空泛的「激发……兴趣」，未说清用什么激' },
  { re: /(自主|合作).{0,5}(合作|探究)/, label: '空泛的「自主合作探究」，未给具体活动' },
  { re: /体验.{0,10}(形成|发现)过程/, label: '空泛的「体验形成过程」' },
  { re: /寓教于乐/, label: '陈词滥调：「寓教于乐」' },
  { re: /层层递进/, label: '陈词滥调：「层层递进」' },
  { re: /由浅入深/, label: '陈词滥调：「由浅入深」' },
  { re: /难点.*突破|突破.*难点/, label: '「突破难点」未说清难点是什么' },
  { re: /突出重点/, label: '「突出重点」未说清重点是什么' },
  { re: /增强.{0,8}信心/, label: '空泛的「增强信心」' },
  { re: /提高.{0,8}(分析问题|解决问题).{0,4}能力/, label: '口号式能力表述，缺具体题例' },
  { re: /符合.{0,6}认知规律/, label: '空泛的「符合认知规律」' },
  { re: /体现.{0,8}(学生)?主体(地位)?/, label: '口号式「学生主体」' },
  { re: /培养.{0,8}(批判性|创新|逻辑|发散).{0,4}思维/, label: '口号式「培养思维」，缺具体路径' },
  { re: /引导.{0,6}(学生)?(思考|发现|探究|归纳|总结)/, label: '空泛的「引导学生……」，缺具体问题' },
  { re: /加深.{0,8}(理解|印象|记忆)/, label: '空泛的「加深理解」' },
  { re: /鼓励.{0,8}(大胆|积极|勇于)/, label: '空泛的「鼓励学生……」' },
  { re: /为下面.{0,10}(做|作)铺垫/, label: '「为下一步铺垫」缺具体承接点' },
]

/** 返回文本命中的所有套话说明 */
export function detectCliche(text: string): string[] {
  if (!text) return []
  const hits: string[] = []
  for (const r of CLICHE_RULES) {
    if (r.re.test(text)) hits.push(r.label)
  }
  return [...new Set(hits)]
}

/** 是否"套话过重"：命中 ≥2 句，或（命中 1 句且整段基本只剩空话） */
export function isClicheHeavy(text: string): boolean {
  const hits = detectCliche(text)
  if (hits.length >= 2) return true
  if (hits.length === 1) return strip(text).length < 30
  return false
}

/** 去掉空白后的有效长度（判断"整段几乎只剩套话"） */
function strip(t: string): string {
  return (t || '').replace(/\s/g, '')
}

const TOPICS_MAP: Record<string, string> = {
  椭圆: '椭圆及其标准方程',
  双曲线: '双曲线及其标准方程',
  抛物线: '抛物线及其标准方程',
  导数: '导数与函数单调性',
  单调性: '函数的单调性',
  指对: '指数函数与对数函数',
}
function topicOf(topic: string): string {
  const t = topic || '本课题'
  for (const k of Object.keys(TOPICS_MAP)) {
    if (t.includes(k)) return TOPICS_MAP[k]
  }
  return t
}

/**
 * 一键去套话（原型为确定性本地改写）。
 * 把命中的空话段替换为围绕课题、可检验、可执行的具体表述。
 * 真实实现应为"AI 重生成本板块并人工校验"，此处满足原型演示 + 单测。
 */
export function dejargonize(topic: string, boardName: string, text: string): string {
  const hits = detectCliche(text || '')
  if (hits.length === 0) return text
  const t = topicOf(topic)
  const b = boardName || ''
  if (b.includes('目标')) {
    return `围绕「${t}」，给出三条可检验目标：(1) 能用定义/标准式说清 ${t}；(2) 会求 ${t} 的标准方程（含焦点在哪个轴）；(3) 完成一道基础应用并自检。`
  }
  if (b.includes('引入')) {
    return `以「观察 ${t} 中 a、b、c 的角色」这一具体问题切入，把上节课 ${t} 的几何条件与本节课符号定义接上，再抛出待解决的例题。`
  }
  if (b.includes('探究')) {
    return `先让学生完成教材 §2.1 的探究步骤（操作→观察→归纳），再对照定义给出一个能立即验证的数值例（如 a=5、c=3 求 ${t}）。`
  }
  if (b.includes('例题') || b.includes('例')) {
    return `讲「例 1：已知 a 与焦点的位置，求 ${t} 的标准方程」，板书分三步书写并逐行指出所用依据；提示用 $a^2=c^2+b^2$ 检验。`
  }
  if (b.includes('变式')) {
    return `把例 1 变式为「焦点改为在 y 轴」「改由长/短轴端点求 ${t}」两个变式，学生当堂独立完成并互批。`
  }
  if (b.includes('小结') || b.includes('检测')) {
    return `小结 ${t} 的标准式与 a、b、c 关系，随后做 3 道当堂检测题（概念/求标准式/易错各 1），学生自评互评后订正。`
  }
  if (b.includes('作业')) {
    return `分层作业：基础=教材 §2.1 对应练习；提高=给几何条件求 ${t} 标准方程；挑战=含参讨论 ${t} 的图形位置。`
  }
  return `针对「${t}」，本节落实：完成一次师生共析的 ${t} 例题，跟进两道变式自测，并在小结中核对 ${t} 的 a、b、c 关系。`
}