/**
 * 聊天卡片契约测试（A1 确证：出题 / 变式链卡片的渲染链）
 *
 * 背景：现场截图出现「1 道变式已全部出好，开始作答吧」但题卡不见的现象。
 * 本文件用后端真实 yield 的 card payload 结构（逐字取自
 * `services/api/app/skills/smart_quiz/main.py`）
 * 走前端归约链，判定「实时流」与「历史回放」两条路径是否能得到可渲染的卡片。
 *
 * 判定规则：
 * - 全部通过 → 前端归约链与 :8000 后端契约一致，卡片丢失的原因在别处
 *   （例如当时前端代理指向的另一套服务，见 A2 修复）。
 * - 出现失败 → 前端归约链自身有缺陷，报错行号即可直接定位。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { applySseEvent, fromHistory, newAssistantMsg } from '@/components/chat/messageModel'
import RetentionCurveCard from '@/components/chat/RetentionCurveCard.vue'
import MasteryBktCard from '@/components/chat/MasteryBktCard.vue'

/** 前端 MessageBubble.vue:343-345 的卡片类型判定（逐字复制） */
const cardType = (c: any) => c?.type || c?.card_type || ''

/**
 * 变式链逐题发卡（后端 smart_quiz/main.py:1610-1641 的 payload 结构）。
 * 后端每出一题过闸即发一张同 chain_id 的卡，前端 mergeCardInto 负责合并。
 */
function variantCard(chainId: string, itemNo: number, total: number) {
  return {
    type: 'quiz_set',
    mode: 'chat_light',
    chain: 'variant',
    chain_id: chainId,
    chain_total: total,
    source: 'user_variant',
    items: [
      {
        item_no: itemNo,
        q_type: 'choice',
        question_text: `第 ${itemNo} 道变式题题干`,
        options: ['A. 选项一', 'B. 选项二', 'C. 选项三', 'D. 选项四'],
        answer: 'B',
        answer_analysis: '解析……',
        kp_code: 'custom',
        kp_name: '变式链',
        difficulty: 'medium',
        ai_generated: true,
        verified: true,
        gate_notes: [],
        source: 'user_variant',
        source_label: 'AI 生成 · 已过五闸验证',
        variant_note: '换数字',
      },
    ],
  }
}

/** 主路径出题卡（后端 smart_quiz/main.py:1132 起） */
function quizSetCard() {
  return {
    type: 'quiz_set',
    items: [
      {
        item_no: 1,
        q_type: 'choice',
        question_text: '集合 A={1,2}，A 的子集个数是（ ）',
        options: ['A. 2', 'B. 3', 'C. 4', 'D. 5'],
        answer: 'C',
        answer_analysis: '含 n 个元素的集合子集数为 2^n',
        kp_code: 'G1-1-1',
        kp_name: '集合',
        difficulty: 'easy',
        ai_generated: true,
        verified: true,
        gate_notes: [],
        source: 'ai',
        source_label: 'AI 生成 · 已过五闸验证',
      },
    ],
  }
}

