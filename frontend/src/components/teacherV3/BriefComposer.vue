<template>
  <div class="tv3-brief" data-testid="tv3-brief">
    <div class="tv3-brief__head">
      <span class="tv3-ai-badge">✦ AI 备课台</span>
      <span class="tv3-brief__slogan">这节课怎么上？说说要求，或把材料丢进来——<b>材料 / 章节优先</b>，AI 只出可审的大纲草稿</span>
    </div>

    <textarea
      v-model="text" class="tv3-brief__input" rows="2" data-testid="tv3-brief-input"
      placeholder="例如：下周三高二(5)班上《双曲线及其标准方程》第1课时，想用拉链实验引入，例题从基础到提升…"
    />

    <!-- 接地槽：与输入框同视觉级（一等公民），不是隐藏的小 + 号 -->
    <div class="tv3-brief__slots">
      <div class="tv3-brief__slot" :class="{ 'is-filled': docs.length || photos.length }" data-testid="tv3-brief-material">
        <span class="tv3-brief__slotlabel">📎 材料</span>
        <span v-for="d in docs" :key="d" class="tv3-tag tv3-tag--gold tv3-brief__chip" :title="'内容解析在生成时进行，此处仅记录来源'">
          📄 {{ d }}<button class="tv3-brief__chipx" :data-testid="`tv3-brief-doc-x-${d}`" @click="removeDoc(d)">×</button>
        </span>
        <span v-for="i in photos.length" :key="i" class="tv3-tag tv3-tag--gold tv3-brief__chip">🖼 照片{{ i }}<button class="tv3-brief__chipx" :data-testid="`tv3-brief-photo-x-${i}`" @click="removePhoto(i - 1)">×</button></span>
        <span v-if="!docs.length && !photos.length" class="tv3-brief__slothint">教案 / 讲义 / 旧课件（PDF·DOC·PPT·TXT）或题目照片</span>
        <button class="tv3-btn tv3-btn--sm" data-testid="tv3-brief-add-doc" @click="docInput?.click()">＋ 文档</button>
        <button class="tv3-btn tv3-btn--sm" data-testid="tv3-brief-add-photo" @click="photoInput?.click()">＋ 照片</button>
        <input ref="docInput" type="file" accept=".doc,.docx,.pdf,.ppt,.pptx,.txt" multiple hidden @change="onDocs">
        <input ref="photoInput" type="file" accept="image/*" multiple hidden @change="onPhotos">
      </div>

      <div class="tv3-brief__slot" :class="{ 'is-filled': !!chapterId }">
        <span class="tv3-brief__slotlabel">📖 章节</span>
        <select v-model="chapterId" class="tv3-input tv3-brief__select" data-testid="tv3-brief-chapter">
          <option value="">未选（建议选择，用于对齐教材与内容源）</option>
          <optgroup v-for="tb in chapters.textbooks" :key="tb.name" :label="tb.name">
            <option v-for="ch in tb.chapters" :key="ch.id" :value="`${tb.name} ▸ ${ch.path}`">{{ ch.path }}</option>
          </optgroup>
        </select>
      </div>

      <div class="tv3-brief__slot">
        <span class="tv3-brief__slotlabel">🏷 课型</span>
        <div class="tv3-brief__chips">
          <button
            v-for="t in COURSE_TYPES" :key="t" class="tv3-brief__type" :class="{ 'is-on': courseType === t }"
            :data-testid="`tv3-brief-type-${t}`" @click="courseType = t"
          >{{ t }}</button>
        </div>
      </div>

      <div class="tv3-brief__slot">
        <span class="tv3-brief__slotlabel">👥 班级</span>
        <select v-model="classId" class="tv3-input tv3-brief__select tv3-brief__select--sm" data-testid="tv3-brief-class">
          <option v-for="c in classes" :key="c.class_id" :value="c.class_id">{{ c.name }}</option>
        </select>
      </div>
    </div>

    <!-- 风格（可选，有默认；生成后仍可换） -->
    <div class="tv3-brief__slot" :class="{ 'is-filled': true }">
      <span class="tv3-brief__slotlabel">🎨 风格</span>
      <div class="tv3-brief__tpls">
        <button
          v-for="t in templates" :key="t.id" class="tv3-brief__tpl" :class="{ 'is-on': templateId === t.id }"
          :data-testid="`tv3-brief-tpl-${t.id}`" :title="t.recommended_for" @click="templateId = t.id"
        >
          <span class="tv3-brief__tpldot" :style="{ background: t.swatch.bg }" />{{ t.name }}
        </button>
      </div>
    </div>

    <div class="tv3-brief__foot">
      <span class="tv3-brief__route" data-testid="tv3-brief-route">{{ routeHint }}</span>
      <button class="tv3-btn tv3-btn--gold" :disabled="!canSubmit" data-testid="tv3-brief-submit" @click="submit">开始备课 →</button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * C1 AI 备课台（BriefComposer）：课件工坊统一接入口。
 * 审计依据（Desktop\V3课件工坊-审计与创新报告.md §A1）：
 *  - 巨大化的是「备课接入台」不是裸主题生成：材料/章节/课型是与输入框同级的接地槽；
 *  - 四路确定性路由：照片→拍照链路 / 文档→教案直通 / 章节→锚定大纲 / 裸课题→兜底（红字引导）；
 *  - 提交后不直接出整套，统一进大纲确认台；路由预览（routeHint）提交前可见，保持诚实。
 */
