/**
 * 平台契约前端语义守卫（P00）。
 *
 * JSON Schema 只能表达形状；**跨字段语义**（教材 page 锚点、Stepper/Wizard、
 * 任意 HTML/CSS/JS、localStorage 业务真相、trust label 提升）必须在前端也有一层
 * 机器守卫，否则前端会在"schema 通过"后把违规 payload 当作合法渲染输入。
 *
 * 本文件是前端侧的语义镜像：与后端 `app/platform_contracts/*.py` 的 validator 一一对应。
 * 任何新增语义规则必须**两端同时**登记，由 consumer contract test 双向断言。
 */

import type { SchemaNode } from './validateSchema'

export interface SemanticViolation {
  path: string
  message: string
}

const FORBIDDEN_TEXTBOOK_KEYS = ['page', 'page_no', 'page_number', 'page_ref', 'page_refs', 'pnn']

function walk(node: unknown, path: string, fn: (n: any, p: string) => void): void {
  if (Array.isArray(node)) {
    node.forEach((item, i) => walk(item, `${path}[${i}]`, fn))
  } else if (node && typeof node === 'object') {
    fn(node, path)
    for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
      walk(v, path ? `${path}.${k}` : k, fn)
    }
  }
}

/**
 * 教材证据不得带 page/page_refs/pNN（教材源铁律的机器化）。
 *
 * 注意：非 TEXTBOOK 来源（LITERATURE/WEB/DATASET/…）允许 page+bbox。
 */
export function checkTextbookLocator(payload: unknown): SemanticViolation[] {
  const violations: SemanticViolation[] = []
  walk(payload, '', (node, path) => {
    if (node?.source_type !== 'TEXTBOOK') return
    if (node.section_id == null || node.chunk_id == null) {
      violations.push({
        path,
        message: 'TEXTBOOK 证据必须携带 section_id + chunk_id（source → section → chunk）',
      })
    }
    if (node.page != null || node.bbox != null) {
      violations.push({ path, message: 'TEXTBOOK 证据禁止 page/bbox' })
    }
    for (const key of FORBIDDEN_TEXTBOOK_KEYS) {
      if (key in node && key !== 'section_id' && key !== 'chunk_id') {
        violations.push({ path: `${path}.${key}`, message: `TEXTBOOK 证据出现被禁字段 ${key}` })
      }
    }
  })
  return violations
}

/** Stepper/Wizard 禁止（Conversation-first）。 */
export function checkConversationFirst(payload: unknown): SemanticViolation[] {
  const violations: SemanticViolation[] = []
  walk(payload, '', (node, path) => {
    if (node?.stepper_allowed === true) violations.push({ path, message: '禁止 Stepper' })
    if (node?.wizard_allowed === true) violations.push({ path, message: '禁止 Wizard' })
    if (node?.conversation_first === false) {
      violations.push({ path, message: 'Workspace 必须 conversation-first' })
    }
  })
  return violations
}

/** 禁止任意模型 HTML/CSS/JS。 */
export function checkNoArbitraryMarkup(payload: unknown): SemanticViolation[] {
  const violations: SemanticViolation[] = []
  walk(payload, '', (node, path) => {
    if (node?.arbitrary_html === true) violations.push({ path, message: '禁止任意 HTML' })
    if (node?.arbitrary_css === true) violations.push({ path, message: '禁止任意 CSS' })
    if (node?.arbitrary_js === true) violations.push({ path, message: '禁止任意 JS' })
    if (node?.ai_may_invent_types === true) {
      violations.push({ path, message: 'AI 只能产生已注册组件类型' })
    }
  })
  return violations
}

/** localStorage 不得承载业务真相 / 授权。 */
export function checkLocalStoragePolicy(payload: unknown): SemanticViolation[] {
  const violations: SemanticViolation[] = []
  walk(payload, '', (node, path) => {
    if (!Array.isArray(node?.allowed_kinds)) return
    for (const kind of node.allowed_kinds as string[]) {
      if (kind === 'BUSINESS_TRUTH' || kind === 'AUTHORIZATION' || kind === 'CAPABILITY') {
        violations.push({ path: `${path}.allowed_kinds`, message: `localStorage 不得保存 ${kind}` })
      }
    }
    if (Array.isArray(node.forbidden_kinds)) {
      for (const required of ['BUSINESS_TRUTH', 'AUTHORIZATION']) {
        if (!(node.forbidden_kinds as string[]).includes(required)) {
          violations.push({
            path: `${path}.forbidden_kinds`,
            message: `forbidden_kinds 必须显式包含 ${required}`,
          })
        }
      }
    }
  })
  return violations
}

/** 用户上传/检索文本始终 UNTRUSTED_DATA。 */
export function checkTrustLabels(payload: unknown): SemanticViolation[] {
  const violations: SemanticViolation[] = []
  walk(payload, '', (node, path) => {
    const labels = node?.trust_labels
    if (!labels || typeof labels !== 'object') return
    for (const key of ['evidence_refs', 'recent_conversation']) {
      const v = (labels as Record<string, unknown>)[key]
      if (v != null && v !== 'UNTRUSTED_DATA') {
        violations.push({
          path: `${path}.trust_labels.${key}`,
          message: '用户上传/检索文本必须标 UNTRUSTED_DATA',
        })
      }
    }
  })
  return violations
}

/** 状态权威分界：client/stream 不得改变业务真相，localStorage 仅 client UI。 */
export function checkStateAuthority(payload: unknown): SemanticViolation[] {
  const violations: SemanticViolation[] = []
  walk(payload, '', (node, path) => {
    if (node?.may_change_business_truth === true) {
      violations.push({ path, message: '前端状态不得改变业务真相' })
    }
    if (node?.localStorage_allowed === true && node?.category && node.category !== 'client') {
      violations.push({
        path,
        message: `localStorage 只允许 UI 偏好；state[${node.category}] 不得持久化`,
      })
    }
  })
  return violations
}

/** Job PARTIAL 必须有 partial_reason；unknown status 不得默认成成功。 */
export function checkJobSemantics(payload: unknown): SemanticViolation[] {
  const violations: SemanticViolation[] = []
  walk(payload, '', (node, path) => {
    if (node?.status === 'PARTIAL' && node?.partial_reason == null) {
      violations.push({ path, message: 'PARTIAL 必须携带 partial_reason（不得显示成完成）' })
    }
  })
  return violations
}

/** 生产不得使用未发布 prompt。 */
export function checkPromptRelease(payload: unknown): SemanticViolation[] {
  const violations: SemanticViolation[] = []
  walk(payload, '', (node, path) => {
    if (node?.environment !== 'PRODUCTION') return
    const status = node?.prompt_ref?.release_status
    if (status != null && status !== 'RELEASED' && status !== 'DEPRECATED') {
      violations.push({
        path: `${path}.prompt_ref.release_status`,
        message: `生产不得使用未发布 prompt（${status}）`,
      })
    }
  })
  return violations
}

/** 全部语义检查（前端侧单一入口）。 */
export const SEMANTIC_CHECKS = [
  checkTextbookLocator,
  checkConversationFirst,
  checkNoArbitraryMarkup,
  checkLocalStoragePolicy,
  checkTrustLabels,
  checkStateAuthority,
  checkJobSemantics,
  checkPromptRelease,
] as const

export function checkSemantics(payload: unknown): SemanticViolation[] {
  return SEMANTIC_CHECKS.flatMap((fn) => fn(payload))
}

export type { SchemaNode }
