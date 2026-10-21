/**
 * WP-G · 旧「课件工坊」路由（`/teacher-v3/slides` / name `tv3-slides`）的收敛规则。
 *
 * 产品决定（冻结，不再讨论）：PPT **不是**独立产品线，链路统一收敛为
 *   TeachingSession → TeachingBlueprint → TeachingDeckSpec → PPT Artifact
 * 旧路由只保留为**兼容入口**：
 *   - 能映射到具体 TeachingSession → 进该会话的 Artifact Focus；
 *   - 不能映射 → 回「我的备课」（备课中心）。
 *
 * 本文件**只放纯函数与常量**，不 import 任何组件、不碰业务状态，便于单测。
 * 唯一允许持久化的是「迁移提示是否已展示过」这一个 UI 偏好标志
 * （DECISIONS §7：localStorage 只放 UI 偏好；该标志**绝不**参与任何业务判断）。
 */

/** 一次性迁移提示文案（验收硬编码，不得改写）。 */
export const SLIDES_RETIRED_NOTICE = '课件已整合到备课会话中。'

/** 旧路由身份：必须保持可解析，外部深链 / 已发出通知不得 404。 */
export const SLIDES_COMPAT_PATH = '/teacher-v3/slides'
export const SLIDES_COMPAT_NAME = 'tv3-slides'

/** 「我的备课」= 备课中心（静态路由表内，任何 flag 状态下都存在）。 */
export const PREP_HOME_PATH = '/teacher-v3/prep'

/** 正式 TeachingSession 路由：始终注册，不能依赖历史 shadow-route feature flag。 */
export const PREP_SESSION_PREFIX = '/teacher-v3/prep/session'
export const PREP_SESSION_ROUTE = 'tv3-prep-session'

/**
 * 沉浸式 TeachingSession surface 的路径前缀与 route name。
 * 路径前缀仅用于拼 URL；route name 用于 `router.hasRoute()` 判定该 surface 是否真的注册过
 * （注册条件见 `src/main.js` 的 `VITE_PREP_SURFACE === 'immersive'`）——
 * 用 hasRoute 判定而不是重复读 env，避免"env 开了但路由没注册"时把老师送到 catch-all。
 */
export const PREP_IMMERSIVE_SESSION_PREFIX = '/teacher-v3/prep-immersive'
export const PREP_IMMERSIVE_SESSION_ROUTE = 'tv3-prep-immersive-session'

/** Artifact Focus 不是独立 URL，而是会话内形态：用 query 提示 surface 打开画布。 */
export const ARTIFACT_FOCUS_QUERY = 'focus'
export const ARTIFACT_FOCUS_VALUE = 'artifact'

/** 兼容逃生舱：显式使用冻结的 legacy 五区编辑器（不在一级导航、无任何深链指向）。 */
export const LEGACY_EDITOR_QUERY = 'editor'
export const LEGACY_EDITOR_VALUE = 'legacy'

/** 旧深链里可能出现的 TeachingSession 标识键（可映射）。 */
export const SESSION_QUERY_KEYS: readonly string[] = ['session', 'session_id', 'sessionId']

/**
 * 只标识 artifact/deck、**不能**标识 TeachingSession 的键。
 * 这些键**故意不**当作 session id 用：`prepApi` 只有会话维度端点
 * （`GET /prep/conversations/{sessionId}`，见 api/prepApi.ts），没有任何 deck→session 解析端点，
 * 把 deck id 当 session id 拼进 URL 只会让 surface 拿到 404 并显示错误态——
 * 比落在「我的备课」更糟。所以 deck id 走 query 原样携带，不猜测。
 */
export const ARTIFACT_QUERY_KEYS: readonly string[] = ['deck', 'deck_id', 'artifact', 'artifact_id']

/** 迁移提示的 localStorage 键：命名空间 + 版本化。 */
export const SLIDES_NOTICE_STORAGE_KEY = 'ma.ui.notice.slides-retired.v1'

export type CompatQuery = Record<string, unknown>

/** 跳转目标里的 query：只允许 URL 能承载的标量/标量数组（vue-router LocationQueryRaw 兼容）。 */
export type CompatQueryOut = Record<string, string | number | (string | number)[]>

export interface SlidesCompatTarget {
  path: string
  query: CompatQueryOut
  hash: string
  /** true = 映射到具体 TeachingSession（Artifact Focus）；false = 落到「我的备课」。 */
  mapped: boolean
  /** 便于测试与排障的判定原因。 */
  reason: 'session' | 'no-session'
}

