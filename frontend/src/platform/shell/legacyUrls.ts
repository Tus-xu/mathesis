/**
 * P01 · 旧直达 URL 兼容层。
 *
 * 背景（P01 指令）：
 * 保留旧 `/teacher-v3/*` 与旧科研 URL 可用性；**不删除旧页面**。
 * 用户在历史通知、收藏、外部链接里保存的地址必须继续可解析。
 *
 * 边界：
 * - 这里只做**路径别名/重写**，不做权限判定（权限在 navigation.ts）；
 * - 未登记的路径**原样返回**，绝不猜测或静默改写（写错会让用户莫名跳走）；
 * - 别名表是数据，供 manifest/测试消费。
 */

/**
 * 旧 URL → 现役 URL 的别名表。
 *
 * 说明：本表登记**路径前缀级**与**精确级**两类别名：
 * - 精确键：整段路径重写；
 * - 以 `*` 结尾的键：前缀匹配，`*` 之后的剩余部分拼接到目标后。
 *
 * 现状（2026-09-11 调研）：
 * - `/teacher-v3/*` 仍是现役路由 → 登记为恒等别名（显式声明"这是受支持的旧形态"）；
 * - `/research/*` 仍是现役路由 → 同上；
 * - 旧 Hub 入口 `/hub` 已拆除，但其历史链接仍需收敛到 workspace 入口。
 */
export const LEGACY_URL_ALIASES: Readonly<Record<string, string>> = Object.freeze({
  // 教师端旧直达：现役路由，登记为恒等，声明兼容承诺（防止未来误删）
  '/teacher-v3/*': '/teacher-v3/*',

  // 科研端：现役路由，同上
  '/research/*': '/research/*',

  // 学生端旧地址（与 src/router/index.js 中的 redirect 保持一致）
  '/overview': '/me?tab=report',
  '/report': '/me?tab=report',
  '/graph': '/me?tab=graph',
  '/profile': '/me?tab=profile',
  '/exam': '/practice?tab=exam',
  '/resource': '/resources',
  '/kb': '/library?tab=materials',
  '/class': '/classroom?tab=class',
  '/tasks': '/classroom?tab=tasks',

  // 已拆除的旧统一入口：收敛到根，由 App Shell 按 AccessContext 决定落地
  '/hub': '/',
})

/**
 * 解析旧 URL 到现役 URL。
 *
 * 匹配优先级：**精确匹配 > 最长前缀匹配**（避免 `/research` 抢走 `/research/reader/x`）。
 * 未命中任何别名 → 原样返回。
 */
export function resolveLegacyUrl(path: string): string {
  if (typeof path !== 'string' || path.length === 0) return path

  // 1) 精确匹配优先
  const exact = LEGACY_URL_ALIASES[path]
  if (typeof exact === 'string') return exact

  // 2) 前缀匹配，取最长者
  let bestKey = ''
  let bestTarget = ''
  for (const [key, target] of Object.entries(LEGACY_URL_ALIASES)) {
    if (!key.endsWith('*')) continue
    const prefix = key.slice(0, -1) // 去掉 '*'
    if (!path.startsWith(prefix)) continue
    if (prefix.length > bestKey.length) {
      bestKey = prefix
      bestTarget = target
    }
  }

  if (bestKey.length === 0) return path

  // 目标同样以 '*' 结尾 → 恒等改写（保留剩余部分）
  const remainder = path.slice(bestKey.length)
  if (bestTarget.endsWith('*')) {
    return bestTarget.slice(0, -1) + remainder
  }
  return bestTarget
}

/** 是否为已登记的旧 URL（供 UI 提示"你访问的是旧地址"）。 */
export function isLegacyUrl(path: string): boolean {
  return resolveLegacyUrl(path) !== path
}
