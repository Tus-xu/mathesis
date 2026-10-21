/**
 * 工作台布局 store（FRONTEND-ARCH §5 · 唯一允许 localStorage 的业务 store，
 * 且**仅**持久化 UI 偏好，不含任何业务事实）。
 *
 * 持久化项：workspace / 折叠态 / 面板宽度 / 日志展开 / 打开的 Tab / 当前项目 id。
 * 这些均可在冷启动时从服务端 recent 重新对齐（宪法 §84），故属「可重建的客户端状态」。
 */
import { ref, watch } from 'vue'
import { defineStore } from 'pinia'
import type { WorkspaceMode } from '../api/types'

/** localStorage 版本号：几何默认值变更时递增，避免旧偏好覆盖新布局。 */
const STORAGE_KEY = 'rw.panel.v3'

export interface PanelWidths {
  readSidebar: number
  readAgent: number
  writeLeft: number
  writeAgent: number
  writeEditor: number
  logHeight: number
}

/**
 * 黄金图默认几何。
 *
 * READ（VISUAL-SPEC §1.4）：330 / 737 / 604 —— 已用像素扫描复核，与 `READ-GOLDEN.png`
 * 实测竖边界（x≈323 / 1065）一致。
 *
 * WRITE（VISUAL-SPEC §1.5 文档写 355/435/428/454，但**与黄金图不符**）：
 * 对 `WRITE-GOLDEN.png`（1672×941）做竖边框像素扫描 + 叠加参考线，实测边界列
 * x≈306 / 896 / 1358 → 左 306 / editor 590 / preview 462 / agent 314。
 * 依 VISUAL-SPEC §0「视觉层冲突时 **黄金图 > 本文件**」及 §0.4「栏宽比例偏差 > 2% 不允许」，
 * 此处采用黄金图实测值（改文档数字只需改本处 + `research-tokens.css`）。
 */
export const DEFAULT_WIDTHS: PanelWidths = {
  // 2026-10-21 UX 重构：READ 左栏 300 / Agent 420，阅读区获得更多宽度。
  readSidebar: 300,
  readAgent: 420,
  // WRITE —— 黄金图实测（合计 306 + 590 + 462 + 314 = 1672）
  writeLeft: 306,
  writeEditor: 590,
  writeAgent: 314,
  logHeight: 164,
}

/** 各面板拖动边界（px）。 */
export const WIDTH_BOUNDS: Record<keyof PanelWidths, { min: number; max: number }> = {
  readSidebar: { min: 240, max: 440 },
  readAgent: { min: 340, max: 720 },
  writeLeft: { min: 240, max: 460 },
  writeAgent: { min: 280, max: 620 },
  writeEditor: { min: 320, max: 820 },
  logHeight: { min: 96, max: 420 },
}

interface PersistedState {
  workspace: WorkspaceMode
  sidebarCollapsed: boolean
  agentCollapsed: boolean
  widths: PanelWidths
  logExpanded: boolean
  openTabs: string[]
  currentProjectId: string
}

function loadPersisted(): Partial<PersistedState> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Partial<PersistedState>) : {}
  } catch {
    return {}
  }
}

export const useRwPanelStore = defineStore('rw.panel', () => {
  const saved = loadPersisted()

  const workspace = ref<WorkspaceMode>(saved.workspace === 'write' ? 'write' : 'read')
  const sidebarCollapsed = ref(Boolean(saved.sidebarCollapsed))
  const agentCollapsed = ref(Boolean(saved.agentCollapsed))
  const widths = ref<PanelWidths>({ ...DEFAULT_WIDTHS, ...(saved.widths ?? {}) })
  const logExpanded = ref(saved.logExpanded !== false)
  const openTabs = ref<string[]>(Array.isArray(saved.openTabs) ? [...saved.openTabs] : [])
  const currentProjectId = ref(typeof saved.currentProjectId === 'string' ? saved.currentProjectId : '')

  function persist() {
    try {
      const payload: PersistedState = {
        workspace: workspace.value,
        sidebarCollapsed: sidebarCollapsed.value,
        agentCollapsed: agentCollapsed.value,
        widths: widths.value,
        logExpanded: logExpanded.value,
        openTabs: openTabs.value,
        currentProjectId: currentProjectId.value,
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    } catch {
      /* 隐私模式 / 配额不足时静默降级：UI 偏好丢失可接受 */
    }
  }

  watch(
    [workspace, sidebarCollapsed, agentCollapsed, widths, logExpanded, openTabs, currentProjectId],
    persist,
    { deep: true },
  )

  function setWorkspace(mode: WorkspaceMode) {
    workspace.value = mode
  }
  function setSidebarCollapsed(v: boolean) {
    sidebarCollapsed.value = v
  }
  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }
  function setAgentCollapsed(v: boolean) {
    agentCollapsed.value = v
  }
  function toggleAgent() {
    agentCollapsed.value = !agentCollapsed.value
  }
  function setWidth(key: keyof PanelWidths, value: number) {
    const bounds = WIDTH_BOUNDS[key]
    widths.value = { ...widths.value, [key]: Math.min(bounds.max, Math.max(bounds.min, Math.round(value))) }
  }
  function resetWidths() {
    widths.value = { ...DEFAULT_WIDTHS }
  }
  function toggleLog() {
    logExpanded.value = !logExpanded.value
  }
  function openTab(path: string) {
    if (!openTabs.value.includes(path)) openTabs.value = [...openTabs.value, path]
  }
  function closeTab(path: string) {
    openTabs.value = openTabs.value.filter((p) => p !== path)
  }
  function setCurrentProject(id: string) {
    currentProjectId.value = id || ''
  }

  return {
    workspace,
    sidebarCollapsed,
    agentCollapsed,
    widths,
    logExpanded,
    openTabs,
    currentProjectId,
    setWorkspace,
    setSidebarCollapsed,
    toggleSidebar,
    setAgentCollapsed,
    toggleAgent,
    setWidth,
    resetWidths,
    toggleLog,
    openTab,
    closeTab,
    setCurrentProject,
  }
})