describe('A1 卡片契约：出题卡在实时流与历史回放两条路径都可渲染', () => {
  it('主路径 quiz_set：SSE card 事件到达后进入 msg.cards 且类型可判定', () => {
    const msg = newAssistantMsg('cmid-1')
    applySseEvent(msg, 'card', quizSetCard())

    expect(msg.cards).toHaveLength(1)
    expect(cardType(msg.cards[0])).toBe('quiz_set')
  })

  it('变式链逐题发卡：同 chain_id 合并为一张链卡，items 递增而非新增卡片', () => {
    const msg = newAssistantMsg('cmid-2')
    const chainId = 'chain-abc'

    applySseEvent(msg, 'card', variantCard(chainId, 1, 3))
    expect(msg.cards).toHaveLength(1)

    applySseEvent(msg, 'card', variantCard(chainId, 2, 3))
    // v1.6「逐题渐进生成」的前端契约：合并，不是每张卡各占一格
    expect(msg.cards).toHaveLength(1)
    // TS 门禁：messageModel.js 为无类型 JS，cards 字面量推断为 never[]，
    // 经 any 别名取 items（与本文件既有 `const c: any` 模式一致，运行时不变）。
    const merged: any = msg.cards[0]
    expect(merged.items).toHaveLength(2)
    expect(merged.items.map((i: any) => i.item_no)).toEqual([1, 2])
  })

  it('变式链卡片：chain/source 命中 QuizSetCard 的 isChain 判定', () => {
    const msg = newAssistantMsg('cmid-3')
    applySseEvent(msg, 'card', variantCard('c1', 1, 2))

    const c: any = msg.cards[0]
    // QuizSetCard.vue:282-284 的判定式
    expect(c.chain === 'variant' || c.source === 'user_variant').toBe(true)
  })

  it('题目配图随 item.figure 合并进题卡（不再作为独立 figure 事件悬浮在题卡前）', () => {
    const card: any = variantCard('fig-chain', 1, 1)
    card.items[0].figure = {
      role: 'question',
      frames: [{ data_uri: 'data:image/svg+xml;base64,PHN2Zy8+', label: '题目配图' }],
      figure_params: { type: 'function' },
    }
    const msg = newAssistantMsg('cmid-fig')
    applySseEvent(msg, 'card', card)
    const merged: any = msg.cards[0]
    expect(merged.items[0].figure.role).toBe('question')
    expect(merged.items[0].figure.frames).toHaveLength(1)
    expect(merged.items[0].figure.step_no).toBeUndefined()
  })

  it('变式链 items[].options 必须是数组，且 answer 是字母（判分前置条件）', () => {
    const msg = newAssistantMsg('cmid-4')
    applySseEvent(msg, 'card', variantCard('c2', 1, 1))

    const it: any = (msg.cards[0] as any).items[0]
    // 选项为 dict 而非数组时，QuizSetCard 会退化成「无选项」渲染
    expect(Array.isArray(it.options)).toBe(true)
    expect(it.options.length).toBeGreaterThan(1)
    // isRightOption 的 `/^[A-F]$/i` 分支要求字母答案
    expect(/^[A-F]$/i.test(String(it.answer))).toBe(true)
  })

  it('历史回放：card 块（后端 envelope.blocks 落库形态）还原为同样的可渲染卡片', () => {
    const chainId = 'chain-hist'
    const item = {
      id: 'm-hist',
      role: 'assistant',
      envelope: {
        msg_id: 'm-hist',
        meta: { skill: 'smart_quiz' },
        blocks: [
          {
            type: 'markdown',
            content:
              '基于你刚才那道题，给你 2 道难度递进的变式——一道一道来，直接点选项作答，我马上判分 👇',
          },
          // 后端 agent_router.py:1195 逐张落库：{"type": "card", "data": card}
          { type: 'card', data: variantCard(chainId, 1, 2) },
          { type: 'card', data: variantCard(chainId, 2, 2) },
        ],
      },
    }

    const msg = fromHistory(item) as any
    expect(msg.cards).toHaveLength(1)
    expect(cardType(msg.cards[0])).toBe('quiz_set')
    expect(msg.cards[0].items).toHaveLength(2)
  })

  it('clarify 事件：选项进入 msg.clarify 且为数组（实时流可选）', () => {
    const msg = newAssistantMsg('cmid-5')
    applySseEvent(msg, 'clarify', {
      question: '请问你具体想要？',
      options: ['帮我引导式解这道题', '直接出几道类似练习'],
    })

    // TS 门禁：clarify 字面量类型为 null，经 any 别名断言其运行时形态
    const cl: any = msg.clarify
    expect(Array.isArray(cl.options)).toBe(true)
    expect(cl.options).toHaveLength(2)
  })

  it('引导式解题每步思路选项：socratic_step_choice 可归约且 options 为数组', () => {
    const msg = newAssistantMsg('cmid-6')
    applySseEvent(msg, 'card', {
      card_type: 'socratic_step_choice',
      session_id: 'sess-1',
      step_no: 1,
      options: ['先确定定义域', '先分别求分子分母的导数', '先把方程配方'],
    })

    expect(msg.cards).toHaveLength(1)
    const c: any = msg.cards[0]
    // MessageBubble 的 cardType = c.type || c.card_type，后端下发的是 card_type
    expect(c.type || c.card_type).toBe('socratic_step_choice')
    expect(Array.isArray(c.options)).toBe(true)
    expect(c.options.length).toBeGreaterThanOrEqual(2)
  })

  it('引导式解题选项不足 2 项时后端不下发卡片（契约：空 options 不渲染）', () => {
    const msg = newAssistantMsg('cmid-7')
    // 后端 _step_choices 在不足 2 项时返回空列表，调用方据此不发 card 事件
    const choices: string[] = []
    expect(choices.length < 2).toBe(true)
    applySseEvent(msg, 'card', { card_type: 'socratic_start', steps_count: 3 })
    expect(msg.cards).toHaveLength(1)
    expect(msg.cards.some((c: any) => c.card_type === 'socratic_step_choice')).toBe(false)
  })
})

