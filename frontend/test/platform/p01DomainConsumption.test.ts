/**
 * P01 · 域消费契约测试（Teacher / Research 各一个最小 harness）。
 *
 * 目的：证明"域可以在**不修改平台层**的前提下消费 WorkspaceLayout + 共享目录"，
 * 且平台层**不反向 import 域代码**。
 *
 * ⚠️ 纪律：本文件**不**伪造班级 / 学生 / 论文 / 证据 / 进度等业务数据。
 * 断言只涉及"结构可组合性"与"边界方向"。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import WorkspaceLayout from '@/platform/workspace-layout/WorkspaceLayout.vue'
import { buildWorkspaceLayout } from '@/platform/workspace-layout/layoutContract'
import { validateLayoutContract } from '@/platform/workspace-layout/layoutContract'
import {
  createCatalog,
  registerComponent,
  lookupComponent,
  renderCatalogNode,
} from '@/platform/component-catalog'
import { PLATFORM_CATALOG_ENTRIES } from '@/platform/component-catalog/catalog'
import { registerWorkspaceHome, resolveWorkspaceHome, __resetWorkspaceHomes } from '@/platform/shell/workspaceManifest'

beforeEach(() => {
  setActivePinia(createPinia())
  __resetWorkspaceHomes()
})

describe('P01 · 域消费平台布局（Teacher）', () => {
  it('teacher-prep-v2 可仅通过 slots 组合 WorkspaceLayout，无需改平台代码', () => {
    const layout = buildWorkspaceLayout()
    expect(validateLayoutContract(layout)).toEqual([])

    const wrapper = mount(WorkspaceLayout, {
      props: { layout },
      slots: {
        left: '<div data-testid="domain:teacher:conversation">T</div>',
        center: '<div data-testid="domain:teacher:artifact">A</div>',
        right: '<div data-testid="domain:teacher:evidence">E</div>',
      },
    })

    expect(wrapper.find('[data-testid="domain:teacher:conversation"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="domain:teacher:artifact"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="domain:teacher:evidence"]').exists()).toBe(true)
  })

  it('teacher-prep-v2 可注册自己的 workspace 首页（平台不硬编码域路由）', () => {
    const ok = registerWorkspaceHome({
      workspace: 'teacher',
      path: '/teacher-v3/today',
      registeredBy: 'teacher-prep-v2@1.0.0',
    })
    expect(ok).toBe(true)
    expect(resolveWorkspaceHome('teacher')).toBe('/teacher-v3/today')
  })

  it('第二个域不得抢占已注册的首页（可追溯，不静默覆盖）', () => {
    registerWorkspaceHome({ workspace: 'teacher', path: '/a', registeredBy: 'first@1.0.0' })
    const second = registerWorkspaceHome({ workspace: 'teacher', path: '/b', registeredBy: 'second@1.0.0' })
    expect(second).toBe(false)
    expect(resolveWorkspaceHome('teacher')).toBe('/a')
  })
})

describe('P01 · 域消费共享目录（Research）', () => {
  it('research-vnext 可注册域组件并按类型查得', () => {
    const catalog = createCatalog()
    PLATFORM_CATALOG_ENTRIES.forEach((e) => registerComponent(catalog, e))

    // 域注册自己的组件（此处只验证登记机制，不放业务实现）
    const registered = registerComponent(catalog, {
      component_type: 'ResearchManuscriptOutline',
      version: '1.0.0',
      domain: 'research',
      props_schema: { type: 'object' },
      event_schema: { type: 'object' },
      risk: 'LOW',
      renderer: { kind: 'REGISTERED_DOMAIN_COMPONENT', component_name: 'ResearchManuscriptOutline' },
      accessibility: { role: 'region' },
      fallback: { strategy: 'TEXT_SUMMARY' },
      registered_by_manifest: 'research-vnext@1.0.0',
    })
    expect(registered).toBe(true)
    expect(lookupComponent(catalog, 'ResearchManuscriptOutline')).toBeDefined()
  })

  it('平台目录条目全部可查得（8 个平台原语）', () => {
    const catalog = createCatalog()
    PLATFORM_CATALOG_ENTRIES.forEach((e) => registerComponent(catalog, e))

    const primitives = [
      'ConversationTimeline',
      'ProposalCard',
      'ConfirmCard',
      'EvidenceRail',
      'SourceLocatorChip',
      'ArtifactCanvas',
      'JobStatusProgress',
      'ErrorEmptyState',
    ]
    for (const p of primitives) {
      expect(lookupComponent(catalog, p), `缺少平台原语 ${p}`).toBeDefined()
    }
  })

  it('模型输出只允许引用已登记类型 + 类型化 props，禁止 HTML/CSS/JS', () => {
    const catalog = createCatalog()
    PLATFORM_CATALOG_ENTRIES.forEach((e) => registerComponent(catalog, e))

    const ok = renderCatalogNode(catalog, { type: 'ProposalCard', props: { title: 'x' } })
    expect(ok.kind).toBe('COMPONENT')

    const bad = renderCatalogNode(catalog, {
      type: 'ProposalCard',
      props: {},
      html: '<b>x</b>',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any)
    expect(bad.kind).toBe('REJECTED')
  })

  it('未知类型降级为 fallback 而不是抛错中断渲染', () => {
    const catalog = createCatalog()
    PLATFORM_CATALOG_ENTRIES.forEach((e) => registerComponent(catalog, e))
    const out = renderCatalogNode(catalog, { type: 'NotRegistered', props: {} })
    expect(out.kind).toBe('FALLBACK')
  })
})

describe('P01 · 边界方向（平台 ↛ 域）', () => {
  it('平台层文件不得 import 任何域 feature 路径', async () => {
    const { readdirSync, readFileSync, statSync } = await import('node:fs')
    const { join } = await import('node:path')

    const roots = [
      'src/platform/shell',
      'src/platform/workspace-layout',
      'src/platform/component-catalog',
      'src/platform/design-system',
      'src/platform/api',
    ]

    const violations: string[] = []
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name)
        if (statSync(p).isDirectory()) { walk(p); continue }
        if (!/\.(ts|vue)$/.test(name)) continue
        const src = readFileSync(p, 'utf8')
        // 平台层唯一允许的跨界 import 是 P00 共享契约（platform-contracts）
        const bad = [...src.matchAll(/from\s+['"]([^'"]+)['"]/g)]
          .map((m) => m[1])
          .filter((spec) => spec.includes('@/') && !spec.includes('@/features/platform-contracts'))
        if (bad.length) violations.push(`${p} -> ${bad.join(', ')}`)
      }
    }
    for (const r of roots) walk(join(process.cwd(), r))

    expect(violations).toEqual([])
  })
})
