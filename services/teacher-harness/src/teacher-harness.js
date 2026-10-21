import { Context } from '@deepseek-ai/cordis'
import { CallId } from '@deepseek-ai/dsh-llm'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime, { defineTool } from '@deepseek-ai/dsh-tools'

const WRITE_TO_STUDENT_TOOLS = new Set([
  'teacher.assignment.publish',
  'teacher.grade.confirm',
  'teacher.classroom.publish',
])

function output() {
  return {
    schema: { type: 'object', additionalProperties: true },
    render: (_arguments, value) => [{
      type: 'text',
      text: JSON.stringify(value),
    }],
  }
}

function teacherTool(name, description, parameters, platform) {
  return defineTool({
    name,
    description,
    parameters,
    output: output(),
    async execute(arguments_) {
      return platform.invoke(name, arguments_)
    },
  })
}

/**
 * Minimal DSH assembly for the teacher copilot.
 *
 * DSH owns plugin registration, typed tool invocation and the pre-execution
 * decision pipeline. FastAPI stays the only owner of identity, class scope,
 * artifacts, audit records and student-visible persistence.
 */
export async function createTeacherHarness({ platform }) {
  const ctx = new Context()
  await ctx.plugin(SystemPrompt)
  await ctx.plugin(ToolRuntime)

  ctx.on('tools/pre-execute', async (execution, next) => {
    if (WRITE_TO_STUDENT_TOOLS.has(execution.name)) {
      return { kind: 'ask', reason: '该操作会影响学生端，需教师确认。' }
    }
    return next()
  })

  ctx.tools.register(teacherTool(
    'teacher.today.read',
    '读取当前教师已授权班级的今日工作项。',
    { class_id: { type: 'string', required: true } },
    platform,
  ))
  ctx.tools.register(teacherTool(
    'teacher.quiz.create_draft',
    '从已审核、可追溯来源的高中数学题库创建教师可编辑的试卷草稿；题源不足必须如实返回。',
    {
      class_id: { type: 'string', required: true },
      knowledge_points: { type: 'array', required: true, items: { type: 'string' } },
      count: { type: 'integer', required: true },
      question_types: { type: 'object', required: true, additionalProperties: false },
      difficulty: { type: 'object', required: true, additionalProperties: false },
    },
    platform,
  ))
  ctx.tools.register(teacherTool(
    'teacher.assignment.publish',
    '将已确认的作业发布给学生；必须经过教师确认。',
    {
      class_id: { type: 'string', required: true },
      assignment_id: { type: 'string', required: true },
    },
    platform,
  ))

  return {
    ctx,
    schemas: () => ctx.tools.schemas(),
    execute(name, arguments_) {
      return ctx.tools.execute({
        signal: new AbortController().signal,
        callId: CallId(`teacher-${crypto.randomUUID()}`),
        name,
        arguments: arguments_,
      })
    },
  }
}
