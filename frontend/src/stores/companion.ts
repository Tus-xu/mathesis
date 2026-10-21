/**
 * C2 · 伴随工具层状态仓（PROTOTYPE-ONLY，IFC-C2-a 登记中）
 * 轻量 reactive 模块（同 teacherContext，不用 Pinia 避免测试依赖）：
 *  - 右下角 Dock 三入口互斥：AI 管家 / 伴随资源 / 数学绘图，同一时刻只开一个覆盖层
 *  - 插入回执 + 撤销：各工作页监听 tv3-companion-insert 落稿后 setReceipt + registerUndo
 *  - 绘图暂存：画一半关掉绘图台时自动暂存（localStorage，仅本机），可从伴随资源台「继续编辑」
 * 诚实原则：没有插入目标就显示"尚未定位"，不假装知道；撤销只对本会话内存态负责。
 */
import { reactive } from 'vue'

export type CompanionTool = 'resource' | 'draw'

export interface DrawTargetContext {
  /** 目标说明，如「第 6 页 · 例题讲解」/「环节「例题精讲」」 */
  targetLabel: string
  /** 完成后的动作文案，如「插入第 6 页」/「插入当前片段」 */
  insertLabel: string
}

export interface StashedFigure {
  id: string
  name: string
  kind: 'free' | 'fx'
  thumb: string
  records?: unknown[]
  expr?: string
  created_at: string
  used?: boolean
}

export interface CompanionReceipt {
  ok: boolean
  message: string
  locationLabel?: string
  undoLabel?: string
}

const STASH_KEY = 'tv3-draw-stash'

const state = reactive({
  open: null as CompanionTool | null,
  drawContext: null as DrawTargetContext | null,
  /** 全局绘图台的重开载荷（从伴随资源台「继续编辑」/ 暂存进入时携带） */
  drawReopen: null as { mode: 'free' | 'fx'; records?: unknown[]; expr?: string; elementId: string } | null,
  receipt: null as CompanionReceipt | null,
  stash: [] as StashedFigure[],
})

function loadStash() {
  try {
    const raw = localStorage.getItem(STASH_KEY)
    if (raw) state.stash = JSON.parse(raw)
  } catch { /* 隐私模式允许失败 */ }
}
loadStash()

function persistStash() {
  try { localStorage.setItem(STASH_KEY, JSON.stringify(state.stash.slice(0, 12))) } catch { /* ignore */ }
}

export function useCompanion() {
  return state
}

/** 打开某工具（互斥：打开一个自动关另一个）；ctx 为绘图目标上下文 */
export function openTool(tool: CompanionTool | null, ctx?: DrawTargetContext | null) {
  state.open = tool
  if (ctx !== undefined) state.drawContext = ctx
  if (tool) state.receipt = null
}

export function closeTool() {
  state.open = null
}

export function setDrawReopen(r: { mode: 'free' | 'fx'; records?: unknown[]; expr?: string; elementId: string } | null) {
  state.drawReopen = r
}

export function setReceipt(r: CompanionReceipt | null) {
  state.receipt = r
}

let undoFn: (() => void) | null = null
export function registerUndo(label: string, fn: () => void) {
  undoFn = fn
  if (state.receipt) state.receipt.undoLabel = label
}
export function undoLast() {
  if (undoFn) {
    undoFn()
    undoFn = null
    setReceipt(null)
  }
}

export function stashFigure(f: Omit<StashedFigure, 'id' | 'created_at'>) {
  state.stash.unshift({ ...f, id: `fig-${Date.now()}`, created_at: new Date().toISOString().slice(5, 16).replace('T', ' ') })
  persistStash()
}
export function removeStash(id: string) {
  state.stash = state.stash.filter((s) => s.id !== id)
  persistStash()
}
export function markStashUsed(id: string) {
  const s = state.stash.find((x) => x.id === id)
  if (s) { s.used = true; persistStash() }
}

/** 测试辅助：清空模块态 */
export function __resetCompanionForTest() {
  state.open = null
  state.drawContext = null
  state.drawReopen = null
  state.receipt = null
  state.stash = []
  undoFn = null
  try { localStorage.removeItem(STASH_KEY) } catch { /* ignore */ }
}
