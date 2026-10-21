/**
 * T22.1 · 沉浸式 surface store —— **只做 UI projection 与 UI 偏好**，不复制业务取数。
 *
 * 边界（严格遵守 `T22.1-CURRENT-DELTA.md` §7）：
 * - 复用 T22 的 `prepWorkspace` store 作为**唯一**服务端事实来源；
 * - 本 store 不改动既有 store 的任何文件，只在其上叠加：
 *   `focusRequested / activeOverlay / dockDismissed / assistantCollapsed` 等 **UI 偏好**；
 * - 业务状态（session/blueprint/artifact/revision）**一律由服务端 snapshot 派生**，
 *   本 store 不缓存、不落盘（除草稿与折叠偏好外）。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { usePrepWorkspaceStore } from '@/features/teacher-prep-v2/stores/prepWorkspace'
import {
  factsFromSnapshot,
  projectWorkspace,
} from '@/features/teacher-prep-v2/projection/project'
import type {
  ActiveOverlay,
  ProjectionFacts,
  UiProjection,
} from '@/features/teacher-prep-v2/projection/types'

/** 允许持久化的 UI 偏好白名单（**业务事实一律不落盘**）。 */
export interface SurfaceUiPrefs {
  /** 未发送草稿（唯一允许持久化的业务相关内容）。 */
  draft: string
  /** Artifact Focus 下对话助手是否收起。 */
  assistantCollapsed: boolean
  /** 「本会话产物」dock 是否被关闭。 */
  dockDismissed: boolean
}

const UI_KEY = 'teacher-prep-v2:surface:v1'
const DEFAULT_UI: SurfaceUiPrefs = { draft: '', assistantCollapsed: false, dockDismissed: false }

export const usePrepSurfaceStore = defineStore('prepSurfaceV221', () => {
  const ws = usePrepWorkspaceStore()

  // ── UI 偏好（内存 + 极小持久化白名单）────────────────────────────────────
  const focusRequested = ref(false)
  const activeOverlay = ref<ActiveOverlay>('none')
  const ui = ref<SurfaceUiPrefs>({ ...DEFAULT_UI })
  /** 打开 drawer 前的焦点元素（关闭时归还焦点）。 */
  let lastFocused: HTMLElement | null = null

  // ── 服务端事实 → projection ─────────────────────────────────────────────
  const facts = computed<ProjectionFacts>(() =>
    factsFromSnapshot({
      snapshot: ws.snapshot,
      loading: ws.phase === 'loading',
      errorKind: ws.error?.kind ?? null,
    }),
  )

  const projection = computed<UiProjection>(() =>
    projectWorkspace(
      facts.value,
      {
        focusRequested: focusRequested.value,
        activeOverlay: activeOverlay.value,
        dockDismissed: ui.value.dockDismissed,
      },
      {
        connected: ws.streamState === 'live',
        reconnecting: ws.streamState === 'reconnecting',
        gapCount: ws.gapCount,
      },
    ),
  )

  // ── 派生（全部来自 projection，组件不再自行 if/else 猜阶段）──────────────
  const surfaceMode = computed(() => projection.value.surfaceMode)
  const sessionPhase = computed(() => projection.value.sessionPhase)
  const streamPhase = computed(() => projection.value.streamPhase)
  const activeArtifact = computed(() => projection.value.activeArtifact)
  const showConversation = computed(() => projection.value.showConversation)
  const focusedError = computed(() => projection.value.focusedError)
  const showDock = computed(() => projection.value.showDock)
  const canOpenFocus = computed(() => projection.value.canOpenFocus)

  /** 无 artifact 时该值恒为 false → 组件据此决定是否渲染画布（默认 0 个画布）。 */
  const showCanvas = computed(
    () => projection.value.surfaceMode === 'artifact_focus' && projection.value.hasArtifact,
  )

  // ── UI 偏好写入（唯一出口）───────────────────────────────────────────────
  function setPref<K extends keyof SurfaceUiPrefs>(key: K, value: SurfaceUiPrefs[K]) {
    ui.value[key] = value
    persistUi()
  }

  function persistUi() {
    try {
      localStorage.setItem(UI_KEY, JSON.stringify(ui.value))
    } catch {
      /* 隐私模式：UI 偏好丢失可接受（业务事实不依赖它） */
    }
  }

  function restoreUi() {
    try {
      const raw = localStorage.getItem(UI_KEY)
      if (raw) ui.value = { ...DEFAULT_UI, ...(JSON.parse(raw) as Partial<SurfaceUiPrefs>) }
    } catch {
      ui.value = { ...DEFAULT_UI }
    }
  }

  // ── 模式 / 面板 ─────────────────────────────────────────────────────────
  function openFocus() {
    if (!canOpenFocus.value) return
    focusRequested.value = true
    setOverlay('none')
  }

  /** 关闭画布 → 回到对话模式；保留草稿、选中 artifact 与 revision（由服务端兜底）。 */
  function closeFocus() {
    focusRequested.value = false
    setOverlay('none')
  }

  /** overlay 互斥：同一时刻只允许一个；打开新的自动关闭旧的。 */
  function setOverlay(next: ActiveOverlay) {
    activeOverlay.value = next
  }

  function openOverlay(next: Exclude<ActiveOverlay, 'none'>) {
    lastFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setOverlay(next)
  }

  function closeOverlay() {
    setOverlay('none')
    // 关闭时把焦点还给触发元素（UX-CONTRACT §6）
    if (lastFocused && document.contains(lastFocused)) lastFocused.focus()
    lastFocused = null
  }

  function dismissDock() {
    setPref('dockDismissed', true)
  }

  // ── 远端动作（一律委托既有 store，保持单一取数通道）──────────────────────
  function attach(sessionId: string | null) {
    restoreUi()
    ws.attach(sessionId)
  }
  function detach() {
    ws.detach()
    focusRequested.value = false
    setOverlay('none')
  }
  function send(
    text: string,
    idempotencyKey?: string,
    selection?: { textbook_id?: string; section_id?: string; class_id?: string },
  ) {
    return ws.send(text, idempotencyKey, selection)
  }
  function runAction(
    action: string,
    target?: string | null,
    payload?: Record<string, unknown> | null,
  ) {
    return ws.runAction(action, target, payload)
  }
  function reload() {
    return ws.load()
  }

  return {
    // 透传（组件需要的服务端事实）
    snapshot: computed(() => ws.snapshot),
    timeline: computed(() => ws.timeline),
    evidence: computed(() => ws.evidence),
    availableActions: computed(() => ws.availableActions),
    error: computed(() => ws.error),
    revision: computed(() => ws.revision),
    gapCount: computed(() => ws.gapCount),
    basicMode: computed(() => ws.basicMode),
    // UI 偏好
    ui, focusRequested, activeOverlay,
    // projection
    facts, projection, surfaceMode, sessionPhase, streamPhase,
    activeArtifact, showConversation, focusedError, showDock, canOpenFocus, showCanvas,
    // actions
    setPref, persistUi, restoreUi,
    openFocus, closeFocus, setOverlay, openOverlay, closeOverlay, dismissDock,
    attach, detach, send, runAction, reload,
  }
})
