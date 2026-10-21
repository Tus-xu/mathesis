/**
 * mathx/expr —— LaTeX 表达式 → JS 可求值函数（"输入 y=a·sin(bx+c)+d 直接生图"）
 * 零依赖、纯函数可单测。老师只写 LaTeX，不写代码。
 */

export interface ParsedExpr {
  js: string              // 可用 new Function('x','a','b','c','d', ...) 求值
  params: string[]        // 表达式中出现的单字母参数（a-d），自动生成滑杆
  error?: string
}

const FUNCS: [RegExp, string][] = [
  [/\\(?:sin)/g, 'sin'],
  [/\\(?:cos)/g, 'cos'],
  [/\\(?:tan)/g, 'tan'],
  [/\\(?:cot)/g, 'cot'],
  [/\\(?:arcsin)/g, 'asin'],
  [/\\(?:arccos)/g, 'acos'],
  [/\\(?:arctan)/g, 'atan'],
  [/\\(?:ln)/g, 'log'],
  [/\\(?:log)/g, 'log10'],
  [/\\(?:sinh)/g, 'sinh'],
  [/\\(?:cosh)/g, 'cosh'],
]

/** 求值时绑定的数学函数（编译冒烟与 makeFn 共用同一签名，缺一个就是 ReferenceError → 整图 NaN） */
const FN_NAMES = ['sin', 'cos', 'tan', 'cot', 'asin', 'acos', 'atan', 'sqrt', 'exp', 'log', 'log10', 'sinh', 'cosh']
const FN_VALS: ((v: number) => number)[] = [
  Math.sin, Math.cos, Math.tan, (v) => 1 / Math.tan(v), Math.asin, Math.acos, Math.atan,
  Math.sqrt, Math.exp, Math.log, Math.log10, Math.sinh, Math.cosh,
]

/** 最终 js 里合法的多字母标识符（其余多字母粘连 = 隐式乘法，如 bx、Math.PIx、xlog10） */
const KNOWN_IDS = new Set(['Math.PI', 'Math.pow', 'sin', 'cos', 'tan', 'cot', 'asin', 'acos', 'atan', 'sqrt', 'exp', 'log', 'log10', 'sinh', 'cosh'])

function splitUnknownId(tok: string): string {
  if (KNOWN_IDS.has(tok) || tok.length === 1) return tok
  const ids = [...KNOWN_IDS].sort((a, b) => b.length - a.length)
  for (const k of ids) if (tok.startsWith(k)) return k + '*' + splitUnknownId(tok.slice(k.length))
  for (const k of ids) if (tok.endsWith(k)) return splitUnknownId(tok.slice(0, -k.length)) + '*' + k
  return tok.split('').join('*')
}

/**
 * 转换规则（顺序敏感）：
 * \frac{u}{v} → (u)/(v)、\sqrt[n]{u}、\sqrt{u} 定点迭代（嵌套需多轮收敛）
 * 函数裸操作数补括号（\ln x → \ln{x}）；e^{u} → exp(u)
 * \cdot \times → *；\pi → Math.PI；\left \right 删除
 * 空白分隔的操作数 → 隐式乘法；JS 不认 2x/bx/)( 写法 → 补 *
 */
