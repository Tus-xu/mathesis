<template>
  <div class="pgn">
    <div class="ailp-blob" style="width:500px;height:500px;background:#c7d2fe;top:-150px;left:-100px"></div>
    <div class="ailp-blob" style="width:400px;height:400px;background:#a5f3fc;top:300px;right:-100px;opacity:0.3"></div>
    <div class="pgn-main">
      <div class="pgn-center ailp-fade">
        <div class="pgn-icon" :class="{ 'is-idle': phase === 'failed' }">
          <div class="pgn-icon__ring" />
          <div class="pgn-icon__pulse" />
          <div class="pgn-icon__pulse" style="animation-delay:1s" />
          <div class="pgn-icon__core">✦</div>
        </div>
        <h2>{{ errorMsg ? '备课暂时无法继续' : '正在连接教师备课服务' }}</h2>
        <p>{{ chain.brief?.topic || '未命名备课' }}</p>
        <p class="ailp-honest" style="margin-top:10px">进度与结果以教师平台返回为准，不显示本地模拟进度</p>

        <div class="pgn-card">
          <div v-if="!errorMsg" class="pgn-server-state">等待 8100 返回 TeachingSession 与教材蓝图…</div>
          <div v-if="errorMsg" class="pgn-error" role="alert">
            <p>{{ errorMsg }}</p>
            <button type="button" class="ailp-btn-primary" @click="retry">重新生成</button>
            <button type="button" class="ailp-btn-ghost" @click="$router.back()">返回修改备课信息</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * PrepGenerating —— AI 生成中
 * 生成清单：大纲请求进行中仅点亮第 1 步「分析教材与知识点」，其余模块待生成；
 * 请求成功 setOutline 进入大纲确认；请求失败立即停止动画，只展示错误卡片。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { createLegacyTeachingSession, getLegacyTeachingSession, mapLegacyPrepSnapshot } from './legacyKernelBridge'
import { applyKernelProjection, setKernelError, usePrepChain } from './prepChain'

const chain = usePrepChain()
/** 生成阶段只反映请求状态，不模拟服务端进度。 */
const phase = ref<'generating' | 'failed'>('generating')
const errorMsg = ref('')
let reqPromise: Promise<any> | null = null

/** 创建/恢复真实 TeachingSession，并只接受服务端投影的蓝图。 */
function runOutline(): Promise<unknown> {
  errorMsg.value = ''
  phase.value = 'generating'
  if (reqPromise) return reqPromise
  reqPromise = (async () => {
    try {
      const brief = chain.brief
      if (!brief?.classId) {
        phase.value = 'failed'
        errorMsg.value = '服务端尚未提供班级信息，无法创建备课会话。请先选择真实班级。'
        setKernelError(errorMsg.value)
        return null
      }
      const snapshot = chain.sessionId
        ? await getLegacyTeachingSession(chain.sessionId)
        : await createLegacyTeachingSession({ classId: brief.classId, message: brief.requirements.join('；') || brief.topic })
      const projection = mapLegacyPrepSnapshot(snapshot)
      applyKernelProjection(projection)
      if (!projection.outline) {
        phase.value = 'failed'
        errorMsg.value = projection.blockedReason || '教师平台尚未返回可确认的教学蓝图。'
        setKernelError(errorMsg.value)
        return null
      }
      return snapshot
    } catch (error) {
      phase.value = 'failed'
      errorMsg.value = error instanceof Error ? error.message : '教师平台请求失败，请稍后重试。'
      setKernelError(errorMsg.value)
      return null
    }
  })()
  return reqPromise
}

function retry() {
  reqPromise = null
  errorMsg.value = ''
  phase.value = 'generating'
  runOutline()
}

onMounted(() => { runOutline() })
onBeforeUnmount(() => { reqPromise = null })
</script>