function firstQueryString(query: CompatQuery | null | undefined, keys: readonly string[]): string | null {
  if (!query) return null
  for (const key of keys) {
    const raw = query[key]
    if (typeof raw === 'string' && raw.trim()) return raw.trim()
    if (typeof raw === 'number' && Number.isFinite(raw)) return String(raw)
    if (Array.isArray(raw)) {
      const hit = raw.find((v) => typeof v === 'string' && v.trim())
      if (typeof hit === 'string') return hit.trim()
    }
  }
  return null
}

/** 从旧 URL 的 query 里读出 TeachingSession 标识（没有则 null）。 */
export function readSessionId(query: CompatQuery | null | undefined): string | null {
  return firstQueryString(query, SESSION_QUERY_KEYS)
}

/** 从旧 URL 的 query 里读出 deck/artifact 标识（仅用于原样携带，不做 session 映射）。 */
export function readArtifactId(query: CompatQuery | null | undefined): string | null {
  return firstQueryString(query, ARTIFACT_QUERY_KEYS)
}

/**
 * VNext 产物打开旧编辑器的唯一许可。
 *
 * `SlidesView.vue` 此时只是编辑器壳：真正的来源必须是同一 TeachingSession
 * 已生成的 TeachingDeckSpec。裸 `/slides`、旧 wizard URL、或缺 session/deck
 * 都不能借此进入，避免重新暴露已经退役的旧生成流程。
 */
export function isVNextDeckEditorRoute(query: CompatQuery | null | undefined): boolean {
  return firstQueryString(query, ['mode']) === 'vnext'
    && Boolean(readSessionId(query))
    && Boolean(readArtifactId(query))
}

/** 原样携带 query（去掉已被消费的键）：只丢 null/undefined/对象，绝不静默改写字符串值。 */
export function carryQuery(
  query: CompatQuery | null | undefined,
  omit: readonly string[] = [],
): CompatQueryOut {
  const out: CompatQueryOut = {}
  if (!query) return out
  for (const [key, raw] of Object.entries(query)) {
    if (omit.includes(key)) continue
    if (raw == null) continue
    if (typeof raw === 'string' || typeof raw === 'number') { out[key] = raw; continue }
    if (Array.isArray(raw)) {
      const kept = raw.filter((v): v is string | number => typeof v === 'string' || typeof v === 'number')
      if (kept.length) out[key] = kept
      continue
    }
    // 对象等复杂值无法在 URL 里有意义地承载 → 丢弃（不猜测）
  }
  return out
}

/**
 * **映射规则（唯一入口，可测试）**：
 *  1. query 带 TeachingSession 标识
 *     → 正式 `/teacher-v3/prep/session/{sessionId}?focus=artifact`；
 *  2. 没有 session 标识（含只有 deck/artifact id 的情况）
 *     → `/teacher-v3/prep`，query/hash 原样携带（**不猜测** deck→session）。
 */
export function resolveSlidesCompatTarget(
  query: CompatQuery | null | undefined,
  opts: { hash?: string | null; immersive?: boolean },
): SlidesCompatTarget {
  const hash = typeof opts.hash === 'string' ? opts.hash : ''
  const sessionId = readSessionId(query)

  if (sessionId) {
    return {
      path: `${PREP_SESSION_PREFIX}/${encodeURIComponent(sessionId)}`,
      query: { ...carryQuery(query, SESSION_QUERY_KEYS), [ARTIFACT_FOCUS_QUERY]: ARTIFACT_FOCUS_VALUE },
      hash,
      mapped: true,
      reason: 'session',
    }
  }

  return {
    path: PREP_HOME_PATH,
    query: carryQuery(query),
    hash,
    mapped: false,
    reason: 'no-session',
  }
}

/** 是否显式要求打开冻结的 legacy 编辑器（逃生舱，非产品入口）。 */
export function isLegacyEditorEscape(query: CompatQuery | null | undefined): boolean {
  return firstQueryString(query, [LEGACY_EDITOR_QUERY]) === LEGACY_EDITOR_VALUE
}

/* ── 一次性迁移提示（唯一允许的 localStorage 用法：UI 偏好标志）────────────── */

type ReadableStorage = { getItem(key: string): string | null }
type WritableStorage = { setItem(key: string, value: string): void }

export function slidesRetiredNoticeShown(storage: ReadableStorage | null | undefined): boolean {
  if (!storage) return false
  try {
    return storage.getItem(SLIDES_NOTICE_STORAGE_KEY) === '1'
  } catch {
    // 隐私模式 / 存储被禁：读不到就当作没看过（下次仍会提示，绝不会因此挡住任何功能）
    return false
  }
}

export function markSlidesRetiredNoticeShown(storage: WritableStorage | null | undefined): void {
  if (!storage) return
  try {
    storage.setItem(SLIDES_NOTICE_STORAGE_KEY, '1')
  } catch {
    /* 写不进去只影响"是否再提示一次"，不影响任何业务逻辑 */
  }
}
