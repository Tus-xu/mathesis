/**
 * WP-G · 「课件工坊」退役契约测试。
 *
 * 冻结的产品决定：PPT 不再是独立产品线 ——
 *   TeachingSession → TeachingBlueprint → TeachingDeckSpec → PPT Artifact
 * 旧路由 `/teacher-v3/slides`（name `tv3-slides`）只作为**兼容入口**保留：
 *   - 能映射到 TeachingSession → 该会话的 Artifact Focus；
 *   - 不能映射 → 「我的备课」（`/teacher-v3/prep`）。
 *
 * 本文件只断言上述新行为；legacy `SlidesView.vue` 自身的行为仍由
 * `test/teacher/v3Draw.test.ts` 覆盖（组件未删除、未重构内部）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type RouteRecordRaw, type Router } from 'vue-router'
import fs from 'node:fs'
import path from 'node:path'

import {
  LEGACY_EDITOR_QUERY,
  LEGACY_EDITOR_VALUE,
  PREP_HOME_PATH,
  PREP_SESSION_PREFIX,
  PREP_SESSION_ROUTE,
  PREP_IMMERSIVE_SESSION_PREFIX,
  PREP_IMMERSIVE_SESSION_ROUTE,
  SLIDES_COMPAT_NAME,
  SLIDES_COMPAT_PATH,
  SLIDES_NOTICE_STORAGE_KEY,
  SLIDES_RETIRED_NOTICE,
  carryQuery,
  isVNextDeckEditorRoute,
  isLegacyEditorEscape,
  markSlidesRetiredNoticeShown,
  readArtifactId,
  readSessionId,
  resolveSlidesCompatTarget,
  slidesRetiredNoticeShown,
} from '@/router/slidesCompat'
import { PREP_SURFACE_ROUTE_NAMES, registerPrepSurface } from '@/features/teacher-prep-v2/route/registerPrepSurface'
import { useToastStore } from '@/stores/toast'
import { router as appRouter } from '@/router'

/** legacy 编辑器很重（真实 1400 行 + 全量 API）；逃生舱测试只验证"渲染的是它"，故用 stub。 */
vi.mock('@/pages/teacher-v3/SlidesView.vue', () => ({
  __esModule: true,
  default: { name: 'SlidesViewStub', template: '<div data-testid="legacy-editor-stub" />' },
}))

import SlidesCompatRedirect from '@/pages/teacher-v3/SlidesCompatRedirect.vue'

const SRC_ROOT = path.resolve(process.cwd(), 'src')

/** 兼容路由 + 落点路由的隔离 router（不带 auth 守卫：守卫行为由 src/router/index.js 契约测试覆盖）。 */
function freshRouter(mapped: boolean): Router {
  const routes: RouteRecordRaw[] = [
    { path: SLIDES_COMPAT_PATH, name: SLIDES_COMPAT_NAME, component: SlidesCompatRedirect },
    { path: PREP_HOME_PATH, name: 'tv3-prep', component: { template: '<div data-testid="prep-home" />' } },
    { path: `${PREP_SESSION_PREFIX}/:sessionId`, name: PREP_SESSION_ROUTE, component: { template: '<div data-testid="prep-session" />' } },
  ]
  if (mapped) {
    routes.push({
      path: `${PREP_IMMERSIVE_SESSION_PREFIX}/:sessionId`,
      name: PREP_IMMERSIVE_SESSION_ROUTE,
      component: { template: '<div data-testid="prep-immersive" />' },
    })
  }
  return createRouter({ history: createMemoryHistory(), routes })
}

const Harness = { template: '<router-view />' }

async function landOn(router: Router, target: string, pinia = createPinia()) {
  setActivePinia(pinia)
  // 先导航再挂载 router-view：避免 memory history 初始 location 为空导致的 vue-router 警告噪音
  await router.push(target)
  await router.isReady()
  const wrapper = mount(Harness, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  localStorage.clear()
})

