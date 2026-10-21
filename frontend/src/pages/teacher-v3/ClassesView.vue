<template>
  <div class="tv3-page-shell" data-testid="tv3-classes">
    <TeacherPageHeader title="班级管理" subtitle="建班 · 邀请码 · 成员确认" icon="🏫">
      <button class="tv3-btn tv3-btn--sm tv3-btn--primary" data-testid="tv3-class-create" @click="openCreate">＋ 建班</button>
    </TeacherPageHeader>

    <!-- 空态：直接可建班（PLAN §1.2①） -->
    <TeacherEmptyState
      v-if="!loading && !classes.length"
      title="还没有班级"
      desc="建一个班，把 6 位邀请码告诉学生；学生在「课堂 → 我的班级」输码申请加入，你在这里确认后，任务与学情链路即打通。"
      cta="＋ 建第一个班"
      @cta-click="openCreate"
    />

    <div v-else style="display: grid; grid-template-columns: 340px 1fr; gap: 14px; align-items: start">
      <!-- 左：班级列表 -->
      <div class="tv3-card">
        <div class="tv3-card__head"><span class="tv3-card__title">我的班级</span><span class="tv3-card__sub">{{ classes.length }} 个</span></div>
        <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 6px">
          <TeacherLoading v-if="loading" />
          <div
            v-for="c in classes" :key="c.id"
            class="tv3-row" :class="{ 'is-selected': current?.id === c.id }" style="cursor: pointer"
            :data-testid="`tv3-class-${c.id}`"
            @click="selectClass(c)"
          >
            <div style="flex: 1; min-width: 0">
              <div style="font-size: 13.5px; font-weight: 700">{{ c.name }}</div>
              <div style="font-size: 11.5px; color: var(--tv3-ink3)">
                {{ c.grade || '年级未填' }} · {{ c.students }} 名学生
              </div>
            </div>
            <span v-if="c.inviteCode" class="tv3-tag tv3-tag--gold" style="font-size: 11px" data-testid="tv3-class-invite-code">邀请码 {{ c.inviteCode }}</span>
            <span style="color: var(--tv3-ink4)">›</span>
          </div>
          <div v-if="loadError" class="tv3-tag tv3-tag--danger" style="padding: 8px 12px; border-radius: 8px" data-testid="tv3-classes-load-error">
            班级列表加载失败：{{ loadError }}
            <button class="tv3-btn tv3-btn--sm" style="margin-left: 8px" @click="reload">重试</button>
          </div>
        </div>
      </div>

      <!-- 右：成员管理 -->
      <div v-if="current" class="tv3-card" data-testid="tv3-class-detail">
        <div class="tv3-card__head">
          <span class="tv3-card__title">{{ current.name }} · 成员</span>
          <span class="tv3-card__sub">学生输码申请后需你确认；确认后才计入任务与学情</span>
          <div class="tv3-card__spacer" />
          <button class="tv3-btn tv3-btn--sm" data-testid="tv3-invite-reset" @click="resetInvite">重置邀请码</button>
        </div>
        <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 8px">
          <div v-if="inviteCode" class="tv3-prep__diffitem" style="display: flex; gap: 10px; align-items: center" data-testid="tv3-invite-banner">
            <span style="font-size: 12px; color: var(--tv3-ink2)">当前邀请码</span>
            <b style="font-family: var(--tv3-font-num); font-size: 20px; letter-spacing: 4px">{{ inviteCode }}</b>
            <span class="tv3-tag" style="font-size: 10px">学生：课堂 → 我的班级 → 输码加入</span>
          </div>
          <TeacherLoading v-if="membersLoading" />
          <div v-else-if="!members.length" style="padding: 20px; text-align: center; color: var(--tv3-ink3); font-size: 13px">
            还没有成员。把上方邀请码发给学生，等他们申请加入。
          </div>
          <div
            v-for="m in members" :key="m.id"
            class="tv3-row" :data-testid="`tv3-member-${m.userId}`"
          >
            <div class="tv3-watch__avatar" style="width: 32px; height: 32px; font-size: 13px">{{ (m.nicknameInClass || m.nickname || '？')[0] }}</div>
            <div style="flex: 1; min-width: 0">
              <div style="font-size: 13px; font-weight: 600">
                {{ m.nicknameInClass || m.nickname || '未命名成员' }}
                <span v-if="m.memberRole === 'teacher'" class="tv3-tag" style="font-size: 10px; margin-left: 4px">教师</span>
              </div>
              <div style="font-size: 11px; color: var(--tv3-ink3)">
                {{ m.confirmed ? '已确认' : '等待你确认' }} · 加入于 {{ fmtDate(m.joinedAt) }}
              </div>
            </div>
            <span v-if="m.memberRole !== 'teacher'" class="tv3-tag" :class="m.confirmed ? 'tv3-tag--ok' : 'tv3-tag--warn'" style="font-size: 10px">
              {{ m.confirmed ? '✓ 在班' : 'pending' }}
            </span>
            <template v-if="m.memberRole !== 'teacher'">
              <button
                v-if="!m.confirmed"
                class="tv3-btn tv3-btn--sm tv3-btn--primary" :disabled="acting === m.userId"
                :data-testid="`tv3-member-confirm-${m.userId}`"
                @click="setConfirmed(m, true)"
              >确认入班</button>
              <button
                v-else
                class="tv3-btn tv3-btn--sm" :disabled="acting === m.userId"
                :data-testid="`tv3-member-remove-${m.userId}`"
                @click="setConfirmed(m, false)"
              >移出班级</button>
            </template>
          </div>
        </div>
      </div>
      <div v-else class="tv3-card" style="display: grid; place-items: center; min-height: 240px">
        <div style="color: var(--tv3-ink3); font-size: 13px">← 选择一个班级管理成员</div>
      </div>
    </div>

    <!-- 建班弹层 -->
    <div v-if="createOpen" class="tv3-push" @click.self="createOpen = false">
      <div class="tv3-card tv3-push__panel" style="max-width: 460px" data-testid="tv3-class-create-panel">
        <div class="tv3-card__head">
          <span class="tv3-card__title">建班</span>
          <button class="tv3-btn tv3-btn--sm" @click="createOpen = false">×</button>
        </div>
        <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 10px">
          <label class="tv3-form-label">班级名称 *</label>
          <input v-model="form.name" class="tv3-input" style="background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" data-testid="tv3-class-name-input" placeholder="如：高二（3）班" />
          <label class="tv3-form-label">年级（选填）</label>
          <input v-model="form.grade" class="tv3-input" style="background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" placeholder="如：高二" />
          <div v-if="formError" class="tv3-tag tv3-tag--danger" style="padding: 6px 10px" data-testid="tv3-class-form-error">{{ formError }}</div>
          <div style="display: flex; justify-content: flex-end; gap: 8px">
            <button class="tv3-btn tv3-btn--sm" @click="createOpen = false">取消</button>
            <button class="tv3-btn tv3-btn--sm tv3-btn--primary" :disabled="creating || !form.name.trim()" data-testid="tv3-class-create-submit" @click="submitCreate">
              {{ creating ? '创建中…' : '创建班级' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * ClassesView —— 班级管理（tel PLAN §1.2①）
 * 唯一数据权威 = 8000 /api/classes*（直调，identity/class_members 的 stateful 权威）。
 * 建班 → 邀请码展示 → 成员 pending/confirmed 管理 →（确认后）任务/学情链路打通。
 * 无 mock：加载失败如实报错，空数据给引导。
 */
import { computed, onMounted, ref } from 'vue'
import { api8000, type ApiClassItem, type ApiMemberItem } from '@/api/api8000'
import { TeacherPageHeader, TeacherEmptyState, TeacherLoading } from '@/components/teacherV3/common'
import { useToastStore } from '@/stores/toast'

const toast = useToastStore()
const classes = ref<ApiClassItem[]>([])
const members = ref<ApiMemberItem[]>([])
const current = ref<ApiClassItem | null>(null)
const loading = ref(true)
const membersLoading = ref(false)
const loadError = ref('')
const acting = ref('')

const createOpen = ref(false)
const creating = ref(false)
const form = ref({ name: '', grade: '' })
const formError = ref('')

const inviteCode = computed(() => current.value?.inviteCode || '')

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

async function reload() {
  loading.value = true
  loadError.value = ''
  try {
    const r = await api8000.classes.list()
    classes.value = r.items
    // 默认选中：保持当前选择，否则取列表第一项（真实 id，无 mock 残留）
    if (!current.value || !classes.value.some((c) => c.id === current.value?.id)) {
      current.value = classes.value[0] ?? null
    }
    if (current.value) void loadMembers(current.value.id)
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '班级列表加载失败'
  } finally {
    loading.value = false
  }
}

async function loadMembers(classId: string) {
  membersLoading.value = true
  try {
    const r = await api8000.classes.members(classId)
    members.value = r.items
  } catch (e) {
    members.value = []
    toast.error(e instanceof Error ? `成员加载失败：${e.message}` : '成员加载失败')
  } finally {
    membersLoading.value = false
  }
}

function selectClass(c: ApiClassItem) {
  current.value = c
  void loadMembers(c.id)
}

function openCreate() {
  form.value = { name: '', grade: '' }
  formError.value = ''
  createOpen.value = true
}

async function submitCreate() {
  if (!form.value.name.trim()) return
  creating.value = true
  formError.value = ''
  try {
    const created = await api8000.classes.create({ name: form.value.name.trim(), grade: form.value.grade.trim() || undefined })
    createOpen.value = false
    toast.success(`班级「${created.name}」已创建，邀请码 ${created.inviteCode}`)
    current.value = {
      id: created.id,
      name: created.name,
      grade: created.grade || '',
      myRole: 'teacher',
      confirmed: true,
      inviteCode: created.inviteCode,
      createdAt: null,
      students: 0,
    }
    await reload()
  } catch (e) {
    formError.value = e instanceof Error ? e.message : '创建失败，请重试'
  } finally {
    creating.value = false
  }
}

async function setConfirmed(m: ApiMemberItem, confirmed: boolean) {
  if (!current.value) return
  if (!confirmed && !window.confirm(`确定把「${m.nicknameInClass || m.nickname || '该学生'}」移出班级？移出后其任务与学情数据不再计入本班。`)) return
  acting.value = m.userId
  try {
    await api8000.classes.setMemberConfirmed(current.value.id, m.userId, confirmed)
    toast.success(confirmed ? '已确认入班：学生任务中心与学情即刻生效' : '已移出班级')
    await loadMembers(current.value.id)
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '操作失败，请重试')
  } finally {
    acting.value = ''
  }
}

async function resetInvite() {
  if (!current.value) return
  if (!window.confirm('重置后旧邀请码立即失效，需要把新码重新告知学生。确定重置？')) return
  try {
    const r = await api8000.classes.resetInviteCode(current.value.id)
    if (current.value) current.value.inviteCode = r.inviteCode
    toast.success(`邀请码已重置为 ${r.inviteCode}（旧码已失效）`)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '重置失败（仅班级创建教师可重置）')
  }
}

onMounted(reload)
</script>

<style scoped>
.tv3-page-shell {
  margin: -22px; padding: 22px; min-height: calc(100vh - var(--tv3-topbar-h));
  background: var(--teacher-bg-gradient);
  box-sizing: border-box;
}
/* 页内弹层（tv3-push 在共享样式中无定义——本页自带，避免透明裸 div） */
.tv3-push {
  position: fixed; inset: 0; z-index: 1200;
  background: rgba(9, 20, 40, 0.45);
  display: grid; place-items: center;
}
.tv3-push__panel { width: calc(100vw - 48px); box-shadow: var(--tv3-shadow-lg, 0 18px 50px rgba(9, 20, 40, 0.25)); }
</style>