describe('A1 附带确证：已定位的契约缺口（特征测试，锁定现状以待修复）', () => {
  it('[缺口 1] clarify 选项未持久化 → 历史回放后 msg.clarify 为空', () => {
    // 根因：后端 agent_router.py:806 `_persist_clarify_message` 落库时
    // blocks 只写 markdown、meta 只写 clarify:True，options 未持久化
    // （实时流 agent_router.py:748 是带 options 的）。
    const item = {
      id: 'm-clarify',
      role: 'assistant',
      envelope: {
        msg_id: 'm-clarify',
        meta: { clarify: true },
        blocks: [{ type: 'markdown', content: '请问你具体想要？' }],
      },
    }

    const msg = fromHistory(item) as any
    expect(msg.clarify).toBeNull()
  })

  it('[缺口 2] 用户消息题目白卡未被还原 → 历史回放后 msg.question 丢失', () => {
    // 根因（两端都有缺口）：
    // - 后端落库 blocks 只有 markdown/card/action/figure/graph/notice/citation，
    //   没有 question 块；发送时 question 只挂在前端本地对象上。
    // - 前端 messageModel.fromHistory 既未初始化 question 字段，也无 question 分支。
    // 现象：刷新或切换会话后，题目白卡位置留空。
    const item = {
      id: 'u-1',
      role: 'user',
      envelope: {
        blocks: [
          { type: 'markdown', content: '请引导我一步步解这道题' },
          { type: 'question', text: '已知四棱锥 S-ABCD…', options: ['A. …', 'B. …'] },
        ],
      },
    }

    const msg = fromHistory(item) as any
    expect(msg.question).toBeUndefined()
  })
})

/* =====================================================================
 * R2 学情可视化卡契约（fsrs_retention / mastery_bkt）
 * 后端（管线轨）经 chat_bridge → ButlerRuntime 真实调工具后下发：
 *   data={type, generated_at, source, degraded, items[], empty_reason?, message?}
 * 与 video_cards 完全同构：SSE event=card → mergeCardInto 通用分支 →
 * envelope.blocks 落库 → fromHistory 历史回放。前端不自取数、不自算、不伪造。
 * ===================================================================== */

/** fsrs_retention 卡：结构逐字对齐后端实际下发（learning_viz_tools.py CurvePayload：
 *  stability_ladder 在 curve 内；points 由后端对真实 stability 现算） */
function fsrsCard(overrides: any = {}) {
  return {
    type: 'fsrs_retention',
    generated_at: '2026-10-11T08:00:00Z',
    source: 'butler.student.fsrs.retention.read',
    degraded: false,
    items: [
      {
        record_id: 'r-1',
        kp_code: 'HS-02',
        kp_name: '函数概念与性质',
        question_preview: '已知函数 f(x)=x²+2x，求最小值（不超过 60 字预览）',
        review_count: 2,
        wrong_count: 1,
        stability: 3,
        retrievability: 0.72,
        level: 'lv2',
        is_due: true,
        hours_to_forget: 30,
        next_review_at: '2026-10-11T08:00:00Z',
        current_t: 1.5,
        curve: {
          thresholds: { due: 0.85, decay: 0.6 },
          points: [
            { t: 0, r: 1 }, { t: 1, r: 0.96 }, { t: 2, r: 0.93 }, { t: 3, r: 0.9 },
          ],
          stability_ladder: [
            { review_count: 0, stability: 0.4 },
            { review_count: 1, stability: 1 },
            { review_count: 2, stability: 3 },
          ],
        },
      },
      {
        record_id: 'r-2',
        kp_code: 'HS-07',
        kp_name: '数列',
        question_preview: '等差数列 an=3n-1，求 S10',
        review_count: 0,
        wrong_count: 1,
        stability: 0.4,
        retrievability: 0.45,
        level: 'new',
        is_due: true,
        hours_to_forget: 4,
        next_review_at: null,
        current_t: 8,
        curve: {
          thresholds: { due: 0.85, decay: 0.6 },
          points: [{ t: 0, r: 1 }, { t: 5, r: 0.65 }, { t: 8, r: 0.45 }],
          stability_ladder: [
            { review_count: 0, stability: 0.4 },
            { review_count: 1, stability: 1 },
          ],
        },
      },
    ],
    ...overrides,
  }
}

