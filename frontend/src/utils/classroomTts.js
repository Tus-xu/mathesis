/**
 * 双师课堂朗读（OpenMAIC 拟人 TTS 风格）。
 *
 * 浏览器原生 speechSynthesis 的「机械感」主要源于：
 *  1. 直接使用默认 voice，未挑选优质中文女声
 *  2. 文本含数学符号（x²、∠、f(x)）被字面朗读成"x²"
 *  3. 音调 pitch=1.0 太冷、rate=1.0 偏快、句子之间无停顿
 *
 * 改进：
 *  - 启动时异步加载 voices（2s 超时），挑选 zh-CN 优质女声候选
 *  - 文本预处理：把常见数学符号替换成自然口语（"x²" → "x 平方"）
 *  - **SSML 节奏注入**：在句号/逗号处加 <break> 标签，模拟真人语速与停顿
 *  - 默认 pitch 1.08 让声音稍微"亮"一些；rate 默认 0.92（更柔和、不机械）
 *  - 暴露 voice 选择 UI 供用户切换
 *  - 兼容旧浏览器（不支持 SSML 时自动降级为纯文本 + 标点停顿）
 */

const STORAGE_KEY = 'classroom.tts.preferred'
const VOICE_LOAD_TIMEOUT = 2000

/** 文本预处理：把数学符号/代码转口语，避免被机械地字面朗读。 */
export function textToSpeech(raw) {
  if (!raw) return ''
  let t = String(raw)
  // LaTeX 公式块 $[...]$ / \(...\) 整体替换为"公式"
  t = t.replace(/\$\$[^$]+\$\$/g, '公式')
  t = t.replace(/\$[^$]+\$/g, '公式')
  t = t.replace(/\\\((.+?)\\\)/g, '公式')
  t = t.replace(/\\\[(.+?)\\\]/g, '公式')
  // 常见数学符号
  t = t.replace(/≤/g, '小于等于')
  t = t.replace(/≥/g, '大于等于')
  t = t.replace(/≠/g, '不等于')
  t = t.replace(/≈/g, '约等于')
  t = t.replace(/∑/g, '求和')
  t = t.replace(/∏/g, '连乘')
  t = t.replace(/∫/g, '积分')
  t = t.replace(/∞/g, '无穷大')
  t = t.replace(/→/g, '趋向')
  t = t.replace(/∈/g, '属于')
  t = t.replace(/∠/g, '角')
  t = t.replace(/⊥/g, '垂直')
  t = t.replace(/∥/g, '平行')
  t = t.replace(/π/g, '派')
  t = t.replace(/θ/g, '西塔')
  t = t.replace(/α/g, '阿尔法')
  t = t.replace(/β/g, '贝塔')
  // x² x³ x^n → "x 平方"、"x 立方"、"x 的 n 次方"
  t = t.replace(/([A-Za-z0-9])²/g, '$1 平方')
  t = t.replace(/([A-Za-z0-9])³/g, '$1 立方')
  t = t.replace(/([A-Za-z0-9])\^(\d+)/g, '$1 的 $2 次方')
  // x_1 → "x 下标 1"
  t = t.replace(/([A-Za-z])_(\d+)/g, '$1 下标 $2')
  // f(x) y=ax+b → 保留但加点停顿
  t = t.replace(/([A-Za-z])=([A-Za-z0-9])/g, '$1 等于 $2')
  t = t.replace(/\+/g, ' 加 ')
  t = t.replace(/·/g, ' 乘以 ')
  t = t.replace(/×/g, ' 乘以 ')
  t = t.replace(/÷/g, ' 除以 ')
  t = t.replace(/\//g, ' 除以 ')
  t = t.replace(/√/g, '根号')
  // 标点 → 停顿符号（SSML 不好使，浏览器 TTS 通常忽略，用逗号代替）
  t = t.replace(/[;:]/g, '，')
  t = t.replace(/\n+/g, '。')
  // 多空格合并
  t = t.replace(/\s{2,}/g, ' ').trim()
  return t
}

/** 把处理后的文本包装为 SSML（在句号/逗号处插入 <break> 让节奏更接近真人）。
 *
 * 多数现代浏览器（Edge/Chrome/Safari 14+）支持 SSML，
 * 旧浏览器/不支持 SSML 的引擎会直接朗读 <break> 标签的文本（不是崩溃），
 * 所以下方有一个 ssmlSupported 标志来降级。
 */
export function toSSML(text) {
  if (!text) return ''
  // 句号 → 长停顿；问号/叹号 → 中等停顿；逗号/分号 → 短停顿
  // 短停顿 250ms、中等 400ms、长 600ms
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return (
    '<speak>' +
    escaped
      .replace(/。/g, '。<break time="600ms"/>')
      .replace(/！/g, '！<break time="400ms"/>')
      .replace(/？/g, '？<break time="400ms"/>')
      .replace(/，/g, '，<break time="220ms"/>') +
    '</speak>'
  )
}

/** 异步加载 voices（2s 超时），返回数组；旧浏览器返回 []。 */
export function ensureVoices(timeoutMs = VOICE_LOAD_TIMEOUT) {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
    return Promise.resolve([])
  }
  const synth = window.speechSynthesis
  const got = synth.getVoices()
  if (got && got.length) return Promise.resolve(got)
  return new Promise((resolve) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      try { synth.removeEventListener('voiceschanged', onChange) } catch { /* 忽略 */ }
      resolve(synth.getVoices() || [])
    }
    const onChange = () => {
      const v = synth.getVoices()
      if (v && v.length) finish()
    }
    try { synth.addEventListener('voiceschanged', onChange) } catch { /* 忽略 */ }
    setTimeout(finish, timeoutMs)
  })
}