import { computed, onMounted, ref } from 'vue'
import { v3Api, type V3TextbookChapters } from '@/api/teacherV3'
import type { V3BriefPayload, V3ClassInfo } from '@/types/teacherV3'

const props = defineProps<{
  classes: V3ClassInfo[]
  templates: { id: string; name: string; style: string; swatch: { bg: string; primary: string; accent: string; light: boolean }; page_kinds: string[]; recommended_for: string }[]
}>()
const emit = defineEmits<{ (e: 'submit', payload: V3BriefPayload): void }>()

const COURSE_TYPES = ['新授课', '习题课', '讲评课', '复习课', '公开课'] as const

const text = ref('')
const chapterId = ref('')
const courseType = ref<string>('新授课')
const classId = ref(props.classes[0]?.class_id || 'c2-05')
const templateId = ref(props.templates[0]?.id || 'tpl-academic-blue')
const docs = ref<string[]>([])
const photos = ref<string[]>([])
const chapters = ref<V3TextbookChapters>({ textbooks: [] })

const docInput = ref<HTMLInputElement | null>(null)
const photoInput = ref<HTMLInputElement | null>(null)

const canSubmit = computed(() => !!(text.value.trim() || docs.value.length || photos.value.length || chapterId.value))

/** 路由预览：提交前就告诉教师会走哪条链路（确定性规则，无假装智能） */
const routeHint = computed(() => {
  if (photos.value.length) return '路由：拍照出课件链路（原图锚定 · 识别块可编辑 · 仍需选识别范围）'
  if (docs.value.length) return '路由：教案直通链路（沿用所选教案的环节结构）'
  if (chapterId.value) return `路由：章节锚定大纲 → 确认大纲后生成（${shortChapter(chapterId.value)}）`
  if (text.value.trim()) return '路由：大纲起步（未选章节/材料，内容匹配度有限——建议补选）'
  return '填写要求、附材料或选章节后开始'
})

const shortChapter = (p: string) => p.split('▸').pop()?.trim() || p

function onDocs(ev: Event) {
  const files = (ev.target as HTMLInputElement).files
  if (!files) return
  for (const f of Array.from(files).slice(0, 4)) if (f.name && !docs.value.includes(f.name)) docs.value.push(f.name)
  ;(ev.target as HTMLInputElement).value = ''
}
function onPhotos(ev: Event) {
  const files = (ev.target as HTMLInputElement).files
  if (!files) return
  for (const f of Array.from(files).filter((x) => x.type.startsWith('image/')).slice(0, 6)) {
    const rd = new FileReader()
    rd.onload = () => { if (typeof rd.result === 'string') photos.value.push(rd.result) }
    rd.readAsDataURL(f)
  }
  ;(ev.target as HTMLInputElement).value = ''
}
function removeDoc(name: string) { docs.value = docs.value.filter((d) => d !== name) }
function removePhoto(i: number) { photos.value.splice(i, 1) }

function submit() {
  if (!canSubmit.value) return
  const chapter = chapterId.value ? chapterId.value : null
  emit('submit', {
    text: text.value.trim(),
    chapter,
    course_type: courseType.value,
    class_id: classId.value,
    template_id: templateId.value,
    docs: [...docs.value],
    photos: [...photos.value],
  })
}