/* ── 1. 旧路由仍可解析（不得 404），鉴权 meta 不变 ───────────────────────── */
describe('WP-G · 旧路由只剩兼容身份', () => {
  it('正式 tv3-slides 路由挂载兼容壳，而不是冻结旧生成器', () => {
    const routerSource = readSrc('router/index.js')
    const slidesRoute = routerSource.match(/\{ path: 'slides'[\s\S]*?\},/)

    expect(slidesRoute?.[0]).toContain("import('@/pages/teacher-v3/SlidesCompatRedirect.vue')")
    expect(slidesRoute?.[0]).not.toContain("import('@/pages/teacher-v3/SlidesView.vue')")
  })

  it('/teacher-v3/slides 仍解析到 route name tv3-slides，requiresRole 等 meta 不变', () => {
    const r = appRouter.resolve(SLIDES_COMPAT_PATH)
    expect(r.name).toBe(SLIDES_COMPAT_NAME)
    expect(r.meta.teacher).toBe(true)
    expect(r.meta.teacherV3).toBe(true)
    expect(r.meta.requiresRole).toBe('teacher')
    expect(String(r.meta.scene)).toMatch(/^teacher\.v3\./)
    expect(r.matched.length).toBeGreaterThan(0)
  })

  it('兼容路由 meta.title 不再宣称「课件工坊」', () => {
    const r = appRouter.resolve(SLIDES_COMPAT_PATH)
    expect(String(r.meta.title)).not.toContain('课件工坊')
  })

  it('PREP_IMMERSIVE_SESSION_ROUTE 与 registerPrepSurface 实际注册的 name 一致', () => {
    const r = createRouter({ history: createMemoryHistory(), routes: [] })
    registerPrepSurface(r)
    expect(r.hasRoute(PREP_IMMERSIVE_SESSION_ROUTE)).toBe(true)
    expect(PREP_SURFACE_ROUTE_NAMES.withSession).toBe(PREP_IMMERSIVE_SESSION_ROUTE)
  })
})

/* ── 2. 映射规则（两种 flag 状态都测） ──────────────────────────────────── */
describe('WP-G · 映射规则 resolveSlidesCompatTarget', () => {
  it('仅真实 VNext session + deck 可以借用旧编辑器壳', () => {
    expect(isVNextDeckEditorRoute({ session: 's-1', deck: 'd-1', mode: 'vnext' })).toBe(true)
    expect(isVNextDeckEditorRoute({ session: 's-1', mode: 'vnext' })).toBe(false)
    expect(isVNextDeckEditorRoute({ deck: 'd-1', mode: 'vnext' })).toBe(false)
    expect(isVNextDeckEditorRoute({ session: 's-1', deck: 'd-1', mode: 'legacy' })).toBe(false)
  })

  it('带 TeachingSession 总是进入正式会话路由的 Artifact Focus', () => {
    const t = resolveSlidesCompatTarget(
      { session: 's-1', deck: 'd-9', topic: '椭圆' },
      { immersive: true, hash: '#p3' },
    )
    expect(t.path).toBe(`${PREP_SESSION_PREFIX}/s-1`)
    expect(t.query.focus).toBe('artifact')
    expect(t.query.deck).toBe('d-9')      // 未被消费的参数原样携带，不丢上下文
    expect(t.query.topic).toBe('椭圆')
    expect(t.query.session).toBeUndefined() // session 已被路径消费，不重复
    expect(t.hash).toBe('#p3')
    expect(t.mapped).toBe(true)
    expect(t.reason).toBe('session')
  })

  it('sessionId / session_id 两种旧写法同样识别', () => {
    for (const q of [{ sessionId: 's-2' }, { session_id: 's-3' }]) {
      expect(readSessionId(q)).toBeTruthy()
      const t = resolveSlidesCompatTarget(q, { immersive: true })
      expect(t.mapped).toBe(true)
    }
  })

  it('不依赖旧沉浸式开关：session 深链仍进入正式会话', () => {
    const t = resolveSlidesCompatTarget({ session: 's-1' }, { immersive: false })
    expect(t.path).toBe(`${PREP_SESSION_PREFIX}/s-1`)
    expect(t.query.focus).toBe('artifact')
    expect(t.mapped).toBe(true)
    expect(t.reason).toBe('session')
  })

  it('只有 deck/artifact id（无法在前端解析到 TeachingSession）→ 「我的备课」，不猜测', () => {
    const t = resolveSlidesCompatTarget({ deck: 'd-1', mode: 'photo' }, { immersive: true })
    expect(t.path).toBe(PREP_HOME_PATH)
    expect(t.query.deck).toBe('d-1')
    expect(t.query.mode).toBe('photo')
    expect(t.mapped).toBe(false)
    expect(t.reason).toBe('no-session')
    expect(readArtifactId({ deck: 'd-1' })).toBe('d-1')
  })

  it('裸深链（无 query）→ 「我的备课」', () => {
    const t = resolveSlidesCompatTarget({}, { immersive: true })
    expect(t.path).toBe(PREP_HOME_PATH)
    expect(t.query).toEqual({})
    expect(t.mapped).toBe(false)
  })

  it('carryQuery 不吞字符串/数字/数组，只丢 null 与对象', () => {
    expect(carryQuery({ a: 'x', b: 2, c: ['y', 'z'], d: null, e: undefined, f: { g: 1 } }))
      .toEqual({ a: 'x', b: 2, c: ['y', 'z'] })
    expect(carryQuery({ a: 'x', b: 'y' }, ['b'])).toEqual({ a: 'x' })
  })

  it('逃生舱判定只认 editor=legacy', () => {
    expect(isLegacyEditorEscape({ [LEGACY_EDITOR_QUERY]: LEGACY_EDITOR_VALUE })).toBe(true)
    expect(isLegacyEditorEscape({ editor: 'v2' })).toBe(false)
    expect(isLegacyEditorEscape({})).toBe(false)
    expect(isLegacyEditorEscape(null)).toBe(false)
  })
})