<style scoped>
.pgn { min-height: calc(100vh - 56px); display: flex; align-items: flex-start; justify-content: center; }
.pgn-main { width: 100%; max-width: 640px; padding: 60px 24px 60px; }
.pgn-center { text-align: center; }
.pgn-icon { position: relative; width: 110px; height: 110px; margin: 0 auto 26px; }
.pgn-icon__ring { position: absolute; inset: -12px; border-radius: 50%; background: conic-gradient(from 0deg, rgba(79, 70, 229, 0.12), rgba(6, 182, 212, 0.4), rgba(139, 92, 246, 0.3), rgba(79, 70, 229, 0.12)); filter: blur(8px); animation: ailp-spin 8s linear infinite; }
.pgn-icon__pulse { position: absolute; inset: -18px; border-radius: 50%; border: 2px solid rgba(99, 102, 241, 0.3); animation: ailp-pulse 2s ease-out infinite; }
.pgn-icon__core { position: absolute; inset: 0; border-radius: 50%; display: grid; place-items: center; font-size: 40px; color: #fff; background: linear-gradient(135deg, #4f46e5, #7c3aed 50%, #06b6d4); box-shadow: 0 8px 32px rgba(79, 70, 229, 0.4); animation: ailp-pulse 2.4s ease-in-out infinite; }
.pgn-icon.is-idle .pgn-icon__ring, .pgn-icon.is-idle .pgn-icon__pulse, .pgn-icon.is-idle .pgn-icon__core { animation: none; }
.pgn-center h2 { font-size: 26px; font-weight: 800; margin-bottom: 8px; }
.pgn-center > p { color: var(--ailp-gray-500); font-size: 14px; }
.pgn-card { margin-top: 30px; background: rgba(255, 255, 255, 0.9); border: 1px solid var(--ailp-gray-200); border-radius: 18px; box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08); padding: 18px; text-align: left; display: flex; flex-direction: column; gap: 4px; }
.pgn-step { display: flex; align-items: center; gap: 13px; padding: 12px 14px; border-radius: 13px; }
.pgn-step.completed { background: rgba(16, 185, 129, 0.06); }
.pgn-step.current { background: linear-gradient(135deg, rgba(99, 102, 241, 0.08), rgba(6, 182, 212, 0.06)); border: 1px solid rgba(99, 102, 241, 0.16); }
.pgn-step.pending { opacity: 0.5; }
.pgn-step__dot { width: 26px; height: 26px; border-radius: 50%; display: grid; place-items: center; flex-shrink: 0; font-size: 11px; color: #fff; background: var(--ailp-gray-200); position: relative; }
.pgn-step__dot.completed { background: var(--ailp-success-500); }
.pgn-step__dot.current { background: linear-gradient(135deg, #4f46e5, #06b6d4); }
.pgn-step__live { width: 8px; height: 8px; border-radius: 50%; background: #fff; animation: ailp-progress-dot 1.3s ease-in-out infinite; }
.pgn-step__pendingdot { width: 6px; height: 6px; border-radius: 50%; background: var(--ailp-gray-400); }
.pgn-step__body { flex: 1; }
.pgn-step__row { display: flex; align-items: center; justify-content: space-between; }
.pgn-step__row b { font-size: 13px; color: var(--ailp-gray-500); font-weight: 600; }
.pgn-step__row b.is-active { color: var(--ailp-primary-700); }
.pgn-step__row b.is-done { color: var(--ailp-gray-800); }
.pgn-step__state { font-size: 11px; font-weight: 600; }
.pgn-step__state.is-done { color: var(--ailp-success-600); }
.pgn-step__state.is-run { color: var(--ailp-primary-600); }
.pgn-step__bar { height: 4px; border-radius: 2px; background: var(--ailp-gray-100); overflow: hidden; margin-top: 8px; }
.pgn-step__bar i { display: block; height: 100%; width: 55%; border-radius: 2px; background: linear-gradient(90deg, #4f46e5, #06b6d4); position: relative; overflow: hidden; }
.pgn-step__bar i::after { content: ''; position: absolute; inset: 0; background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.5), transparent); background-size: 200% 100%; animation: ailp-shimmer 1.8s linear infinite; }
.pgn-error { margin-top: 14px; padding: 12px 14px; border: 1px solid #f3c4b9; background: #fdf0ec; border-radius: 8px; }
.pgn-error p { margin: 0 0 10px; color: #9c3a28; font-size: 13px; }
.pgn-error .ailp-btn-ghost { margin-left: 10px; border: 1px solid #d4d4d8; background: #fff; color: #333; padding: 7px 14px; border-radius: 8px; cursor: pointer; font-size: 13px; }
.pgn-error .ailp-btn-primary { cursor: pointer; font-size: 13px; }
</style>