/** 管家深链 / 今日授课「去备」的预填入口（大纲确认保留，不做隐藏跳转） */
function prefill(p: { text?: string; class_id?: string; template_id?: string; course_type?: string }) {
  if (p.text) text.value = p.text
  if (p.class_id && props.classes.some((c) => c.class_id === p.class_id)) classId.value = p.class_id
  if (p.template_id && props.templates.some((t) => t.id === p.template_id)) templateId.value = p.template_id
  if (p.course_type && (COURSE_TYPES as readonly string[]).includes(p.course_type)) courseType.value = p.course_type
}
defineExpose({ prefill })

onMounted(async () => {
  try {
    const r = await v3Api.catalog.textbookChapters()
    chapters.value = r.data
  } catch { /* 章节接口不可用时留空，仍可走兜底路 */ }
})
</script>

<style scoped>
.tv3-brief {
  background: #fff; border: 1.5px solid var(--tv3-ai-border, #c9d7f2); border-radius: var(--tv3-radius-lg, 16px);
  box-shadow: 0 10px 30px rgba(79, 70, 229, 0.08); padding: 16px 18px 12px; margin-bottom: 14px;
}
.tv3-brief__head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
.tv3-brief__slogan { font-size: 12.5px; color: var(--tv3-ink2); }
.tv3-brief__slogan b { color: var(--tv3-primary, #4f46e5); }
.tv3-brief__input {
  width: 100%; box-sizing: border-box; resize: vertical; min-height: 56px;
  border: 1.5px solid var(--tv3-line); border-radius: 12px; padding: 10px 12px;
  font-size: 13.5px; line-height: 1.6; font-family: inherit; color: var(--tv3-ink); outline: none;
  background: #fbfcfe;
}
.tv3-brief__input:focus { border-color: var(--tv3-primary, #4f46e5); background: #fff; }
.tv3-brief__slots { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
.tv3-brief__slot {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  border: 1px dashed var(--tv3-line); border-radius: 10px; padding: 7px 10px; background: #fff;
}
.tv3-brief__slot.is-filled { border-style: solid; border-color: var(--tv3-gold); background: var(--tv3-gold-soft, #fdf8ec); }
.tv3-brief__slotlabel { font-size: 12px; font-weight: 700; color: var(--tv3-ink2); flex-shrink: 0; }
.tv3-brief__slothint { font-size: 11.5px; color: var(--tv3-ink4, #9aa5b5); }
.tv3-brief__select { flex: 1; min-width: 200px; max-width: 560px; font-size: 12.5px; padding: 5px 8px; }
.tv3-brief__select--sm { flex: 0 0 auto; min-width: 120px; }
.tv3-brief__chips { display: flex; gap: 6px; flex-wrap: wrap; }
.tv3-brief__type {
  border: 1px solid var(--tv3-line); background: #fff; border-radius: 999px; padding: 3px 12px;
  font-size: 12px; cursor: pointer; color: var(--tv3-ink2);
}
.tv3-brief__type.is-on { border-color: var(--tv3-primary, #4f46e5); background: rgba(79, 70, 229, 0.08); color: var(--tv3-primary, #4f46e5); font-weight: 700; }
.tv3-brief__tpls { display: flex; gap: 6px; flex-wrap: wrap; }
.tv3-brief__tpl {
  display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--tv3-line); background: #fff;
  border-radius: 999px; padding: 3px 11px; font-size: 12px; cursor: pointer; color: var(--tv3-ink2);
}
.tv3-brief__tpl.is-on { border-color: var(--tv3-gold); background: var(--tv3-gold-soft, #fdf8ec); color: var(--tv3-ink); font-weight: 700; }
.tv3-brief__tpldot { width: 10px; height: 10px; border-radius: 50%; border: 1px solid rgba(0, 0, 0, 0.12); }
.tv3-brief__chip { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; }
.tv3-brief__chipx { border: none; background: none; cursor: pointer; font-size: 12px; color: inherit; padding: 0 1px; line-height: 1; }
.tv3-brief__foot { display: flex; align-items: center; gap: 12px; margin-top: 10px; }
.tv3-brief__route { flex: 1; font-size: 11.5px; color: var(--tv3-ink3); }
</style>