/* ── 3. 真跳转（组件挂载 + router.replace） ─────────────────────────────── */
describe('WP-G · 兼容路由真的会跳转', () => {
  it('无法映射 → 落到 /teacher-v3/prep，并保留 deck id', async () => {
    const router = freshRouter(false)
    await landOn(router, '/teacher-v3/slides?deck=d-1')
    expect(router.currentRoute.value.path).toBe(PREP_HOME_PATH)
    expect(router.currentRoute.value.query.deck).toBe('d-1')
  })

  it('可映射 → 落到正式会话的 Artifact Focus', async () => {
    const router = freshRouter(true)
    await landOn(router, '/teacher-v3/slides?session=s-1')
    expect(router.currentRoute.value.path).toBe(`${PREP_SESSION_PREFIX}/s-1`)
    expect(router.currentRoute.value.query.focus).toBe('artifact')
  })

  it('真实 VNext deck 不重定向：只把冻结编辑器作为产物编辑壳加载', async () => {
    const router = freshRouter(true)
    const wrapper = await landOn(router, '/teacher-v3/slides?session=s-1&deck=d-9&mode=vnext')
    expect(router.currentRoute.value.path).toBe(SLIDES_COMPAT_PATH)
    expect(wrapper.find('[data-testid="tv3-slides-legacy"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="legacy-editor-stub"]').exists()).toBe(true)
  })

  it('?editor=legacy 不跳转：显式使用冻结的 legacy 编辑器（非产品入口）', async () => {
    const router = freshRouter(true)
    const wrapper = await landOn(router, `/teacher-v3/slides?${LEGACY_EDITOR_QUERY}=${LEGACY_EDITOR_VALUE}&deck=d-1`)
    expect(router.currentRoute.value.path).toBe(SLIDES_COMPAT_PATH)
    expect(wrapper.find('[data-testid="tv3-slides-legacy"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tv3-slides-compat"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="legacy-editor-stub"]').exists()).toBe(true)
  })
})

