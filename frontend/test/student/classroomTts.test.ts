/**
 * classroomTts 工具函数单测：文本预处理 + 声音选择。
 */
import { describe, expect, it, beforeEach } from 'vitest'
import {
  textToSpeech,
  pickChineseVoice,
  listChineseVoices,
  getPreferredVoice,
  setPreferredVoice,
} from '@/utils/classroomTts'

describe('textToSpeech', () => {
  it('空值返回空串', () => {
    expect(textToSpeech('')).toBe('')
    expect(textToSpeech(null)).toBe('')
    expect(textToSpeech(undefined)).toBe('')
  })

  it('LaTeX 公式块统一替换为"公式"', () => {
    expect(textToSpeech('当 $x > 0$ 时，$y = x^2$ 递增')).toContain('公式')
    expect(textToSpeech('当 $x > 0$ 时，$y = x^2$ 递增')).not.toContain('$')
    expect(textToSpeech('\\(\\frac{1}{2}\\) 是公式')).toContain('公式')
  })

  it('数学符号转口语', () => {
    expect(textToSpeech('a ≤ b')).toBe('a 小于等于 b')
    expect(textToSpeech('a ≥ b')).toBe('a 大于等于 b')
    expect(textToSpeech('a ≠ b')).toBe('a 不等于 b')
    expect(textToSpeech('a ≈ b')).toBe('a 约等于 b')
    expect(textToSpeech('π')).toBe('派')
    expect(textToSpeech('θ 角')).toContain('西塔')
    expect(textToSpeech('α')).toBe('阿尔法')
    expect(textToSpeech('β')).toBe('贝塔')
    expect(textToSpeech('∑')).toBe('求和')
    expect(textToSpeech('A ⊥ B')).toBe('A 垂直 B')
    expect(textToSpeech('A ∥ B')).toBe('A 平行 B')
  })

  it('幂与下标转口语', () => {
    expect(textToSpeech('x²')).toBe('x 平方')
    expect(textToSpeech('x³')).toBe('x 立方')
    expect(textToSpeech('x^5')).toBe('x 的 5 次方')
    expect(textToSpeech('x_1')).toBe('x 下标 1')
  })

  it('运算符加停顿', () => {
    expect(textToSpeech('6×7')).toBe('6 乘以 7')
    expect(textToSpeech('8÷4')).toBe('8 除以 4')
    expect(textToSpeech('3+4')).toContain('加')
  })

  it('标点转停顿，多空格合并', () => {
    expect(textToSpeech('a; b')).toBe('a， b')
    expect(textToSpeech('a  b  c')).toBe('a b c')
  })
})

describe('pickChineseVoice', () => {
  const voices = [
    { name: 'Microsoft David - English (United States)', lang: 'en-US', voiceURI: 'david' },
    { name: 'Microsoft Huihui - Chinese (Simplified, PRC)', lang: 'zh-CN', voiceURI: 'huihui' },
    { name: 'Microsoft Yaoyao - Chinese (Simplified)', lang: 'zh-CN', voiceURI: 'yaoyao' },
    { name: 'Google 普通话（中国大陆）', lang: 'zh-CN', voiceURI: 'google-zh' },
  ]

  it('优先匹配用户偏好', () => {
    const v = pickChineseVoice(voices, 'huihui')
    expect(v?.name).toContain('Huihui')
  })

  it('其次优先 GOOD_CN_HINTS 优质候选（Huihui 先于 Yaoyao/Google）', () => {
    const v = pickChineseVoice(voices)
    // GOOD_CN_HINTS 顺序中 Microsoft Huihui 先于 Google 普通话；Yaoyao 不在候选表
    expect(v?.name).toContain('Huihui')
  })

  it('兜底匹配任意 zh- 开头', () => {
    const onlyEn = [{ name: 'David', lang: 'en-US', voiceURI: 'david' }]
    expect(pickChineseVoice(onlyEn)).toBeNull()
    const mixed = [...onlyEn, ...voices]
    const v = pickChineseVoice(mixed)
    expect(v?.lang).toMatch(/^zh/)
  })

  it('空 voices 数组返回 null', () => {
    expect(pickChineseVoice([])).toBeNull()
    expect(pickChineseVoice(null)).toBeNull()
  })
})

describe('listChineseVoices', () => {
  it('只过滤出中文（zh 开头）', () => {
    const voices = [
      { name: 'David', lang: 'en-US', voiceURI: 'david' },
      { name: 'Huihui', lang: 'zh-CN', voiceURI: 'huihui' },
      { name: '粤语', lang: 'zh-HK', voiceURI: 'hk' },
      { name: '日本語', lang: 'ja-JP', voiceURI: 'jp' },
    ]
    const list = listChineseVoices(voices)
    expect(list).toHaveLength(2)
    expect(list[0].name).toBe('Huihui')
  })
})

describe('preferred voice 持久化', () => {
  beforeEach(() => {
    setPreferredVoice('')
  })

  it('默认无偏好', () => {
    expect(getPreferredVoice()).toBe('')
  })

  it('设置后能读回', () => {
    setPreferredVoice('yaoyao')
    expect(getPreferredVoice()).toBe('yaoyao')
    setPreferredVoice('')
  })
})
