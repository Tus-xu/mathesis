<template>
  <Transition name="dock">
    <div v-if="open" class="mcd" data-testid="mcd-root" role="dialog" aria-label="数学输入台">
      <div class="mcd__panel">
        <!-- 目标头：始终显示将插入到哪里（§5.2 必须具备 #4） -->
        <header class="mcd__head">
          <span class="mcd__title">数学输入台</span>
          <span class="mcd__target" :class="{ 'is-missing': !targetLabel }" data-testid="mcd-target">
            插入到：{{ targetLabel || '尚未选择目标（点击教案正文后再试）' }}
          </span>
          <div class="mcd__spacer" />
          <button class="tv3-btn tv3-btn--sm" type="button" data-testid="mcd-close" aria-label="关闭数学输入台" @click="$emit('close')">×</button>
        </header>

        <!-- 统一的可编辑 MathField：所有入口的最终形态（键盘/结构/手写/语音都汇到这里） -->
        <div class="mcd__field">
          <MathField ref="mf" v-model="fieldLatex" :font-size="24" testid="mcd-field" />
          <div class="mcd__field-hint">最终公式以此为准：可直接键入、方向键移动、选区替换、Ctrl+Z 撤销</div>
        </div>

        <!-- 键位组 + 手写画布（复用既有 MathKeyboard，点按/拖入都进入上方 MathField） -->
        <MathKeyboard :smart-for="smartFor" @insert="onKbdInsert" />

        <!-- 语音入口（fixture 演示，诚实标注） -->
        <div class="mcd__voice" data-testid="mcd-voice">
          <span class="mcd__voice-label">🎤 语音（P0 文本模拟，P1 接真实麦克风）</span>
          <input
            v-model="voiceText" class="tv3-input" style="flex: 1; min-width: 220px"
            placeholder="例如：负b加减根号下b平方减4ac，除以2a" data-testid="mcd-voice-input"
            @keydown.enter.prevent="sendVoice"
          />
          <button class="tv3-btn tv3-btn--sm tv3-btn--ghost-ai" type="button" :disabled="voiceLoading || !voiceText.trim()" data-testid="mcd-voice-run" @click="sendVoice">
            {{ voiceLoading ? '解析中…' : '🎤 转公式' }}
          </button>
          <div v-if="asrLive" class="mcd__asr" data-testid="mcd-asr">听到：{{ asrLive }}</div>
          <div v-if="voiceAlts.length" class="mxd-photo__choices" style="margin-top: 6px">
            <div
              v-for="(a, i) in voiceAlts" :key="i" class="mxd-photo__choice" style="flex: 1; min-width: 200px"
              :class="{ 'is-picked': fieldLatex === a }" :data-testid="`mcd-voice-alt-${i}`"
              role="button" @click="fieldLatex = a"
            >
              <span class="mxd-photo__choice-icon">{{ i === 0 ? '①' : '②' }}</span>
              <div style="flex: 1; min-width: 0; overflow: hidden" v-html="renderLatex(a)" />
            </div>
          </div>
        </div>

        <footer class="mxd-photo__foot">
          <span class="mcd__note">识别/语音为示例识别，插入前请在上方校对区确认</span>
          <span class="mcd__spacer" />
          <button class="tv3-btn tv3-btn--sm" type="button" @click="clearField" :disabled="!fieldLatex">清空</button>
          <button class="tv3-btn tv3-btn--sm" type="button" data-testid="mcd-cancel" @click="$emit('close')">取消</button>
          <button
            class="tv3-btn tv3-btn--sm tv3-btn--primary" type="button"
            :disabled="!fieldLatex.trim() || !targetLabel" data-testid="mcd-insert"
            :title="targetLabel ? `插入到：${targetLabel}` : '请先点击正文选择插入位置'"
            @click="onInsert"
          >✓ 插入</button>
        </footer>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
/**
 * MathCaptureDock —— 数学内容捕获台（B1 核心）
 * 键入 / 公式键盘 / 手写画布 / 语音（fixture）→ 同一个可编辑 MathField 校对 → 教师确认插入目标光标。
 * 复用 MathKeyboard（键位 + 画布）与 MathField，不另造编辑器（§5.2）。
 */
import { ref, watch, onBeforeUnmount } from 'vue'
import MathField from './MathField.vue'
import MathKeyboard from './MathKeyboard.vue'
import { renderLatex } from './latex'
import { v3Api } from '@/api/teacherV3'

const props = defineProps<{
  open: boolean
  /** 目标位置描述，如「教案 > 情境引入 > 光标处」；为空时禁止插入 */
  targetLabel: string
  smartFor?: 'conic' | 'solid' | 'function' | 'plane' | 'stat'
}>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'insert', latex: string): void }>()