/* ── 4. 一次性迁移提示 ─────────────────────────────────────────────────── */
describe('WP-G · 一次性迁移提示', () => {
  it('首次落到兼容路由提示一次；同浏览器第二次不再提示；提示可关闭', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const toast = useToastStore()
    const items = () => toast.items as { id: number; text: string }[]

    const r1 = freshRouter(false)
    await landOn(r1, '/teacher-v3/slides', pinia)
    expect(items().map((t) => t.text)).toEqual([SLIDES_RETIRED_NOTICE])
    expect(localStorage.getItem(SLIDES_NOTICE_STORAGE_KEY)).toBe('1')

    // 可关闭（可达性要求：不是只能等它自己消失）
    toast.dismiss(items()[0].id)
    expect(items().length).toBe(0)

    // 同一浏览器第二次进入：不再提示
    const r2 = freshRouter(false)
    await landOn(r2, '/teacher-v3/slides', pinia)
    expect(items().length).toBe(0)
  })

  it('提示标志只是 UI 偏好：清掉标志不影响跳转，也不影响任何业务判定', async () => {
    const storage = new Map<string, string>()
    expect(slidesRetiredNoticeShown({ getItem: (k) => storage.get(k) ?? null })).toBe(false)
    markSlidesRetiredNoticeShown({ setItem: (k, v) => { storage.set(k, v) } })
    expect(slidesRetiredNoticeShown({ getItem: (k) => storage.get(k) ?? null })).toBe(true)

    // storage 不可用（隐私模式）：读失败=当作没看过，写失败不抛
    const broken = {
      getItem() { throw new Error('denied') },
      setItem() { throw new Error('denied') },
    }
    expect(slidesRetiredNoticeShown(broken)).toBe(false)
    expect(() => markSlidesRetiredNoticeShown(broken)).not.toThrow()

    const router = freshRouter(false)
    await landOn(router, '/teacher-v3/slides?deck=d-1')
    expect(router.currentRoute.value.path).toBe(PREP_HOME_PATH)
  })
})

/* ── 5. 源码级回归守卫（导航与深链） ───────────────────────────────────── */
function readSrc(rel: string): string {
  return fs.readFileSync(path.join(SRC_ROOT, rel), 'utf-8')
}

function walk(dir: string, out: string[] = []): string[] {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name)
    if (e.isDirectory()) walk(full, out)
    else if (/\.(vue|ts|js)$/.test(e.name)) out.push(full)
  }
  return out
}

describe('WP-G · 课件工坊不再是一级导航 / 深链', () => {
  it('TeacherV3Layout 的一级导航里没有「课件工坊」，备课入口仍在', () => {
    const text = readSrc('layouts/TeacherV3Layout.vue')
    expect(text).not.toContain('课件工坊')

    // 只扫导航数据：`r === '/teacher-v3/slides'` 这类"我在哪一页"的判定保留
    // （逃生舱里 legacy 编辑器仍在同一 path 上，绘图插入目标标签需要它）
    const menus = text.slice(text.indexOf('const dailyMenus'), text.indexOf('const assessMenus'))
    expect(menus).not.toContain('/teacher-v3/slides')
    expect(menus).toContain('/teacher-v3/prep')
    expect(text).not.toContain("path: '/teacher-v3/slides'")

    // 侧栏副标题表里也不再有旧路由键
    const subs = text.slice(text.indexOf('const subTitles'), text.indexOf('async function refreshTasks'))
    expect(subs).not.toContain('/teacher-v3/slides')
  })

  it('产品页面/组件/mock 不再把用户导航到旧路由（冻结的 SlidesView 自报路由除外）', () => {
    const offenders: string[] = []
    const scopes = [
      path.join(SRC_ROOT, 'pages', 'teacher-v3'),
      path.join(SRC_ROOT, 'components', 'teacherV3'),
      path.join(SRC_ROOT, 'mock'),
    ]
    const navSinks = [
      'to="/teacher-v3/slides"',
      "push('/teacher-v3/slides'",
      "path: '/teacher-v3/slides'",
      "route: '/teacher-v3/slides'",
    ]
    for (const scope of scopes) {
      for (const file of walk(scope)) {
        const rel = path.relative(SRC_ROOT, file).replace(/\\/g, '/')
        // SlidesView.vue 是冻结的 legacy 文件：它向管家上报"自己在哪"，必须如实（不改内部）
        if (rel === 'pages/teacher-v3/SlidesView.vue') continue
        const text = fs.readFileSync(file, 'utf-8')
        for (const sink of navSinks) {
          if (text.includes(sink)) offenders.push(`${rel} → ${sink}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('SlidesView.vue 仍在磁盘上（本包不删除任何文件）', () => {
    expect(fs.existsSync(path.join(SRC_ROOT, 'pages', 'teacher-v3', 'SlidesView.vue'))).toBe(true)
  })
})
