/**
 * ResultCard 动作行的**假能力守卫**单测（§70）。
 *
 * 背景缺陷（2026-09-17 交互复验发现）：`canonicalActions()` 无条件包含 `locate`，
 * 渲染循环只跳过 `apply_patch`（无补丁时）——**不跳过多余的 `locate`**。
 * 于是「诚实空态」卡（如 `translate` 且无选区、无证据）上会渲染一个
 * 点击**毫无反应**的「定位原文」按钮（死按钮 / false affordance）。
 *
 * 修复：`hasLocateTarget` 与 `ResearchAgentPanel.onCardAction('locate')` 的可执行条件
 * 逐一对应 —— `write` 且带 `source_locations`，或带 `evidence`；两者皆无则不渲染。
 *
 * 本测试逐条锁定该对应关系，防止回归。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ResultCard from '@/features/research-workspace/agent/ResultCard.vue'
import type { ResearchResult } from '@/features/research-workspace/api/types'

function makeResult(patch: Partial<ResearchResult> = {}): ResearchResult {
  return {
    kind: 'translate',
    status: 'likely',
    summary: '当前没有可翻译的文本。',
    findings: [],
    evidence: [],
    source_locations: [],
    patches: [],
    actions: ['locate', 'add_note'],
    confidence: 0.5,
    ...patch,
  }
}

const evidence = {
  paper_id: 'p-1',
  title: 'Finite Element Methods for Interface Problems',
  authors: 'Zhiqiang Cai',
  year: 2015,
  page: 3,
  section: '2.1',
}

const btnLabels = (w: ReturnType<typeof mount>): string[] =>
  w.findAll('.rw-actions__btn').map((b) => b.text().trim())

function mountCard(result: ResearchResult, mode: 'read' | 'write') {
  return mount(ResultCard, { props: { result, mode } })
}

describe('ResultCard · 定位动作的假能力守卫', () => {
  it('READ · 诚实空态（无证据、无 source_locations）→ 不渲染「定位原文」，但保留「加入笔记」', () => {
    const w = mountCard(makeResult({ kind: 'translate', evidence: [], source_locations: [] }), 'read')
    expect(btnLabels(w)).not.toContain('定位原文')
    expect(btnLabels(w)).toContain('加入笔记')
  })

  it('READ · 有证据 → 渲染「定位原文」（正向不被误伤）', () => {
    const w = mountCard(makeResult({ kind: 'derivation_check', evidence: [evidence] }), 'read')
    expect(btnLabels(w)).toContain('定位原文')
  })

  it('WRITE · latex_error 带 source_locations → 渲染「定位到第 N 行」（正向不被误伤）', () => {
    const w = mountCard(
      makeResult({
        kind: 'latex_error',
        status: 'insufficient',
        source_locations: [{ file: 'notation.tex', line: 5 }],
        actions: ['locate', 'add_note'],
      }),
      'write',
    )
    expect(btnLabels(w)).toContain('定位到第 5 行')
  })

  it('WRITE · 编译通过的成功态（无 source_locations、无证据）→ 不渲染任何定位按钮', () => {
    const w = mountCard(
      makeResult({
        kind: 'latex_error',
        status: 'verified', // latexOk → canonical 为空
        actions: [],
        source_locations: [],
        evidence: [],
      }),
      'write',
    )
    expect(btnLabels(w).some((t) => t.startsWith('定位'))).toBe(false)
  })

  it('WRITE · 无 source_locations 但有证据 → 仍渲染「定位原文」（切文献路径仍有效）', () => {
    const w = mountCard(
      makeResult({ kind: 'suggestion', status: 'insufficient', evidence: [evidence], source_locations: [] }),
      'write',
    )
    expect(btnLabels(w)).toContain('定位原文')
  })

  it('READ · 默认族（无证据）→ 不渲染「查看证据」（同为假能力守卫）', () => {
    const w = mountCard(
      makeResult({ kind: 'derivation_check', status: 'insufficient', evidence: [], actions: ['locate', 'view_evidence', 'add_note'] }),
      'read',
    )
    expect(btnLabels(w)).not.toContain('查看证据')
    expect(btnLabels(w)).not.toContain('定位原文')
  })

  it('READ · 默认族（有证据）→ 「查看证据」与「定位原文」都在（正向不被误伤）', () => {
    const w = mountCard(
      makeResult({ kind: 'derivation_check', evidence: [evidence], actions: ['locate', 'view_evidence', 'add_note'] }),
      'read',
    )
    expect(btnLabels(w)).toContain('查看证据')
    expect(btnLabels(w)).toContain('定位原文')
  })
})
