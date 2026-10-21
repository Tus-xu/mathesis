/**
 * 内部码 → 用户可见文案的**唯一映射层**。
 *
 * 为什么需要它（见 frontend/.impeccable.md）：
 *   目标用户 = 一线高中数学教师（非技术用户）；品牌调性 = 温和陪伴（不甩术语）。
 *   后端会在多个字段里回传**机器码**：`job_summary.job_type`（`conversation.run`）、
 *   `job_summary.status`（`PENDING`）、`message.run_status`、`message.error_label`
 *   （`provider_timeout` / `grounding_violation` …，handler.py 注释即写明「脱敏机器标签」）。
 *   这些码只应进日志与事件，**不得直接渲染给教师**。
 *
 * 2026-09-14 实测外露过的内容：
 *   - `JobStatus.vue` 直接渲染 `conversation.run`，以及 `last_error` 的真实内容
 *     `IntegrityError('(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError)…')`
 *   - `MessageItem.vue` / `MessageCard.vue` 直接渲染 `error_label` 与未知 `run_status`
 *
 * 关键点：`copyFor()` 在未收录时**原样返回 key**，所以调用方必须判定并兜底，
 * 否则后端新增一个码就会静默泄漏。本模块把这件事收敛到一处。
 */

import { copyFor } from './copy'

/** 查表；未收录（copyFor 原样返回 key）→ 走 fallback，绝不回显原码。 */
function mapOrFallback(prefix: string, code: string, fallbackKey: string): string {
  const key = `${prefix}${code}`
  const label = copyFor(key)
  return label === key ? copyFor(fallbackKey) : label
}

/** 作业状态：`job_summary.status`（PENDING / CLAIMED / RUNNING / SUCCEEDED / …）。 */
export function jobStatusLabel(status: string): string {
  return mapOrFallback('prep.job.', status, 'prep.job.unknown')
}

/** 作业类型：`job_summary.job_type`，形如 `conversation.run` → `prep.job.type.conversation_run`。 */
export function jobTypeLabel(jobType: string): string {
  return mapOrFallback('prep.job.type.', jobType.replace(/\./g, '_'), 'prep.job.type.unknown')
}

const RUN_STATUS_ALIASES: Record<string, string> = {
  running: 'RUNNING',
  pending: 'PENDING',
  failed: 'FAILED',
  basic_mode: 'BASIC_MODE',
}

/** 消息运行状态：`message.run_status`（后端大小写不统一，此处归一）。 */
export function runStatusLabel(status: string | null | undefined): string {
  if (!status) return ''
  const normalized = RUN_STATUS_ALIASES[status.toLowerCase()] ?? status
  return mapOrFallback('prep.run.', normalized, 'prep.run.unknown')
}

/** 消息错误标签：`message.error_label`（后端脱敏机器标签）。 */
export function errorLabelText(label: string | null | undefined): string {
  if (!label) return ''
  return mapOrFallback('prep.error.label.', label, 'prep.error.label.generic')
}
