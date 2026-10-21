import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { applySseEvent, fromHistory, newAssistantMsg } from '@/components/chat/messageModel'
import VideoCardsCard from '@/components/chat/VideoCardsCard.vue'

/**
 * P1 对话内视频推荐卡前端契约自测（迭代20）：
 * - SSE card 事件与 envelope.blocks type=card 历史重放都走 messageModel 通用通道，
 *   卡对象同构（{type:'video_cards', query, source, items[]}）→ MessageBubble 以
 *   cardType(c)==='video_cards' 分发到 VideoCardsCard。
 * - 数据七字段（bvid/title/author/cover/duration/duration_sec/play/url）由后端
 *   bilibili.fetch_videos 下发，前端只渲染：整卡新标签、封面 no-referrer、万/亿、合集。
 */

const ITEM = {
  bvid: 'BV1xx411c7mD',
  title: '二次函数全知识点讲解',
  author: '一数',
  cover: 'https://i0.hdslb.com/bfs/archive/demo.jpg',
  duration: '12:05',
  duration_sec: 725,
  play: 123456,
  url: 'https://www.bilibili.com/video/BV1xx411c7mD',
  match_reason: '按「二次函数」为你挑的B站讲解',
}
const CARD = {
  type: 'video_cards',
  query: '二次函数',
  source: 'bilibili',
  items: [
    ITEM,
    { ...ITEM, bvid: 'BV2demo0000', url: 'https://www.bilibili.com/video/BV2demo0000', title: '数列入门' },
  ],
}

describe('video_cards 卡 · 消息模型通道（SSE + 历史重放）', () => {
  it('SSE card 事件进入 msg.cards，type=video_cards（MessageBubble 分发键）', () => {
    const msg: any = newAssistantMsg('cm')
    applySseEvent(msg, 'card', CARD)
    expect(msg.cards).toHaveLength(1)
    expect(msg.cards[0].type).toBe('video_cards')
    expect(msg.cards[0].items).toHaveLength(2)
  })

  it('历史 envelope.blocks {type:card,data} 重放还原同构卡（刷新后仍在）', () => {
    const m: any = fromHistory({
      id: 'm1',
      role: 'assistant',
      envelope: {
        meta: {},
        blocks: [
          { type: 'markdown', content: '给你挑了几个视频：' },
          { type: 'card', data: CARD },
        ],
      },
    })
    expect(m.cards).toHaveLength(1)
    expect(m.cards[0].type).toBe('video_cards')
    expect(m.cards[0].items[0].bvid).toBe(ITEM.bvid)
    expect(m.cards[0].items[0].url).toBe(ITEM.url)
  })
})

describe('video_cards 卡 · 组件渲染', () => {
  it('每条视频一张整卡链接：新标签 + noopener noreferrer，封面 no-referrer', () => {
    const w = mount(VideoCardsCard, { props: { card: CARD } })
    const links = w.findAll('a.vc-item')
    expect(links).toHaveLength(2)
    expect(links[0].attributes('href')).toBe(ITEM.url) // url 由后端拼好，前端原样用
    expect(links[0].attributes('target')).toBe('_blank')
    expect(links[0].attributes('rel')).toContain('noopener')
    expect(links[0].attributes('rel')).toContain('noreferrer')
    const img = w.find('img')
    expect(img.attributes('src')).toBe(ITEM.cover)
    expect(img.attributes('referrerpolicy')).toBe('no-referrer') // hdslb 防盗链
    expect(w.text()).toContain('二次函数全知识点讲解')
    expect(w.text()).toContain('@一数')
    expect(w.text()).toContain(ITEM.match_reason)
  })

  it('播放量万/亿格式化与 >6h 合集角标（与资源中心同口径）', () => {
    const w = mount(VideoCardsCard, {
      props: {
        card: {
          ...CARD,
          items: [
            { ...ITEM, play: 123456, duration: '12:05', duration_sec: 725 },
            { ...ITEM, bvid: 'BV3', url: 'https://www.bilibili.com/video/BV3', play: 250000000, duration: '176:02:25', duration_sec: 633735 },
          ],
        },
      },
    })
    expect(w.text()).toContain('12.3 万')
    expect(w.text()).toContain('2.5 亿')
    expect(w.text()).toContain('合集')
  })

  it('空 items：出空态文案，不出空卡链接', () => {
    const w = mount(VideoCardsCard, { props: { card: { ...CARD, items: [] } } })
    expect(w.find('.vc-empty').exists()).toBe(true)
    expect(w.findAll('a.vc-item')).toHaveLength(0)
  })

  it('封面加载失败：退回占位，不整卡崩', async () => {
    const w = mount(VideoCardsCard, { props: { card: CARD } })
    await w.find('img').trigger('error')
    await nextTick()
    expect(w.find('.vc-cover-fallback').exists()).toBe(true)
  })
})
