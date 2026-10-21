/**
 * 课堂播放引擎纯函数（OpenMAIC playback 语义的前端实现）：
 *
 * - splitNarrationSegments：把整页讲稿切成「句级 action 时间轴」——
 *   每句一个播放节拍，并按比例分配该句应写出的板书步骤数（对应 OpenMAIC
 *   的 wb_draw 时序）与应聚光的画面块（对应 spotlight）。
 * - buildFocusKeys：把一页的内容块按渲染顺序展平为聚光目标序列。
 * - estimateSpeakMs：无音频时的兜底节拍（按 CJK 字数估算朗读时长）。
 */

/** 把讲稿切句：先按句末标点，过长的再按逗号/顿号二次切分。
 *  数学感知：$...$ 内部的句读不是句子边界，切中公式会把讲稿腰斩。 */
export function splitSentences(text) {
  const raw = String(text || '').trim()
  if (!raw) return []
  const out = []
  let buf = ''
  let inMath = false
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i]
    if (ch === '$' && raw[i - 1] !== '\\') inMath = !inMath
    buf += ch
    if (!inMath && /[。！？!?；;\n]/.test(ch)) {
      const t = buf.trim()
      if (t) out.push(t)
      buf = ''
    }
  }
  if (buf.trim()) out.push(buf.trim())
  const final = []
  for (const s of out) {
    if (s.length <= 48) {
      final.push(s)
      continue
    }
    // 长句按逗号切（同样数学感知），每片尽量 ≥12 字，避免碎成单字节拍
    let b2 = ''
    let m2 = false
    for (let i = 0; i < s.length; i++) {
      const ch = s[i]
      if (ch === '$' && s[i - 1] !== '\\') m2 = !m2
      b2 += ch
      if (!m2 && b2.trim().length > 12 && /[，,、：:]/.test(ch)) {
        final.push(b2.trim())
        b2 = ''
      }
    }
    if (b2.trim()) final.push(b2.trim())
  }
  return final.length ? final : [raw]
}

/** 无音频兜底节拍：CJK 字符 0.24s/字 + 西文 0.12s/字，下限 2.2s */
export function estimateSpeakMs(text, rate = 1) {
  const s = String(text || '')
  const cjk = (s.match(/[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/g) || []).length
  const rest = s.length - cjk
  return Math.max(2200, Math.round((cjk * 240 + rest * 120) / Math.max(rate, 0.5)))
}

/**
 * 讲稿 → 句级播放时间轴。
 * boardTo：播放完该句后应可见的板书步骤数（板书均摊到句）；
 * 末句收尾保证全部板书写出。
 */
export function buildTimeline(narration, formulaCount = 0) {
  const sentences = splitSentences(narration)
  if (!sentences.length) return []
  const totalWeight = sentences.reduce((a, s) => a + Math.max(s.length, 6), 0)
  let acc = 0
  return sentences.map((text, i) => {
    acc += Math.max(text.length, 6)
    let boardTo = 0
    if (formulaCount > 0) {
      if (i === sentences.length - 1) boardTo = formulaCount
      else boardTo = Math.min(formulaCount, Math.floor((acc / totalWeight) * formulaCount))
    }
    return { text, boardTo }
  })
}

/**
 * 一页内容块按渲染顺序 → 聚光目标 key 序列。
 * key 与课堂页模板中的 data-focus 一一对应：
 * title / t{i}(定理) / p{i}(讲解段) / e{i}(例题) / tb{i}(表格) / n{i}(结论)
 */
export function buildFocusKeys(sections = {}) {
  const keys = ['title']
  for (let i = 0; i < (sections.theorems || []).length; i++) keys.push(`t${i}`)
  for (let i = 0; i < (sections.lecture || []).length; i++) keys.push(`p${i}`)
  for (let i = 0; i < (sections.examples || []).length; i++) keys.push(`e${i}`)
  for (let i = 0; i < (sections.tables || []).length; i++) keys.push(`tb${i}`)
  for (let i = 0; i < (sections.summary || []).length; i++) keys.push(`n${i}`)
  return keys
}

/** 给时间轴每句分配聚光目标（按句子累计权重比例映射到 focusKeys；首句固定聚标题） */
export function attachFocus(timeline, focusKeys) {
  if (!timeline.length || focusKeys.length <= 1) return timeline
  const totalWeight = timeline.reduce((a, s) => a + Math.max(s.text.length, 6), 0)
  let acc = 0
  timeline.forEach((seg, i) => {
    if (i === 0) {
      acc += Math.max(seg.text.length, 6)
      seg.focus = focusKeys[0]
      return
    }
    acc += Math.max(seg.text.length, 6)
    const idx = Math.min(
      focusKeys.length - 1,
      Math.floor((acc / totalWeight) * focusKeys.length),
    )
    seg.focus = focusKeys[idx]
  })
  return timeline
}
