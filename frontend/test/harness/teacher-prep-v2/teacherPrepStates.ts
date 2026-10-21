import type { WorkspaceSnapshot } from '@/features/teacher-prep-v2/types/workspace'

const baseSession = { id: 'harness-session', title: '二次函数复习课', status: 'active', revision: 3, last_event_seq: 6 }
const message = (seq: number, role: 'user' | 'assistant', text: string, component: WorkspaceSnapshot['timeline'][number]['component'] = null, blueprintId: string | null = null) => ({
  message_id: `harness-message-${seq}`,
  seq,
  role,
  kind: component?.type === 'blueprint_card' ? 'blueprint_proposal' : role === 'user' ? 'user_text' : 'assistant_text',
  component,
  text,
  intent: null,
  context_snapshot_id: null,
  blueprint_id: blueprintId,
  run_status: null,
  error_label: null,
  error_code: null,
  editable: false,
  created_at: `2026-09-12T00:0${Math.min(seq, 9)}:00Z`,
})

const blueprint = {
  blueprint_id: 'harness-blueprint',
  title: '二次函数单元教学蓝图',
  status: 'proposal',
  revision: 1,
  body: { objectives: ['理解图像与表达式的对应关系'], flow: ['问题引入', '图像观察', '表达式归纳'] },
}

export function snapshotFor(state: string): WorkspaceSnapshot {
  const sixTurns = [
    message(1, 'user', '我想准备一节二次函数复习课。'),
    message(2, 'assistant', '我先基于已确认的教材范围整理教学目标。'),
    message(3, 'user', '重点放在图像和表达式的对应关系。'),
    message(4, 'assistant', '我会把图像观察和表达式归纳放进同一条教学链路。'),
    message(5, 'user', '请把课堂活动控制在一课时内。'),
    message(6, 'assistant', '下面是一份待你确认的教学方案。', { type: 'blueprint_card', props: blueprint }, 'harness-blueprint'),
  ]
  const t23Review = [
    message(1, 'assistant', '我先标出本次方案使用的教材依据。', { type: 'direct_chunk_locator', props: { source_id: 'textbook-1', section_path: ['函数', '二次函数'], chunk_id: 'chunk-9', chunk_ordinal: 9, content_hash: 'sha256:harness' } }),
    message(2, 'assistant', '这份学前诊断仍待你确认。', { type: 'diagnostic_proposal', props: { diagnostic_id: 'diag-1', status: 'proposal', revision: 7, title: '学前诊断', rationale: '确认顶点式与一般式的先备理解。', questions: [{ prompt: '请选择图像变化', evidence_refs: ['chunk-9'] }], available_actions: ['confirm', 'edit', 'reject', 'skip'] } }),
    message(3, 'assistant', '以下是班级层面的聚合学习画像。', { type: 'class_learning_profile', props: { sample_size: 18, window: '最近三次作业', coverage: 0.72, confidence: 'medium', misconceptions: ['把顶点式与一般式混用'], difficulty: 'medium', unknown: ['未覆盖口头解释'], warnings: ['样本量有限'] } }),
    message(4, 'assistant', '我建议对教学主线做一处可追溯修订。', { type: 'blueprint_revision', props: { target: 'lesson_flow', before: '图像观察', after: '图像观察→表达式归纳', reason: '补足从观察到表达的过渡。', confidence: 0.86, limitations: ['尚未覆盖分层练习'], evidence_links: ['chunk-9'], expected_revision: 8 } }),
    message(5, 'assistant', '你可以按需查看本次运行依据。', { type: 'run_context_disclosure', props: { prompt_ref: { id: 'prep-review', version: 'v2' }, context_manifest: 'ctx-1', evidence_refs: ['chunk-9'], model: 'model-label', tools: ['retrieval'], cost: { unit: 'credits', value: 2 }, trace_id: 'trace-1' } }),
  ]
  if (state === 't23-review') {
    return {
      session: { ...baseSession, status: 'waiting_confirmation', revision: 8, last_event_seq: t23Review.length },
      timeline: t23Review,
      artifact: { context: null, blueprint: null, diagnostic: null },
      evidence: [],
      available_actions: [{ action: 'confirm', target: 'diag-1', copy_key: 'prep.t23.action.confirm', disabled: false }],
      job_summary: [], basic_mode: false,
    }
  }
  const proposal = state === 'blueprint' || state === 'confirmation'
  const confirmed = state === 'artifact'
  return {
    session: { ...baseSession, status: confirmed ? 'artifact_ready' : proposal ? 'waiting_confirmation' : 'active', revision: confirmed ? 4 : 3, last_event_seq: sixTurns.length },
    timeline: state === 'empty' ? [] : sixTurns,
    artifact: {
      context: { source: { title: '脱敏教材' }, section: { title: '二次函数' } },
      blueprint: confirmed ? { ...blueprint, status: 'confirmed', revision: 2 } : proposal ? blueprint : null,
      diagnostic: null,
    },
    evidence: [],
    available_actions: state === 'confirmation' ? [{ action: 'confirm_proposal', target: 'harness-blueprint', copy_key: 'prep.action.confirm_proposal', disabled: false }] : [],
    job_summary: [],
    basic_mode: false,
  }
}
