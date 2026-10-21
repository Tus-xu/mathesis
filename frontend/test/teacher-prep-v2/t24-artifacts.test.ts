import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import LessonPlanFocus from '@/features/teacher-prep-v2/artifacts/lesson-plan/LessonPlanFocus.vue'
import TeachingDeckFocus from '@/features/teacher-prep-v2/artifacts/deck/TeachingDeckFocus.vue'
import ConflictResolver from '@/features/teacher-prep-v2/artifacts/shared/ConflictResolver.vue'
import ExportPanel from '@/features/teacher-prep-v2/artifacts/shared/ExportPanel.vue'
import ArtifactFocusCanvas from '@/features/teacher-prep-v2/surface/ArtifactFocusCanvas.vue'
import { readFileSync } from 'node:fs'

describe('T24 typed artifact focus', () => {
  it('edits one lesson block and emits a typed patch without touching other blocks', async () => {
    const wrapper = mount(LessonPlanFocus, { props: {
      title: '教案', revision: 4,
      blocks: [{ id: 'b1', title: '目标', kind: 'objective', content: '原目标' }, { id: 'b2', title: '活动', kind: 'activity', content: '保留活动' }],
      lineage: { blueprintRevision: 3, artifactRevision: 4 },
    } })
    const editors = wrapper.findAll('[data-testid="t24-block-editor"]')
    await editors[0].setValue('新目标')
    await wrapper.findAll('[data-testid="t24-block-propose"]')[0].trigger('click')
    expect(wrapper.emitted('patch')?.[0]?.[0]).toEqual({ blockId: 'b1', before: '原目标', after: '新目标' })
    expect((editors[1].element as HTMLTextAreaElement).value).toBe('保留活动')
  })

  it('edits a target slide content/notes and emits typed slide patches', async () => {
    const wrapper = mount(TeachingDeckFocus, { props: {
      title: '课件', revision: 2, slides: [{ id: 's1', title: '开场', content: '旧内容', notes: '旧备注' }], qaIssues: [], lineage: {},
    } })
    await wrapper.find('[data-testid="t24-slide-content"]').setValue('新内容')
    await wrapper.find('[data-testid="t24-slide-content-propose"]').trigger('click')
    expect(wrapper.emitted('patch')?.[0]?.[0]).toEqual({ slideId: 's1', target: 'content', before: '旧内容', after: '新内容' })
    await wrapper.find('[data-testid="t24-slide-notes"]').setValue('新备注')
    await wrapper.find('[data-testid="t24-slide-propose"]').trigger('click')
    expect(wrapper.emitted('patch')?.[1]?.[0]).toEqual({ slideId: 's1', target: 'notes', before: '旧备注', after: '新备注' })
  })

  it('renders recoverable 409 and capability-disabled exports without fake job progress', () => {
    const conflict = mount(ConflictResolver, { props: { expectedRevision: 3, currentRevision: 4 } })
    expect(conflict.find('[data-testid="t24-conflict-resolver"]').exists()).toBe(true)
    expect(conflict.text()).toContain('3')
    const exportPanel = mount(ExportPanel, { props: { capabilities: [{ format: 'pdf', label: 'PDF', state: 'capability-disabled' }] } })
    expect(exportPanel.find('[data-testid="t24-export-pdf"]').exists()).toBe(false)
    expect(exportPanel.text()).toContain('暂不可用')
  })

  it('maps server-advertised export capabilities and disables only when absent', () => {
    const wrapper = mount(ArtifactFocusCanvas, {
      props: {
        artifact: { artifact_id: 'lp-1', artifact_type: 'lesson_plan_spec', revision: 2, title: '教案', status: 'available', updated_at: null },
        body: { export_capabilities: [{ format: 'pdf', label: 'PDF', state: 'available' }] },
        messages: [], assistantCollapsed: false, draft: '', busy: false, diff: null,
      },
      global: {
        stubs: {
          'n-icon': { template: '<i><slot /></i>' },
          MessageItem: { template: '<div />' },
          ComposerStage: { template: '<div />' },
        },
      },
    })
    const focus = wrapper.findComponent(LessonPlanFocus)
    expect(focus.props('exportCapabilities')).toEqual([{ format: 'pdf', label: 'PDF', state: 'available', downloadUrl: null, fileName: null }])

    const source = readFileSync('src/features/teacher-prep-v2/surface/ArtifactFocusCanvas.vue', 'utf8')
    expect(source).not.toContain('>rev {{ artifact.revision }}<')
    expect(source).toContain("copyWith('prep.t24.revision.label'")
  })
})