/** 给中文朗读挑选"拟人"声音。优先：用户偏好 > 优质女声候选 > 系统默认中文。
 *
 * 优质候选按"自然度"由高到低排序：
 *  - Microsoft Edge 的 Neural voices（晓晓、晓伊、晓涵、晓晓 2 等）最自然
 *  - macOS 的 Tingting / Sin-ji
 *  - Google 普通话（Chrome 上的网络语音）
 *  - 其它 zh 开头 */
const GOOD_CN_HINTS = [
  // === Windows Edge Neural voices（最自然） ===
  'Microsoft Xiaoxiao Online',
  'Microsoft Xiaoyi Online',
  'Microsoft Xiaohan Online',
  'Microsoft Xiaomeng Online',
  'Microsoft Xiaomo Online',
  'Microsoft Xiaorui Online',
  'Microsoft Yunxi Online',
  'Microsoft Yunjian Online',
  'Microsoft Xiaoxiao',
  'Microsoft Xiaoyi',
  'Microsoft Huihui',
  'Microsoft Tracy',
  // === Windows OneCore voices（Win10/11 自带，比 ms-/desktop 真人感强）===
  'Microsoft Zira',
  // === macOS voices ===
  'Tingting',
  'Sin-ji',
  // === Android / Google voices（Chrome 上拉网络语音）===
  'Google 普通话',
  'Google 中文',
  'Google 中文（中国大陆）',
  'Google 中文（香港）',
  'Google 中文（台灣）',
  // === Linux（部分发行版的 espeak）===
  'cmn-Hans-CN',
  'zh-CN',
]

export function pickChineseVoice(voices, preferred) {
  if (!voices || !voices.length) return null
  // 1. 用户偏好
  if (preferred) {
    const hit = voices.find((v) => v.voiceURI === preferred || v.name === preferred)
    if (hit) return hit
  }
  // 2. 优质候选（按顺序匹配 name/localService/voiceURI）
  for (const hint of GOOD_CN_HINTS) {
    const hit = voices.find(
      (v) =>
        (v.name || '').includes(hint) ||
        (v.voiceURI || '').includes(hint) ||
        (v.lang || '').toLowerCase() === hint.toLowerCase(),
    )
    if (hit) return hit
  }
  // 3. 任意 zh 开头
  return voices.find((v) => (v.lang || '').toLowerCase().startsWith('zh')) || null
}

/** 列出可选中文 voice（供 UI 切换） */
export function listChineseVoices(voices) {
  if (!voices) return []
  return voices.filter((v) => (v.lang || '').toLowerCase().startsWith('zh'))
}

export function getPreferredVoice() {
  try { return localStorage.getItem(STORAGE_KEY) || '' } catch { return '' }
}

export function setPreferredVoice(name) {
  try {
    if (name) localStorage.setItem(STORAGE_KEY, name)
    else localStorage.removeItem(STORAGE_KEY)
  } catch { /* 无痕模式忽略 */ }
}

/** 探测当前 speechSynthesis 是否支持 SSML（注入 <break> 等）。
 *
 * 检测方法：尝试用 SSML 构造一个 utterance，如果浏览器接受则 onstart 会被触发。
 * 出于性能考虑，默认采用"乐观"策略：现代浏览器基本都支持；
 * 若浏览器对 SSML 静默（无停顿但不报错），体验也不至于比 plain text 差。
 * 用户在 UI 中可选"启用 SSML"。
 */
export function detectSsmlSupport() {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return false
  // 简单启发式：Chrome / Edge / Safari 14+ 都支持；Firefox 不支持
  const ua = navigator.userAgent || ''
  if (/Firefox\//.test(ua)) return false
  if (/Chrome\//.test(ua) || /Edg\//.test(ua) || /Safari\//.test(ua) || /AppleWebKit\//.test(ua)) return true
  return false
}
