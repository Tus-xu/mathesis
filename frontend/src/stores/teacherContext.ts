/**
 * B6 · 教师端 V3 页面上下文（PROTOTYPE-ONLY，IFC-PRODUCT-01 登记中）
 * 轻量 reactive 模块（非 Pinia，避免测试环境依赖）：各视图写入当前工件上下文，AI 管家读取。
 * 原则：没采集到的字段就是 undefined——UI 显示"尚未打开/未选择"，不用演示值冒充真实识别。
 *
 * ── WP-K / Q-2 修复（业务事实不得落在 localStorage）────────────────────────────
 * 原来是 `tv3-decision-log`：把教师"接受/拒绝/执行/取消/修正"的决策记录写进 localStorage。
 * 那是**业务事实（审计语料）**，平台契约只允许 localStorage 存 UI 偏好（草稿/主题/折叠态），
 * 且该记录**没有任何读取方**（`decisionLog()` 只被 import、从未被调用）——
 * 即"写了但永不显示、永不过期、永远只在这台机器上"的隐性业务真相，必须去掉。
 *
 * 现在：决策记录改为**会话内存**（刷新即失），并明确标注它不是持久审计。
 * 服务端决策审计需要新端点 —— 见 CHANGE REQUEST CR-K-02。
 */
import { reactive } from 'vue'

export interface Tv3Context {
  route: string
  class_name?: string
  /** 课题 / 工件标题（教案课题、课件标题、作业标题…） */
  topic?: string
  plan_id?: string
  deck_id?: string
  deck_title?: string
  slide_index?: number
  slide_count?: number
  selection?: { type: string; summary: string }
  /** 页面特有补充（课堂状态、作业提交数等，一行内） */
  extra?: string
}

const state = reactive<Tv3Context>({ route: '' })

export function useTv3Context(): Tv3Context {
  return state
}

/** 视图写入：route 变化时自动清空上一页的工件上下文（防止跨页残留误导） */
export function updateTv3Context(patch: Partial<Tv3Context> & { route: string }) {
  if (patch.route !== state.route) {
    state.class_name = undefined
    state.topic = undefined
    state.plan_id = undefined
    state.deck_id = undefined
    state.deck_title = undefined
    state.slide_index = undefined
    state.slide_count = undefined
    state.selection = undefined
    state.extra = undefined
  }
  Object.assign(state, patch)
}

/** route 是否匹配当前上下文（管家侧判断"工件字段是否可信任"） */
export function contextBelongsTo(route: string): boolean {
  return state.route === route
}

/* ---------- 轻量决策记录（**会话内存，不落盘**） ----------
 * Q-2：这里过去写 localStorage('tv3-decision-log')。决策记录是业务事实，不是 UI 偏好，
 * 因此不再持久化：本次会话可见，刷新清空，且 UI 不得把它当成审计凭据。
 * 持久决策审计需要服务端端点（CHANGE REQUEST CR-K-02）。
 */
export interface DecisionEntry { ts: string; kind: string; detail: string }
const DECISION_CAP = 30
const decisionState = reactive<{ log: DecisionEntry[] }>({ log: [] })
export function logDecision(kind: 'accept' | 'reject' | 'execute' | 'cancel' | 'fix', detail: string) {
  decisionState.log.unshift({ ts: new Date().toISOString().slice(11, 19), kind, detail })
  if (decisionState.log.length > DECISION_CAP) decisionState.log.length = DECISION_CAP
}
/** 本次会话的决策记录（只读快照；**不代表**持久审计） */
export function decisionLog(): readonly DecisionEntry[] {
  return decisionState.log
}