const mf = ref<InstanceType<typeof MathField> | null>(null)
const fieldLatex = ref('')
const voiceText = ref('')
const voiceLoading = ref(false)
const asrLive = ref('')
const voiceAlts = ref<string[]>([])
let sseCtrl: { abort: () => void } | null = null

watch(() => props.open, (v) => {
  if (v) {
    asrLive.value = ''
    voiceAlts.value = []
    window.setTimeout(() => mf.value?.focus(), 120)
  } else if (sseCtrl) { sseCtrl.abort(); sseCtrl = null }
})
onBeforeUnmount(() => sseCtrl?.abort())

/** 键位点按 → 插入当前光标/替换选区（MathLive selectionMode=placeholder）；模板占位符保留可填 */
function onKbdInsert(k: { latex: string; label?: string }) {
  if (!k || typeof k !== 'object' || !('latex' in k)) return
  if (k.label === '手写公式') {
    // 手写识别结果（演示）：先载入 MathField 校对，确认后再插入正文
    fieldLatex.value = k.latex
    return
  }
  mf.value?.insert(k.latex)
  mf.value?.focus()
}

function clearField() {
  fieldLatex.value = ''
  mf.value?.focus()
}

function onInsert() {
  const latex = fieldLatex.value.trim()
  if (!latex || !props.targetLabel) return
  emit('insert', latex)
  fieldLatex.value = ''
}

/** 语音 fixture：asr 原文回显 → 候选进 MathField 校对（不自动落稿） */
async function sendVoice() {
  if (!voiceText.value.trim() || voiceLoading.value) return
  voiceLoading.value = true
  asrLive.value = ''
  voiceAlts.value = []
  try {
    sseCtrl = v3Api.butler.voiceFormula({ text: voiceText.value, context: { route: 'math-capture-dock' } }, (event, data) => {
      if (event === 'asr_partial') asrLive.value += (data as { text: string }).text
      else if (event === 'asr_final') asrLive.value = (data as { text: string }).text
      else if (event === 'card') {
        const card = data as { latex: string; alternatives?: string[] }
        fieldLatex.value = card.latex
        voiceAlts.value = [card.latex, ...(card.alternatives || [])].filter((v, i, arr) => arr.indexOf(v) === i)
      }
    })
    await new Promise<void>((resolve) => setTimeout(resolve, 2200))
  } catch { /* mock */ } finally { voiceLoading.value = false }
}
</script>

<style scoped>
.mcd {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 140;
  display: flex; justify-content: center; pointer-events: none;
}
.mcd__panel {
  pointer-events: auto; width: min(980px, 96vw); margin-bottom: 10px;
  background: #fff; border: 1px solid var(--tv3-line); border-radius: 16px;
  box-shadow: 0 -8px 40px rgba(10, 30, 58, 0.18);
  padding: 10px 14px 12px; display: flex; flex-direction: column; gap: 8px;
  max-height: 72vh; overflow-y: auto;
}
.mcd__head { display: flex; align-items: center; gap: 10px; }
.mcd__title { font-size: 13.5px; font-weight: 800; }
.mcd__target {
  font-size: 12px; color: var(--tv3-ink2); padding: 3px 10px; border-radius: 8px;
  background: var(--tv3-teal-soft, #e8f6f4); border: 1px solid var(--tv3-teal, #0e9488);
}
.mcd__target.is-missing {
  background: #fdf3f3; border-color: #f0c9c6; color: #b1382c;
}
.mcd__spacer { flex: 1; }
.mcd__field { display: flex; flex-direction: column; gap: 4px; }
.mcd__field-hint { font-size: 11px; color: var(--tv3-ink4); }
.mcd__voice { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mcd__voice-label { font-size: 11.5px; color: var(--tv3-ink3); }
.mcd__asr {
  flex-basis: 100%; font-size: 12px; color: var(--tv3-ink2);
  padding: 4px 10px; border-radius: 8px; background: var(--tv3-bg2);
}
.mcd__note { font-size: 11px; color: var(--tv3-ink4); }
.mxd-photo__choices { display: flex; gap: 8px; }
.mxd-photo__choice {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px;
  border: 1px solid var(--tv3-line); border-radius: 10px; cursor: pointer; background: #fff;
}
.mxd-photo__choice.is-picked { border-color: var(--tv3-gold, #c99735); background: var(--tv3-gold-soft, #fdf6e3); }
.mxd-photo__foot { display: flex; align-items: center; gap: 8px; }
.dock-enter-active, .dock-leave-active { transition: transform .22s ease, opacity .22s ease; }
.dock-enter-from, .dock-leave-to { transform: translateY(30px); opacity: 0; }
</style>
