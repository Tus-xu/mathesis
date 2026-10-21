/**
 * 课堂设计页语音输入 composable（R14 · 裁定 1）。
 *
 * - 浏览器原生 Web Speech API（`SpeechRecognition` / `webkitSpeechRecognition`），
 *   桌面 Chrome/Edge 真实语音转文字；零后端依赖（后端 FunASR sidecar 未部署，裁定禁止接入）。
 * - 能力探测：运行时不支持 → `supported=false`，调用方**不渲染**麦克风入口
 *   （反空壳铁律：不显示禁用态、不显示「即将支持」）。
 * - 识别结果只在 `isFinal` 时经 `onResult` 回调回填，避免中间结果与输入框 `v-model` 冲突。
 */
import { onBeforeUnmount, ref, shallowRef, type Ref } from 'vue'

/* ── Web Speech API 最小类型（lib.dom 未全量收录，自行声明避免依赖 TS 版本） ── */
interface SpeechRecognitionAlternativeLike {
  transcript: string
}
interface SpeechRecognitionResultLike {
  isFinal: boolean
  length: number
  [index: number]: SpeechRecognitionAlternativeLike
}
interface SpeechRecognitionEventLike {
  resultIndex: number
  results: { length: number; [index: number]: SpeechRecognitionResultLike }
}
interface SpeechRecognitionErrorEventLike {
  error: string
}
interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  start(): void
  stop(): void
  abort(): void
  onresult: ((e: SpeechRecognitionEventLike) => void) | null
  onerror: ((e: SpeechRecognitionErrorEventLike) => void) | null
  onend: (() => void) | null
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike

/** 能力探测：当前运行环境是否提供 Web Speech 识别（构造器存在即视为可用）。 */
function detectSpeechSupport(): boolean {
  if (typeof window === 'undefined') return false
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
  return !!w.SpeechRecognition || !!w.webkitSpeechRecognition
}

/** 识别错误码 → 教师可读中文（产品化文案，不上技术堆栈）。 */
function errorText(code: string): string {
  if (code === 'not-allowed' || code === 'service-not-allowed') return '麦克风权限被拒绝，请在浏览器地址栏允许麦克风后重试'
  if (code === 'no-speech') return '未听到语音，请靠近麦克风再试一次'
  if (code === 'audio-capture') return '未检测到麦克风设备'
  if (code === 'network') return '语音识别服务不可用，请检查网络后重试'
  if (code === 'aborted') return ''
  return `语音识别出错（${code}），请重试`
}

export interface UseSpeechInputOptions {
  /** 每段最终识别文本的回填回调（调用方负责追加到输入框）。 */
  onResult: (text: string) => void
  /** 识别语言（BCP-47），默认中文普通话。 */
  lang?: string
}

export interface SpeechInputState {
  /** 运行环境是否支持（false → 调用方不渲染入口）。 */
  supported: Ref<boolean>
  /** 是否处于录音/识别中。 */
  listening: Ref<boolean>
  /** 最近一次错误的教师可读文案（空串 = 无错误）。 */
  error: Ref<string>
  start: () => void
  stop: () => void
  toggle: () => void
}

/**
 * 语音输入状态机：idle → listening →（onresult 回填）→ idle。
 * 每次开始都新建识别实例（Chrome 对单实例复用有状态残留问题）。
 */
export function useSpeechInput(options: UseSpeechInputOptions): SpeechInputState {
  const supported = ref(detectSpeechSupport())
  const listening = ref(false)
  const error = ref('')
  const rec = shallowRef<SpeechRecognitionLike | null>(null)

  function getCtor(): SpeechRecognitionCtor | null {
    const w = window as unknown as {
      SpeechRecognition?: SpeechRecognitionCtor
      webkitSpeechRecognition?: SpeechRecognitionCtor
    }
    return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
  }

  function start(): void {
    if (!supported.value || listening.value) return
    const Ctor = getCtor()
    if (!Ctor) {
      supported.value = false
      return
    }
    error.value = ''
    const instance = new Ctor()
    instance.lang = options.lang ?? 'zh-CN'
    instance.continuous = false
    instance.interimResults = true

    instance.onresult = (e: SpeechRecognitionEventLike) => {
      let finalText = ''
      for (let i = e.resultIndex; i < e.results.length; i += 1) {
        const result = e.results[i]
        if (result && result.isFinal) {
          for (let j = 0; j < result.length; j += 1) {
            const alt = result[j]
            if (alt?.transcript) finalText += alt.transcript
          }
        }
      }
      const text = finalText.replace(/\s+/g, ' ').trim()
      if (text) options.onResult(text)
    }
    instance.onerror = (e: SpeechRecognitionErrorEventLike) => {
      error.value = errorText(e.error)
    }
    instance.onend = () => {
      listening.value = false
      rec.value = null
    }

    rec.value = instance
    try {
      instance.start()
      listening.value = true
    } catch {
      // InvalidStateError 等：实例未就绪/已启动，按未开始处理
      listening.value = false
      rec.value = null
    }
  }

  function stop(): void {
    if (!listening.value) return
    try {
      rec.value?.stop()
    } catch {
      // 已停止/未启动：忽略
    }
    listening.value = false
  }

  function toggle(): void {
    if (listening.value) stop()
    else start()
  }

  onBeforeUnmount(() => {
    try {
      rec.value?.abort()
    } catch {
      // 卸载路径：静默
    }
    rec.value = null
  })

  return { supported, listening, error, start, stop, toggle }
}
