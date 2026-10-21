<script setup lang="ts">
/**
 * 节点展开卡（黄金图 §2 · R6-S3fix）：与激活行贴合为同一容器（2px 浅蓝描边环，
 * 白底）；内部 ≈1.73:1 两栏（黄金图 804:464）。
 *
 * 左栏三组（教师提问/学生活动/板书要点）真实落库编辑，内容置于浅灰圆角盒；
 * 右栏：教材依据卡（真实 SourceLocator）+ 教学提示卡（无 hints 诚实隐藏）。
 * 时长编辑**不在展开卡内**（黄金图无此行）——入口已下沉到折叠行的时长文字（ClassroomPane）。
 */
import { computed, ref } from 'vue'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import { type PrepNodeItem, type PrepBlueprintStage } from '@/features/teacher-prep-center/api/prepCenterApi'
import EditableGroup from './EditableGroup.vue'
import TextbookEvidenceCard from './TextbookEvidenceCard.vue'

const props = defineProps<{
  sessionId: string
  nodeIndex: number
  node: PrepBlueprintStage
}>()

const store = usePrepSessionStore()

const editingGroups = ref({ teacher_questions: false, student_activities: false, board_notes: false })
const savingKey = ref<string | null>(null)
const rowError = ref<string | null>(null)

const locator = computed(() => store.current?.artifact.context?.locator ?? null)

function groupOf(key: 'teacher_questions' | 'student_activities' | 'board_notes'): PrepNodeItem[] {
  const v = (props.node as unknown as Record<string, unknown>)[key]
  return Array.isArray(v) ? (v as PrepNodeItem[]) : []
}

async function saveGroup(key: 'teacher_questions' | 'student_activities' | 'board_notes', items: { text: string }[]) {
  savingKey.value = key
  rowError.value = null
  const ok = await store.editNode(props.sessionId, {
    idempotency_key: (crypto?.randomUUID?.() ?? `pc-${Date.now()}-${Math.random().toString(36).slice(2)}`),
    expected_revision: store.current?.session.revision ?? 1,
    node_index: props.nodeIndex,
    [key]: items,
  })
  savingKey.value = null
  if (!ok) {
    rowError.value = describeError(store.currentError)
    setTimeout(() => (rowError.value = null), 6000)
  }
}

function describeError(msg: string | null): string {
  return msg || '保存失败，请重试'
}
</script>

<template>
  <div class="pc-expand">
    <div class="pc-expand__cols">
      <!-- 左栏 ≈2/3：三组 -->
      <div class="pc-expand__left">
        <EditableGroup
          v-model:editing="editingGroups.teacher_questions"
          title="教师提问"
          icon="question"
          :items="groupOf('teacher_questions')"
          :ordered="true"
          :saving="savingKey === 'teacher_questions'"
          @save="(items) => saveGroup('teacher_questions', items)"
        />
        <EditableGroup
          v-model:editing="editingGroups.student_activities"
          title="学生活动"
          icon="student"
          :items="groupOf('student_activities')"
          :saving="savingKey === 'student_activities'"
          @save="(items) => saveGroup('student_activities', items)"
        />
        <EditableGroup
          v-model:editing="editingGroups.board_notes"
          title="板书要点"
          icon="board"
          :items="groupOf('board_notes')"
          :saving="savingKey === 'board_notes'"
          @save="(items) => saveGroup('board_notes', items)"
        />
        <p v-if="rowError" class="pc-expand__error">{{ rowError }}</p>
      </div>

      <!-- 右栏 ≈1/3：教材依据 + 教学提示 -->
      <div class="pc-expand__right">
        <TextbookEvidenceCard :session-id="sessionId" :locator="locator" />

        <!-- 教学提示：真实 hints；无则诚实隐藏整卡（反空壳） -->
        <div v-if="(node.hints || []).length" class="pc-hints">
          <div class="pc-hints__head">
            <span class="pc-hints__icon" aria-hidden="true">
              <svg viewBox="0 0 16 16">
                <path d="M8 1.8a4.6 4.6 0 0 1 2.6 8.4c-.5.4-.8.9-.8 1.5v.5H6.2v-.5c0-.6-.3-1.1-.8-1.5A4.6 4.6 0 0 1 8 1.8Z" fill="none" stroke="currentColor" stroke-width="1.3" />
                <path d="M6.5 14h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
              </svg>
            </span>
            <span class="pc-hints__name">教学提示</span>
            <span class="pc-hints__pill">基于学情</span>
          </div>
          <ul class="pc-hints__list">
            <li v-for="(h, i) in node.hints" :key="i">{{ h.text }}</li>
          </ul>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 展开体：白底（黄金图实测 254,254,254）+ 2px 浅蓝描边环（与激活行连成一体）。
   左右下描边补全环形；顶部无边框与行头连续（黄金图行头/展开体之间无分隔线）。 */
.pc-expand {
  padding: 14px 18px 16px;
  background: var(--pc-panel);
  border: 2px solid var(--pc-primary-ring);
  border-top: none;
  border-radius: 0 0 var(--pc-radius-card) var(--pc-radius-card);
}
.pc-expand__cols {
  /* 黄金图两栏 804:464 ≈ 1.73:1（原 2fr/1.05fr ≈ 1.9:1 右栏过窄致封面缩格） */
  display: grid;
  grid-template-columns: 1.733fr 1fr;
  gap: 20px;
}
.pc-expand__left {
  min-width: 0;
}
.pc-expand__right {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pc-expand__error {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--pc-danger);
}

/* 教学提示卡（展开体为白底后按黄金图补发丝线边界） */
.pc-hints {
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  padding: 14px 16px;
}
.pc-hints__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pc-hints__icon {
  display: inline-flex;
  width: 17px;
  height: 17px;
  color: var(--pc-warning);
}
.pc-hints__icon svg {
  width: 17px;
  height: 17px;
}
.pc-hints__name {
  font-size: 14px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-hints__pill {
  padding: 1px 8px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 11px;
}
.pc-hints__list {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.pc-hints__list li {
  position: relative;
  padding-left: 16px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--pc-text-muted);
}
.pc-hints__list li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0.72em;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--pc-primary);
}
</style>
