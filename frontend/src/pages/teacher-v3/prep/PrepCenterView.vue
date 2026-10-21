<template>
  <div class="ailp-root" data-testid="tv3-prep-center">
    <div v-if="homeError" class="prep-kernel-error" role="alert" data-testid="prep-home-error">
      <strong>{{ copyFor('prep.error.home.title') }}</strong><span>{{ homeError }}</span>
      <button type="button" @click="homeError = ''">{{ copyFor('prep.error.dismiss') }}</button>
    </div>
    <PrepHome :busy="creatingSession" @submit="onHomeSubmit" />
  </div>
</template>

<script setup lang="ts">
/**
 * PrepCenterView —— 教师备课中心入口（2026-09-14 简化后）。
 *
 * 现在只有一条路径：`PrepHome` 首页 → 真实 8100 TeachingSession →
 * `/teacher-v3/prep/session/:sessionId` 的沉浸式备课会话。
 *
 * 已删除的中间环节（教师明确要求）：
 * - 「大纲预览」：教学方案的正文与「采用这份方案」确认，改由 `useAutoDeckFlow`
 *   按服务端下发的动作自动确认；
 * - 「教案预览」：`PrepEditor.vue` 与其 `focus=lesson-plan` /
 *   `mode=legacy` 两个入口。AI 生成结束后课件直接落到 PPT 编辑工作台。
 *
 * 因此本文件不再持有任何编辑器分支，也不再进入 `TeacherV3Layout` 之外的旧链路。
 */
import './ailp.css'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { createLegacyTeachingSession } from './legacyKernelBridge'
import PrepHome from './PrepHome.vue'
import { PrepApiError } from '@/features/teacher-prep-v2/api/prepApi'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

const router = useRouter()
const creatingSession = ref(false)
const homeError = ref('')

function presentHomeError(error: unknown): string {
  if (error instanceof PrepApiError) return copyFor(error.info.copyKey)
  if (error instanceof Error && /^prep\./.test(error.message)) return copyFor(error.message)
  return error instanceof Error ? error.message : copyFor('prep.error.create_failed')
}

/**
 * The home composer is the only entry into the VNext preparation conversation.
 * E-3 可选班级：classId 可缺省（未绑定班级的教师同样可开课）；8100 仍是唯一
 * generation step is allowed. The in-flight guard provides exactly-once UI
 * dispatch while the bridge supplies the request idempotency key.
 */
async function onHomeSubmit(input: { message: string; classId?: string }) {
  if (creatingSession.value || !input.message.trim()) return
  creatingSession.value = true
  homeError.value = ''
  try {
    const snapshot = await createLegacyTeachingSession({ classId: input.classId, message: input.message.trim() })
    const session = snapshot?.session as Record<string, unknown> | undefined
    const sessionId = typeof session?.id === 'string' && session.id ? session.id
      : typeof session?.session_id === 'string' && session.session_id ? session.session_id : ''
    if (!sessionId) throw new Error(copyFor('prep.error.session_id_missing'))
    await router.replace(`/teacher-v3/prep/session/${encodeURIComponent(sessionId)}`)
  } catch (error) {
    homeError.value = presentHomeError(error)
  } finally {
    creatingSession.value = false
  }
}
</script>

<style scoped>
.prep-kernel-error { margin: var(--ma-space-4) auto 0; max-width: calc(100% - var(--ma-space-6)); padding: var(--ma-space-3) var(--ma-space-4); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-card); background: var(--ma-surface-subtle); color: var(--ma-status-danger); display: flex; gap: var(--ma-space-2); align-items: baseline; }
.prep-kernel-error span { color: var(--ma-text-muted); font-size: var(--ma-font-size-body); }
.prep-kernel-error button { margin-left: auto; border: 1px solid currentColor; border-radius: var(--ma-radius-control); padding: 4px 10px; background: transparent; color: inherit; cursor: pointer; }
</style>
