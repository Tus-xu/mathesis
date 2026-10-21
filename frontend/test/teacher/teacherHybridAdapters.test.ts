import { describe, expect, it } from 'vitest'
import {
  adaptLessonPlanSpecToLegacyPlan,
  adaptTeachingDeckSpecToLegacyDeck,
  readLegacyArtifactIdentity,
} from '@/pages/teacher-v3/prep/legacyArtifactAdapter'
import { readFileSync, existsSync } from 'node:fs'

describe('Teacher hybrid spec adapters', () => {
  it('maps LessonPlanSpec content without inventing class or generation state', () => {
    const spec = {
      id: 'lp-1', revision: 4,
      metadata: { title: '函数单调性', lesson_scope: '第 1 课时' },
      objectives: [{ text: '理解单调性' }],
      key_difficult_points: [{ text: '区间判断' }],
      lesson_flow: [{ title: '概念引入', duration_minutes: 8, items: [{ text: '观察图像' }] }],
      projection: { approved_blueprint_revision: 3 },
    }
    const plan = adaptLessonPlanSpecToLegacyPlan(spec)
    expect(plan).toMatchObject({ id: 'lp-1', topic: '函数单调性', class_id: '', objectives: ['理解单调性'] })
    expect(plan.sections[0]).toMatchObject({ name: '概念引入', minutes: 8, teacher_activity: '观察图像' })
    expect(readLegacyArtifactIdentity(spec)).toMatchObject({ id: 'lp-1', revision: 4, lineage: spec.projection })
  })

  it('maps TeachingDeckSpec slides to the legacy canvas as a one-way read-only projection', () => {
    const deck = adaptTeachingDeckSpecToLegacyDeck({
      id: 'deck-1', revision: 2, metadata: { title: '函数课件' },
      slides: [{ id: 'slide-1', type: 'concept', title: '单调性', content: '在区间内讨论变化' }],
    }, '高二一班')
    expect(deck).toMatchObject({ id: 'deck-1', title: '函数课件', brief_context: { material: '高二一班' } })
    expect(deck.slides[0].elements.map((item) => item.type)).toEqual(['text', 'text'])
    expect(deck.slides[0].elements[1]).toMatchObject({ html: '在区间内讨论变化' })
  })

  it('E-3R3: keeps math inequality text raw in element.html (no double escape)', () => {
    const deck = adaptTeachingDeckSpecToLegacyDeck({
      id: 'deck-2', revision: 1, metadata: { title: '椭圆课件' },
      slides: [{ id: 's1', type: 'concept', title: '离心率', content: '轨迹定义要求 0<e<1 且 a>b，故 e=\\sqrt{1-\\frac{b^2}{a^2}}<1' }],
    })
    const content = deck.slides[0].elements.find((el) => el.id === 'vnext-content-1') as { html: string }
    expect(content.html).toContain('0<e<1')
    expect(content.html).not.toContain('&lt;')
    expect(content.html).not.toContain('&amp;')
  })

  it('keeps the deck entry on spec/session identity, out of legacy generation, and no lesson-plan editor', () => {
    const slides = readFileSync('src/pages/teacher-v3/SlidesView.vue', 'utf8')
    // 2026-09-14 备课中心简化：教案编辑器（PrepEditor.vue）已删除，
    // 课件入口改由沉浸式 surface + 自动推进模块负责。
    expect(existsSync('src/pages/teacher-v3/prep/PrepEditor.vue')).toBe(false)
    const surface = readFileSync('src/features/teacher-prep-v2/surface/ImmersivePrepSurface.vue', 'utf8')
    expect(surface).not.toContain('focus: \'lesson-plan\'')
    expect(surface).toContain("mode: 'vnext'")
    expect(surface).toContain('useAutoDeckFlow')
    const autoFlow = readFileSync('src/features/teacher-prep-v2/surface/useAutoDeckFlow.ts', 'utf8')
    expect(autoFlow).toContain("'confirm_proposal'")
    expect(autoFlow).toContain("'project_specs'")
    expect(autoFlow).toContain("'open_deck'")
    expect(slides).toContain("q.mode === 'vnext'")
    expect(slides).toContain('getLegacyTeachingSession')
    expect(slides).toContain('restoreTeachingDeck')
    const recovery = readFileSync('src/features/ppt-studio-v2/recovery/restoreTeachingDeck.ts', 'utf8')
    expect(recovery).toContain('pptStudioApi.ingestDeck')
    expect(recovery).toContain('pptStudioApi.getDeck')
    expect(recovery).toContain('pptStudioApi.listArtifacts')
    expect(recovery).toContain('pptStudioApi.getJobPages')
    expect(recovery).toContain('pptStudioApi.getJobEvents')
    const vnextStart = slides.indexOf("if (q.mode === 'vnext'")
    const legacyStart = slides.indexOf('/* 管家 navigate', vnextStart)
    const restoreStart = slides.indexOf('\n  restoreWorkshopDraft()')
    expect(vnextStart).toBeGreaterThanOrEqual(0)
    expect(legacyStart).toBeGreaterThan(vnextStart)
    expect(vnextStart).toBeLessThan(restoreStart)
    expect(slides.slice(vnextStart, legacyStart)).not.toContain("v3Api.generation.deck(")
    expect(slides).toContain("copyFor('prep.vnext.deck.readonly.title')")
    expect(slides).toContain("copyFor('prep.vnext.deck.restored')")
    expect(slides).not.toContain('当前 8200 编辑/导出能力尚未向浏览器开放')
    expect(slides).not.toContain('VNext TeachingDeckSpec · 只读 ·')
  })

  it('registers every Naive UI component used by the focused prep surface locally', () => {
    const sources = [
      'src/features/teacher-prep-v2/surface/FocusShell.vue',
      'src/features/teacher-prep-v2/surface/ArtifactFocusCanvas.vue',
      'src/features/teacher-prep-v2/surface/ArtifactDock.vue',
      'src/features/teacher-prep-v2/surface/ArtifactReadyCard.vue',
      'src/features/teacher-prep-v2/surface/ComposerStage.vue',
      'src/features/teacher-prep-v2/surface/ContextChips.vue',
      'src/features/teacher-prep-v2/surface/FocusedState.vue',
      'src/features/teacher-prep-v2/surface/MilestoneDrawer.vue',
      'src/features/teacher-prep-v2/surface/drawers/OverlayHost.vue',
    ]
    for (const source of sources) {
      const text = readFileSync(source, 'utf8')
      expect(text).toContain("from 'naive-ui'")
    }
  })
})