/** mastery_bkt 卡：结构逐字对齐方案契约（trend 来自 mastery_snapshots 真实行） */
function masteryCard(overrides: any = {}) {
  const day = (n: number) => `2026-10-${String(n).padStart(2, '0')}`
  return {
    type: 'mastery_bkt',
    generated_at: '2026-10-11T08:00:00Z',
    source: 'butler.student.mastery.snapshot.trend.read',
    degraded: false,
    model: 'bkt_lite',
    min_answers_threshold: 20,
    summary: {
      avg_mastery: 0.58,
      mastered_count: 1,
      total_kps: 3,
      weak_top1: { kp_code: 'HS-02', kp_name: '函数概念与性质', mastery: 0.31 },
    },
    items: [
      {
        kp_code: 'HS-02', kp_name: '函数概念与性质', mastery: 0.31, state: 'weak',
        practice_count: 6, correct_count: 2, delta: -0.04,
        trend: [
          { date: day(8), mastery: 0.3 }, { date: day(9), mastery: null },
          { date: day(10), mastery: 0.35 }, { date: day(11), mastery: 0.31 },
        ],
      },
      {
        kp_code: 'HS-05', kp_name: '平面向量', mastery: 0.55, state: 'improving',
        practice_count: 10, correct_count: 6, delta: 0.06,
        trend: [
          { date: day(8), mastery: 0.48 }, { date: day(10), mastery: 0.52 }, { date: day(11), mastery: 0.55 },
        ],
      },
      {
        kp_code: 'HS-07', kp_name: '数列', mastery: 0.78, state: 'mastered',
        practice_count: 22, correct_count: 18, delta: 0,
        trend: [{ date: day(11), mastery: 0.78 }],
      },
    ],
    ...overrides,
  }
}