export function parseLatexExpr(raw: string): ParsedExpr {
  let s = String(raw ?? '').trim()
  if (!s) return { js: '', params: [], error: '表达式为空' }
  s = s.replace(/^y\s*=/, '').replace(/^f\s*\(\s*x\s*\)\s*=/, '').trim()
  if (!s) return { js: '', params: [], error: '表达式为空' }

  // 清理无关命令（不动结尾的 }：x^{2} 的闭括号合法）
  s = s.replace(/\\(?:left|right|,|;|!|quad|displaystyle)/g, '')

  // 裸操作数补括号：\ln x、\sin\pi → \ln{x}、\sin{\pi}（否则拼成 logx 这类未定义标识符）
  // 备选顺序长名在前，避免 \sinh 先被 \sin 吃掉
  const BARE_FN = 'sinh|cosh|arcsin|arccos|arctan|sqrt|sin|cos|tan|cot|ln|log'
  s = s.replace(new RegExp(`\\\\(${BARE_FN})\\s*(\\\\pi)`, 'g'), '\\$1{$2}')
  s = s.replace(new RegExp(`\\\\(${BARE_FN})\\s+([a-z0-9])(?![a-z0-9])`, 'g'), '\\$1{$2}')

  // 结构命令定点迭代：frac / sqrt[n] / sqrt 交替重写直到收敛
  let prev = ''
  while (prev !== s) {
    prev = s
    s = s.replace(/\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '(($1)/($2))')
    s = s.replace(/\\sqrt\s*\[([^\[\]]*)\]\s*\{([^{}]*)\}/g, 'Math.pow(($2),1/($1))')
    s = s.replace(/\\sqrt\s*\{([^{}]*)\}/g, 'sqrt($1)')
  }

  // 函数名
  for (const [re, js] of FUNCS) s = s.replace(re, js)
  // e^{u} / e^u（在 ^ 处理前）
  s = s.replace(/\be\s*\^\s*\{([^{}]*)\}/g, 'exp($1)')
  s = s.replace(/\be\s*\^\s*(\\?[a-z0-9])/g, 'exp($1)')

  // 常量与符号
  s = s.replace(/\\pi/g, 'Math.PI').replace(/π/g, 'Math.PI')
  s = s.replace(/\\(?:cdot|times|div)/g, '*')
  s = s.replace(/÷/g, '/').replace(/×/g, '*').replace(/·/g, '*')
  // 空白：操作数之间的空白是隐式乘法（\pi x、a sin(x)），其余删除
  s = s.replace(/([0-9a-zA-Z.)])\s+(?=[0-9a-zA-Z.(])/g, '$1*')
  s = s.replace(/\s+/g, '')

  // x^{2} → x^(2)；x_{1} 下标剥离
  s = s.replace(/\^\{([^{}]*)\}/g, '^($1)')
  s = s.replace(/_\{([^{}]*)\}/g, '')

  // 剩余花括号（分组）→ 括号；^ → **（JS 幂）
  s = s.replace(/\{/g, '(').replace(/\}/g, ')')
  s = s.replace(/\^/g, '**')

  // JS 无隐式乘法：补 *。标识符级处理——未知多字母标识符是变量粘连（bx → b*x），
  // 已知 token（函数名/Math.PI）原样保留；另处理 数字→操作数、)→操作数、单字母→( 三类边界。
  // log10( 是唯一含数字的函数名，负向后顾排除，避免破坏调用
  s = s.replace(/[a-zA-Z][a-zA-Z0-9.]*/g, (tok) => splitUnknownId(tok))
  s = s.replace(/(?<!log1)(?<=[0-9])(?=[a-zA-Z(])/g, '*')
  s = s.replace(/(?<=\))(?=[a-zA-Z0-9(])/g, '*')
  s = s.replace(/(?<=[a-dxI])(?=\()/g, '*')

  const params = [...new Set((s.match(/(?<![a-zA-Z0-9_.])[abcd](?![a-zA-Z0-9_])/g) || []))]

  // 语法冒烟：new Function 编译
  try {
    // eslint-disable-next-line no-new-func
    new Function('x', 'a', 'b', 'c', 'd', ...FN_NAMES, `"use strict";return (${s})`)
  } catch {
    return { js: s, params, error: '表达式无法解析，请检查格式' }
  }
  return { js: s, params }
}

/** 求值（供绘图采样）：失败点返回 NaN 由调用方跳过 */
export function makeFn(parsed: ParsedExpr): (x: number, p: Record<string, number>) => number {
  if (parsed.error || !parsed.js) return () => NaN
  try {
    // eslint-disable-next-line no-new-func
    const f = new Function('x', 'a', 'b', 'c', 'd', ...FN_NAMES, `"use strict";return (${parsed.js})`) as
      (x: number, a: number, b: number, c: number, d: number, ...m: unknown[]) => number
    return (x, p) => {
      try {
        return f(x, p.a ?? 1, p.b ?? 1, p.c ?? 0, p.d ?? 0, ...FN_VALS)
      } catch { return NaN }
    }
  } catch {
    return () => NaN
  }
}
