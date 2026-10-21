/**
 * 上下文/提案卡「信息卫生」：教师看到的内容不得含内部码、重复噪声、内部标识符。
 *
 * 背景（2026-09-14 真实截图反馈「乱码」）：
 *   1. `conflicting_evidence` 是后端 warning 码，前端无映射 → 直出英文裸码；
 *   2. 同一 warning 码按不同 ref 重复下发（实测 `no_units` ×10）→ 卡片变复读机；
 *   3. `context_snapshot_hash` 是 64 位十六进制内容指纹，以「上下文摘要」为标题
 *      原样渲染在提案卡上，教师无法理解也无从使用。
 *
 * 另：`cancel_run` / `cancel_session` 曾共用 copy_key → 界面并排两个一样的「取消」，
 * 点错即不可逆结束整个备课会话（真实事故），文案必须可区分。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import StructuredCard from '@/features/teacher-prep-v2/components/cards/StructuredCard.vue'
import MessageItem from '@/features/teacher-prep-v2/surface/MessageItem.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

const HASH64 = '1f7284eb717bdef6383819432575233f8c8c445119c6f9df21eb0bc8821ca4cb'

function mountContext(props: Record<string, unknown>) {
  return mount(StructuredCard, {
    props: { componentType: 'context_card', props, requiresConfirmation: false },
  })
}

describe('上下文卡 · warning 呈现', () => {
  it('conflicting_evidence 走人话，不直出后端码', () => {
    const w = mountContext({ warnings: ['conflicting_evidence'] })
    const text = w.text()
    expect(text).not.toContain('conflicting_evidence')
    expect(text).toContain(copyFor('prep.warning.conflicting_evidence'))
  })

  it('重复的同一 warning 只呈现一次（实测 no_units ×10）', () => {
    const w = mountContext({
      warnings: Array.from({ length: 10 }, () => 'no_units'),
    })
    const items = w.findAll('.structured__item')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toBe(copyFor('prep.warning.no_units'))
  })

  it('不同 warning 各自保留且保持服务端顺序', () => {
    const w = mountContext({
      warnings: ['incomplete_chapter', 'no_units', 'no_units', 'rule_extraction_fallback'],
    })
    const texts = w.findAll('.structured__item').map((n) => n.text())
    expect(texts).toEqual([
      copyFor('prep.warning.incomplete_chapter'),
      copyFor('prep.warning.no_units'),
      copyFor('prep.warning.rule_extraction_fallback'),
    ])
  })

  it('未收录的码原样兜底，不静默丢信息', () => {
    const w = mountContext({ warnings: ['brand_new_code_from_backend'] })
    expect(w.text()).toContain('brand_new_code_from_backend')
  })

  it('不渲染任何 HTML（文本插值路径）', () => {
    const w = mountContext({ warnings: ['no_units'], section_title: '<b>x</b>' })
    expect(w.html()).not.toContain('<b>x</b>')
  })
})

describe('上下文卡 · 内部标识符不外露', () => {
  it('64 位内容指纹（context_snapshot_hash）不得出现在界面', () => {
    const w = mountContext({
      source_title: '人教A版2019 · 必修第一册 · 教师用书 · 必修第一册',
      section_title: '2.3 二次函数与一元二次方程、不等式',
      context_snapshot_hash: HASH64,
      warnings: ['no_units'],
    })
    expect(w.text()).not.toContain(HASH64)
    expect(w.text()).not.toContain(HASH64.slice(0, 12))
  })

  it('snapshot_hash 别名同样不外露', () => {
    const w = mountContext({ snapshot_hash: HASH64 })
    expect(w.text()).not.toContain(HASH64)
  })

  it('教材名与章节名仍正常呈现（去掉指纹不等于去掉上下文）', () => {
    const w = mountContext({
      source_title: '人教A版2019 · 必修第一册 · 教师用书 · 必修第一册',
      section_title: '2.3 二次函数与一元二次方程、不等式',
      context_snapshot_hash: HASH64,
    })
    expect(w.text()).toContain('2.3 二次函数与一元二次方程、不等式')
    expect(w.text()).toContain('必修第一册')
  })
})

describe('两个终态动作的文案必须可区分', () => {
  it('cancel_run / cancel_session 文案不同且都不是裸码', () => {
    const run = copyFor('prep.action.cancel_run')
    const session = copyFor('prep.action.cancel_session')
    expect(run).not.toBe('prep.action.cancel_run')
    expect(session).not.toBe('prep.action.cancel_session')
    expect(run).not.toBe(session)
  })

  it('cancel_run 语义是「中断本次生成」而非结束会话', () => {
    expect(copyFor('prep.action.cancel_run')).toContain('停止')
  })
})

describe('消息卡片标题：不得把上下文卡叫成「AI 提案」', () => {
  function mountMessage(component: { type: string; props: Record<string, unknown> }, runStatus: string | null = null) {
    return mount(MessageItem, {
      props: {
        message: {
          message_id: 'm-1',
          seq: 1,
          role: 'assistant',
          kind: 'context',
          component,
          text: '',
          created_at: new Date(Date.now() - 90_000).toISOString(),
          run_status: runStatus,
          error_label: null,
          editable: false,
        } as never,
        actions: [],
        activeArtifact: null,
      },
    })
  }

  it('context_card 标题说明「已锁定教材与章节」，不是 AI 提案', () => {
    const w = mountMessage({ type: 'context_card', props: { section_title: '2.3 不等式' } })
    const title = w.find('.msg__card-title').text()
    expect(title).toBe(copyFor('prep.msg.card.context_title'))
    expect(title).not.toBe(copyFor('prep.msg.card.fallback_title'))
  })

  it('selection_card 标题不是 AI 提案', () => {
    const w = mountMessage({ type: 'selection_card', props: { kind: 'textbook', options: [] } })
    expect(w.find('.msg__card-title').text()).toBe(copyFor('prep.msg.card.selection_title'))
  })

  it('服务端给了 title 时以服务端为准（业务卡不被覆盖）', () => {
    const w = mountMessage({ type: 'blueprint_card', props: { title: '2.3 二次函数与一元二次方程、不等式' } })
    expect(w.find('.msg__card-title').text()).toBe('2.3 二次函数与一元二次方程、不等式')
  })
})

describe('生成中必须给出真实耗时（区分「在跑」与「卡死」）', () => {
  function mountRunning(runStatus: string, ageSeconds: number) {
    return mount(MessageItem, {
      props: {
        message: {
          message_id: 'm-2',
          seq: 2,
          role: 'user',
          kind: 'text',
          component: { type: 'user_text', props: { text: '选择已确认' } },
          text: '选择已确认',
          created_at: new Date(Date.now() - ageSeconds * 1000).toISOString(),
          run_status: runStatus,
          error_label: null,
          editable: false,
        } as never,
        actions: [],
        activeArtifact: null,
      },
    })
  }

  it('RUNNING 且已等待 90 秒 → 显示「已等待 1 分 30 秒」', () => {
    const w = mountRunning('RUNNING', 90)
    expect(w.find('.msg__status-elapsed').text()).toBe('已等待 1 分 30 秒')
  })

  it('生成中同时给出「在做什么」的说明', () => {
    const w = mountRunning('RUNNING', 30)
    expect(w.find('.msg__status-hint').text()).toBe(copyFor('prep.run.long_hint'))
  })

  it('PENDING（排队中）同样显示耗时', () => {
    const w = mountRunning('PENDING', 20)
    expect(w.find('.msg__status-elapsed').exists()).toBe(true)
  })

  it('非生成态不显示耗时与说明', () => {
    const w = mountRunning('FAILED', 300)
    expect(w.find('.msg__status-elapsed').exists()).toBe(false)
    expect(w.find('.msg__status-hint').exists()).toBe(false)
  })

  it('刚发出（< 5 秒）不闪耗时', () => {
    const w = mountRunning('RUNNING', 2)
    expect(w.find('.msg__status-elapsed').exists()).toBe(false)
  })
})