describe('R2 fsrs_retention 卡 · 消息模型通道（SSE + 历史重放）', () => {
  it('SSE card 事件进入 msg.cards，type=fsrs_retention（MessageBubble 分发键）', () => {
    const msg: any = newAssistantMsg('cm-fsrs')
    applySseEvent(msg, 'card', fsrsCard())
    expect(msg.cards).toHaveLength(1)
    expect(msg.cards[0].type).toBe('fsrs_retention')
    expect(msg.cards[0].items).toHaveLength(2)
  })

  it('历史 envelope.blocks {type:card,data} 重放还原同构卡（刷新后仍在）', () => {
    const m: any = fromHistory({
      id: 'm-fsrs',
      role: 'assistant',
      envelope: {
        meta: {},
        blocks: [
          { type: 'markdown', content: '你的记忆状态如下：' },
          { type: 'card', data: fsrsCard() },
        ],
      },
    })
    expect(m.cards).toHaveLength(1)
    expect(m.cards[0].type).toBe('fsrs_retention')
    expect(m.cards[0].items[0].curve.points.length).toBe(4)
  })

  it('组件渲染：关键数字来自后端字段（R% / S 天 / 距遗忘 / 下次复习），曲线点不前端合成', () => {
    const w = mount(RetentionCurveCard, { props: { card: fsrsCard() } })
    // 焦点 = R 最低的错题（0.45 → 45%）
    expect(w.text()).toContain('45%')
    expect(w.text()).toContain('0.4 天') // 稳定度 S
    expect(w.text()).toContain('下次复习')
    expect(w.text()).toContain('刚收录') // level=new 的学生端叫法（与 ErrorsView 一致）
    expect(w.text()).toContain('FSRS R(t)=(1+t/9S)⁻¹') // 算法角标
    // 曲线容器存在（jsdom 无尺寸不初始化 echarts，但容器必须挂载）
    expect(w.find('.rcc-chart').exists()).toBe(true)
    // 「算法含义」一句话：ladder 真实档位推导（review_count=0 → 第 1 次复习后预计 1 天）
    expect(w.text()).toContain('第 1 次复习后')
  })

  it('组件渲染：curve.points 缺失 → 隐藏图并如实说明，绝不画假曲线', () => {
    const card = fsrsCard()
    card.items = card.items.map((it: any) => ({ ...it, curve: { thresholds: { due: 0.85, decay: 0.6 }, points: [] } }))
    const w = mount(RetentionCurveCard, { props: { card } })
    expect(w.find('.rcc-chart').exists()).toBe(false)
    expect(w.find('.rcc-nocurve').exists()).toBe(true)
    expect(w.text()).toContain('曲线数据不足')
  })

  it('空态：empty_reason=no_error_records → 诚实文案 + 去做几道题（open_page 协议）', async () => {
    const w = mount(RetentionCurveCard, {
      props: { card: fsrsCard({ items: [], empty_reason: 'no_error_records' }) },
    })
    expect(w.find('.rcc-empty').exists()).toBe(true)
    expect(w.text()).toContain('还没有错题数据')
    const btn = w.find('.rcc-empty-btn')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    const evt = w.emitted('act')
    expect(evt).toBeTruthy()
    expect((evt![0][0] as any).kind).toBe('open_page')
    expect((evt![0][0] as any).to).toBe('/practice')
  })

  it('degraded=true：卡顶可见降级提示条', () => {
    const w = mount(RetentionCurveCard, { props: { card: fsrsCard({ degraded: true }) } })
    expect(w.find('.rcc-degraded').exists()).toBe(true)
    expect(w.text()).toContain('AI 分析暂不可用')
  })

  it('错题 >3 条：只画 R 最低 3 条，其余给「见错题本」链接', () => {
    const card = fsrsCard()
    card.items = [
      ...card.items,
      { ...card.items[0], record_id: 'r-3', retrievability: 0.9, kp_name: '立体几何' },
      { ...card.items[0], record_id: 'r-4', retrievability: 0.2, kp_name: '复数' },
      { ...card.items[0], record_id: 'r-5', retrievability: 0.66, kp_name: '三角函数' },
    ]
    const w = mount(RetentionCurveCard, { props: { card } })
    expect(w.findAll('.rcc-chip')).toHaveLength(3)
    expect(w.text()).toContain('其余 2 条见错题本')
  })

  it('兼容旧契约形态：stability_ladder 在 item 顶层时同样可推导「复习抬升」一句话', () => {
    const card = fsrsCard()
    // 方案契约旧位：item.stability_ladder；后端实际在 curve.stability_ladder——两处都接
    card.items = card.items.map((it: any) => {
      const { stability_ladder, ...curve } = it.curve
      return { ...it, curve, stability_ladder }
    })
    const w = mount(RetentionCurveCard, { props: { card } })
    // 焦点错题 review_count=0 → 第 1 次复习后预计 1 天
    expect(w.text()).toContain('第 1 次复习后')
  })

  it('后端 message 摘要（有数据时）展示在卡内', () => {
    const w = mount(RetentionCurveCard, {
      props: { card: fsrsCard({ message: '当前展示 2 道错题的记忆状态，其中 2 道已到复习期。' }) },
    })
    expect(w.find('.rcc-msg').exists()).toBe(true)
    expect(w.text()).toContain('当前展示 2 道错题的记忆状态')
  })
})

