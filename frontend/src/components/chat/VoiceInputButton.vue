<template>
  <!-- 语音输入：先取 asr-token；测试前端不接讯飞 WSS 直连，
       统一提供「文本 → LaTeX」降级面板（to-latex 预览后插入草稿框） -->
  <button class="icon-btn" :disabled="disabled || cooling" title="语音输入（文本转公式）" @click="onClick">
    <UiIcon name="mic" :size="17" />
  </button>

  <teleport to="body">
    <div v-if="open" class="voice-mask" @click.self="close">
      <div class="voice-panel glass-card anim-in">
        <div class="vp-head">
          <span class="vp-title">🎤 语音输入 · 文本转公式</span>
          <button class="vp-close" @click="close">×</button>
        </div>
        <div class="vp-tip">
          实时语音识别在测试前端未接入，这里用文字描述公式，AI 帮你转成 LaTeX。例如：「x 的平方加 2x 减 3」
        </div>
        <textarea
          v-model="text"
          class="textarea vp-ta"
          rows="2"
          maxlength="500"
          placeholder="用文字描述公式…"
          @keydown.enter.exact.prevent="convert"
        ></textarea>
        <div class="vp-actions">
          <button class="btn btn-primary btn-sm" :disabled="!text.trim() || converting" @click="convert">
            <span v-if="converting" class="spinner"></span> 转为公式
          </button>
        </div>

        <div v-if="errorText" class="vp-error">{{ errorText }}</div>

        <template v-if="result">
          <div v-if="result.ambiguous || !result.latex" class="vp-warn">
            ⚠️ {{ result.latex ? '这个公式可能有歧义，请核对后再插入' : '未能识别成公式，请换个说法试试' }}
          </div>
          <div v-if="result.normalized_text" class="vp-norm">识别文本：{{ result.normalized_text }}</div>
          <div v-if="result.latex" class="vp-preview">
            <MarkdownView :text="previewText" />
            <div class="vp-latex-src">{{ result.latex }}</div>
          </div>
          <div class="vp-actions" v-if="result.latex">
            <button class="btn btn-primary btn-sm" @click="insert">插入输入框</button>
            <button class="btn btn-sm" @click="reset">再转一次</button>
          </div>
        </template>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { computed, ref } from 'vue'
import { speechApi } from '@/api'
import { useToastStore } from '@/stores/toast'
import MarkdownView from '@/components/MarkdownView.vue'
import UiIcon from '@/components/common/UiIcon.vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['insert'])

const toast = useToastStore()
const open = ref(false)
const loading = ref(false)
const text = ref('')
const converting = ref(false)
const result = ref(null)
const errorText = ref('')
const cooldownUntil = ref(0)
const now = ref(Date.now())
let coolTimer = null

const cooling = computed(() => now.value < cooldownUntil.value)
const previewText = computed(() => (result.value?.latex ? `$${result.value.latex}$` : ''))

async function onClick() {
  if (props.disabled || cooling.value) return
  loading.value = true
  try {
    // 凭证可取也不假装做 WSS 直连，直接给出文本降级面板
    await speechApi.asrToken()
    toast.info('实时语音未接入测试前端，已切换为文本转公式')
    openPanel()
  } catch (e) {
    if (e?.code === 42901) {
      toast.error('语音输入使用过于频繁，请 60 秒后再试')
      cooldownUntil.value = Date.now() + 60000
      now.value = Date.now()
      clearInterval(coolTimer)
      coolTimer = setInterval(() => {
        now.value = Date.now()
        if (now.value >= cooldownUntil.value) clearInterval(coolTimer)
      }, 1000)
    } else if (e?.code === 42902) {
      toast.error(e.message || 'AI 服务繁忙，请稍后重试') // 轻提示，不禁用
    } else {
      toast.error('语音服务未配置')
      openPanel() // 降级面板仍可用
    }
  } finally {
    loading.value = false
  }
}

function openPanel() {
  open.value = true
  result.value = null
  errorText.value = ''
}

function close() {
  open.value = false
}

function reset() {
  result.value = null
  errorText.value = ''
}

async function convert() {
  const t = text.value.trim()
  if (!t || converting.value) return
  converting.value = true
  errorText.value = ''
  result.value = null
  try {
    const d = await speechApi.toLatex(t)
    if (d && typeof d === 'object' && ('latex' in d || 'ambiguous' in d)) {
      result.value = { latex: d.latex || '', normalized_text: d.normalized_text || '', ambiguous: !!d.ambiguous }
    } else {
      errorText.value = '语音转公式服务返回异常，请稍后再试'
    }
  } catch (e) {
    errorText.value = e?.message || '语音转公式服务暂不可用'
  } finally {
    converting.value = false
  }
}

function insert() {
  if (!result.value?.latex) return
  emit('insert', `$${result.value.latex}$`)
  toast.success('公式已插入输入框')
  close()
}
</script>

<style scoped>
.icon-btn {
  width: 34px; height: 34px; border-radius: var(--radius-md); border: 1px solid transparent;
  background: none; font-size: 16px; cursor: pointer; transition: all 0.15s;
  display: inline-flex; align-items: center; justify-content: center;
}
.icon-btn:hover:not(:disabled) { background: rgba(138, 90, 86, 0.1); }
.icon-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.voice-mask {
  position: fixed; inset: 0; z-index: 1000; background: rgba(15, 23, 42, 0.35);
  backdrop-filter: blur(2px); display: flex; align-items: center; justify-content: center; padding: 20px;
}
.voice-panel { width: 440px; max-width: 100%; padding: 18px 20px; }
.vp-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.vp-title { font-weight: 700; font-size: 15px; }
.vp-close { border: none; background: none; font-size: 18px; cursor: pointer; color: var(--text-muted); }
.vp-tip { font-size: 12px; color: var(--text-muted); margin-bottom: 10px; }
.vp-ta { min-height: 56px; }
.vp-actions { display: flex; gap: 8px; margin-top: 10px; }
.vp-error { margin-top: 10px; font-size: 12px; color: var(--accent-red); }
.vp-warn {
  margin-top: 10px; padding: 8px 12px; border-radius: var(--radius-sm);
  background: #fffbeb; color: #d97706; font-size: 12px;
}
.vp-norm { margin-top: 8px; font-size: 12px; color: var(--text-secondary); }
.vp-preview {
  margin-top: 8px; padding: 10px 12px; border: 1px solid var(--border);
  border-radius: var(--radius-md); background: var(--bg-white);
}
.vp-latex-src { margin-top: 6px; font-size: 11px; color: var(--text-muted); font-family: monospace; word-break: break-all; }
</style>