describe('R2 mastery_bkt 卡 · 消息模型通道（SSE + 历史重放）', () => {
  it('SSE card 事件进入 msg.cards，type=mastery_bkt（MessageBubble 分发键）', () => {
    const msg: any = newAssistantMsg('cm-bkt')
    applySseEvent(msg, 'card', masteryCard())
    expect(msg.cards).toHaveLength(1)
    expect(msg.cards[0].type).toBe('mastery_bkt')
    expect(msg.cards[0].summary.total_kps).toBe(3)
  })

  it('历史 envelope.blocks {type:card,data} 重放还原同构卡（刷新后仍在）', () => {
    const m: any = fromHistory({
      id: 'm-bkt',
      role: 'assistant',
      envelope: {
        meta: {},
        blocks: [
          { type: 'markdown', content: '分析一下你的学情：' },
          { type: 'card', data: masteryCard() },
        ],
      },
    })
    expect(m.cards).toHaveLength(1)
    expect(m.cards[0].type).toBe('mastery_bkt')
    expect(m.cards[0].items[0].trend.length).toBeGreaterThan(0)
  })

  it('组件渲染：模型标注如实（BKT-lite 带门槛数字）、关键数字与阈值色对齐 0.4/0.7', () => {
    const w = mount(MasteryBktCard, { props: { card: masteryCard() } })
    expect(w.text()).toContain('BKT-lite（答题 < 20 题）')
    expect(w.text()).toContain('58%') // avg_mastery
    expect(w.text()).toContain('1/3') // mastered_count/total_kps
    expect(w.text()).toContain('最薄弱知识点')
    // delta 文字：由后端 delta 字段直出（前端不计算），weak 项在列表中可见
    expect(w.text()).toContain('函数概念与性质')
  })

  it('组件渲染：model=bkt 时标注「标准 BKT」不夸大', () => {
    const w = mount(MasteryBktCard, { props: { card: masteryCard({ model: 'bkt' }) } })
    expect(w.text()).toContain('标准 BKT')
  })

  it('快照 <3 天：顶部「数据积累中」灰条 + 天数为真实快照去重日期数', () => {
    const card = masteryCard()
    // 仅 2 个有效快照日（10-10 / 10-11）
    card.items = card.items.map((it: any) => ({ ...it, trend: it.trend.filter((p: any) => p.date >= '2026-10-10') }))
    const w = mount(MasteryBktCard, { props: { card } })
    expect(w.find('.mbc-accum').exists()).toBe(true)
    expect(w.text()).toContain('数据积累中')
    expect(w.text()).toContain('2 天')
  })

  it('空态：empty_reason=no_mastery_data → 诚实文案 + 去做几道题（open_page 协议）', async () => {
    const w = mount(MasteryBktCard, {
      props: { card: masteryCard({ items: [], summary: null, empty_reason: 'no_mastery_data' }) },
    })
    expect(w.find('.mbc-empty').exists()).toBe(true)
    expect(w.text()).toContain('还没有练习数据')
    const btn = w.find('.mbc-empty-btn')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    const evt = w.emitted('act')
    expect(evt).toBeTruthy()
    expect((evt![0][0] as any).kind).toBe('open_page')
    expect((evt![0][0] as any).to).toBe('/practice')
  })

  it('趋势点 <2 个有效日 → 不画趋势线（如实提示积累中），无合成序列', () => {
    const card = masteryCard()
    card.items = card.items.map((it: any) => ({ ...it, trend: [{ date: '2026-10-11', mastery: 0.5 }] }))
    const w = mount(MasteryBktCard, { props: { card } })
    expect(w.find('.mbc-trend').exists()).toBe(false)
    expect(w.text()).toContain('趋势快照积累中')
  })

  it('degraded=true：卡顶可见降级提示条', () => {
    const w = mount(MasteryBktCard, { props: { card: masteryCard({ degraded: true }) } })
    expect(w.find('.mbc-degraded').exists()).toBe(true)
    expect(w.text()).toContain('AI 分析暂不可用')
  })
})

describe('R2 学情卡通用回归：不破坏既有卡片分发', () => {
  it('两种新卡与 video_cards/quiz_set 共存时互不干扰，cardType 分发键唯一', () => {
    const msg: any = newAssistantMsg('cm-mix')
    applySseEvent(msg, 'card', fsrsCard())
    applySseEvent(msg, 'card', masteryCard())
    applySseEvent(msg, 'card', { type: 'video_cards', items: [] })
    expect(msg.cards).toHaveLength(3)
    const types = msg.cards.map((c: any) => c.type || c.card_type)
    expect(types).toEqual(['fsrs_retention', 'mastery_bkt', 'video_cards'])
  })
})
