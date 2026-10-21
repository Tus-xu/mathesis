<template>
  <Teleport to="body">
    <div v-if="open" class="mxd-board" data-testid="mxd-drawboard">
      <!-- 顶栏 -->
      <div class="mxd-board__top">
        <span class="mxd-board__title">📐 绘图工作台</span>
        <span v-if="contextLabel" class="mxd-board__ctx" data-testid="mxd-board-ctx">{{ contextLabel }}</span>
        <div class="tv3-seg">
          <button
            v-for="m in MODES" :key="m.id"
            class="tv3-seg__btn" :class="{ 'is-active': mode === m.id }"
            :data-testid="`mxd-board-tab-${m.id}`"
            @click="mode = m.id"
          >{{ m.label }}</button>
        </div>
        <span class="mxd-board__note">{{ modeNote }}</span>
        <div class="tv3-card__spacer" />
        <button
          v-if="mode !== 'hand'"
          class="tv3-btn tv3-btn--sm tv3-btn--gold"
          type="button" data-testid="mxd-board-savelib" @click="startSave"
        >💾 存入图形库</button>
        <button class="tv3-btn tv3-btn--sm" type="button" data-testid="mxd-board-close" @click="close">关闭 ✕</button>
      </div>

      <div class="mxd-board__body">
        <!-- 左：图形库 -->
        <aside class="mxd-board__lib">
          <div class="mxd-board__lib-head">
            <span class="tv3-form-label" style="margin: 0">图形库</span>
            <span class="mxd-board__lib-count">{{ libItems.length }}</span>
          </div>
          <div class="mxd-board__lib-scroll">
            <div v-if="!libItems.length" class="mxd-board__lib-empty">
              还没有保存的图形<br>画好后点右上「存入图形库」
            </div>
            <div
              v-for="it in libItems" :key="it.id"
              class="mxd-board__lib-item" :data-testid="`mxd-lib-${it.id}`"
              :title="it.shared ? '教研组共享' : '个人图形'"
              @click="applyLib(it)"
            >
              <div class="mxd-board__lib-thumb" v-html="it.thumb" />
              <div class="mxd-board__lib-meta">
                <div class="mxd-board__lib-name">{{ it.name }}</div>
                <div class="mxd-board__lib-sub">
                  <span class="tv3-tag" :class="it.kind === 'fx' ? 'tv3-tag--primary' : 'tv3-tag--gold'">{{ it.kind === 'fx' ? '函数' : '画布' }}</span>
                  {{ it.author }}{{ it.shared ? ' · 共享' : '' }}
                </div>
              </div>
            </div>
          </div>
        </aside>

        <!-- 主区：三模式 -->
        <div class="mxd-board__main">
          <KeepAlive>
            <FxMode v-if="mode === 'fx'" ref="fxRef" :initial-expr="reopenExpr" @insert="onInsert" @changed="onFxChanged" />
            <FreeMode v-else-if="mode === 'free'" ref="freeRef" :initial-records="reopenRecords" @insert="onInsert" @changed="onFreeChanged" />
            <GeomMode v-else-if="mode === 'geom'" ref="geomRef" @insert="onInsert" />
            <GeometryDirector v-else-if="mode === 'director'" @insert="onInsert" />
            <HandMode v-else @insert="onInsert" />
          </KeepAlive>
        </div>
      </div>

      <!-- 保存到图形库弹窗 -->
      <div v-if="saving" class="mxd-board__mask" @click.self="saving = false">
        <div class="mxd-board__save" data-testid="mxd-board-save">
          <div class="mxd-board__save-title">存入图形库</div>
          <div v-if="saveDesc?.thumb" class="mxd-board__save-thumb"><img :src="saveDesc.thumb" alt="图形快照"></div>
          <input v-model="saveName" class="tv3-input" placeholder="图形名称，如：抛物线焦点弦模型" data-testid="mxd-board-save-name">
          <label class="mxd-board__save-share">
            <input v-model="saveShared" type="checkbox" style="accent-color: var(--tv3-gold)">
            共享到教研组（备课组可见）
          </label>
          <div class="mxd-board__save-acts">
            <button class="tv3-btn tv3-btn--sm" type="button" @click="saving = false">取消</button>
            <button class="tv3-btn tv3-btn--sm tv3-btn--primary" type="button" :disabled="!saveName.trim() || savingBusy" data-testid="mxd-board-save-ok" @click="confirmSave">{{ savingBusy ? '保存中…' : '保存' }}</button>
          </div>
        </div>
      </div>

      <!-- 空画布保存提示 -->
      <div v-if="saveToast" class="mxd-board__toast">{{ saveToast }}</div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * DrawBoard —— 绘图工作台主容器（P1）
 * 三模式：函数绘图 / 自由画布 / 手写公式；左侧图形库（个人 + 教研组共享）。
 * 重开编辑：reopen 传入已插入元素的配方，改完 insert 回写原元素。
 * 红线：插入的公式/图形都是结构化数据；识别公式必须经 MathField 审查。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { v3Api } from '@/api/teacherV3'
import type { V3DrawRecord, V3FigureLibraryItem } from '@/types/teacherV3'
import FxMode from './FxMode.vue'
import FreeMode from './FreeMode.vue'
import GeomMode from '../geom/GeomMode.vue'
import GeometryDirector from '../director/GeometryDirector.vue'
import HandMode from './HandMode.vue'
import type { V3DrawInsert } from './drawCore'

export interface DrawReopen {
  mode: 'fx' | 'free' | 'geom'
  records?: V3DrawRecord[]
  expr?: string
  elementId: string
}

const props = withDefaults(defineProps<{ open: boolean; reopen?: DrawReopen | null; contextLabel?: string }>(), { reopen: null, contextLabel: '' })
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'insert', payload: V3DrawInsert, elementId?: string): void
  /** C2 全局化：手动关闭且画布有内容时上抛快照，由宿主暂存（画一半关掉不丢） */
  (e: 'stash', desc: { kind: 'free' | 'fx'; thumb: string; records?: V3DrawRecord[]; expr?: string }): void
}>()

type Mode = 'fx' | 'free' | 'geom' | 'hand' | 'director'
const MODES: { id: Mode; label: string; note: string }[] = [
  { id: 'fx', label: '函数绘图', note: '输入表达式 → 可调参图像，插入后放映态参数可拖' },
  { id: 'free', label: '自由画布', note: '画笔 / 直线 / 圆 / 多边形 + 规整图形，配方结构化可重开' },
  { id: 'geom', label: '立体几何', note: '平行六面体/棱锥骨架 + 棱上点/中点/交点/截面构造，依赖可拖动、虚实自动、标签入图' },
  { id: 'hand', label: '手写公式', note: '手写 → 识别为 LaTeX → 编辑器审查 → 插入' },
  { id: 'director', label: '构图导演（实验）', note: '自然语言 → 可审查的构造步骤 + 确定性图形；首批支持六类任务' },
]

const mode = ref<Mode>('fx')
const libItems = ref<V3FigureLibraryItem[]>([])
const fxRef = ref<InstanceType<typeof FxMode> | null>(null)
const freeRef = ref<InstanceType<typeof FreeMode> | null>(null)
const geomRef = ref<InstanceType<typeof GeomMode> | null>(null)
const reopenRecords = ref<V3DrawRecord[]>([])
const reopenExpr = ref('')

const saving = ref(false)
const savingBusy = ref(false)
const saveName = ref('')
const saveShared = ref(false)
const saveDesc = ref<{ kind: 'free' | 'fx'; thumb: string; records?: V3DrawRecord[]; expr?: string } | null>(null)
const saveToast = ref('')

const modeNote = computed(() => MODES.find((m) => m.id === mode.value)?.note || '')

function close() {
  /* C2：主动关闭且画布有内容 → 上抛暂存快照（插入路径 onInsert 直接关，不走此处，不重复暂存） */
  try {
    if (mode.value === 'free' || mode.value === 'fx' || mode.value === 'geom') {
      const desc = mode.value === 'free' ? freeRef.value?.describe() : mode.value === 'geom' ? geomRef.value?.describe() : fxRef.value?.describe()
      const hasContent = desc && (desc.kind === 'fx' ? !!desc.expr : !!(desc.records && desc.records.length))
      if (desc && hasContent) emit('stash', desc)
    }
  } catch { /* 暂存失败不阻塞关闭 */ }
  emit('update:open', false)
}

async function loadLib() {
  try {
    const r = await v3Api.draw.library()
    libItems.value = r.data.items
  } catch { libItems.value = [] }
}

function onInsert(payload: V3DrawInsert) {
  const elementId = props.reopen?.elementId
  emit('insert', payload, elementId)
  emit('update:open', false)
}

function onFxChanged(_expr: string) { /* 预留：脏状态标记 */ }
function onFreeChanged(_records: V3DrawRecord[]) { /* 预留：脏状态标记 */ }

async function applyLib(it: V3FigureLibraryItem) {
  if (it.kind === 'free' && it.records?.length && it.records[0]?.kind === 'geomdoc') {
    mode.value = 'geom'
    await nextTick()
    geomRef.value?.loadRecords(it.records)
  } else if (it.kind === 'free' && it.records?.length) {
    mode.value = 'free'
    await nextTick()
    freeRef.value?.loadRecords(it.records)
  } else if (it.kind === 'fx' && it.expr) {
    mode.value = 'fx'
    await nextTick()
    fxRef.value?.loadExpr(it.expr)
  }
}

function startSave() {
  const desc = mode.value === 'free' ? freeRef.value?.describe() : mode.value === 'geom' ? geomRef.value?.describe() : fxRef.value?.describe()
  if (!desc) {
    saveToast.value = '当前画布为空，先画点内容再保存'
    window.setTimeout(() => { saveToast.value = '' }, 1800)
    return
  }
  saveDesc.value = desc
  saveName.value = desc.kind === 'fx' ? `函数 ${desc.expr?.slice(0, 18) || ''}` : `画布图形 ${new Date().getMonth() + 1}/${new Date().getDate()}`
  saveShared.value = false
  saving.value = true
}

async function confirmSave() {
  if (!saveDesc.value || !saveName.value.trim()) return
  savingBusy.value = true
  try {
    const r = await v3Api.draw.saveLibrary({
      name: saveName.value.trim(),
      kind: saveDesc.value.kind,
      thumb: saveDesc.value.thumb,
      records: saveDesc.value.records,
      expr: saveDesc.value.expr,
      shared: saveShared.value,
    })
    libItems.value.unshift(r.data)
    saving.value = false
  } catch {
    savingBusy.value = false
  } finally {
    savingBusy.value = false
  }
}

function onKeydown(ev: KeyboardEvent) {
  if (!props.open) return
  if (ev.key === 'Escape' && !saving.value) {
    const tag = (ev.target as HTMLElement)?.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA') return
    close()
  }
}

watch(() => props.open, async (v) => {
  if (!v) return
  loadLib()
  if (props.reopen) {
    mode.value = props.reopen.mode
    reopenRecords.value = props.reopen.records || []
    reopenExpr.value = props.reopen.expr || ''
    await nextTick()
    if (props.reopen.mode === 'free' && reopenRecords.value.length) freeRef.value?.loadRecords(reopenRecords.value)
    if (props.reopen.mode === 'geom' && reopenRecords.value.length) geomRef.value?.loadRecords(reopenRecords.value)
    if (props.reopen.mode === 'fx' && reopenExpr.value) fxRef.value?.loadExpr(reopenExpr.value)
  } else {
    reopenRecords.value = []
    reopenExpr.value = ''
  }
})

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  if (props.open) loadLib()
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.mxd-board {
  position: fixed; inset: 0; z-index: 300;
  background: rgba(7, 26, 50, 0.55); backdrop-filter: blur(2px);
  display: flex; flex-direction: column; padding: 26px 46px 30px;
}
.mxd-board__top {
  display: flex; align-items: center; gap: 14px; padding: 10px 16px;
  background: #fff; border-radius: 14px 14px 0 0; border-bottom: 1px solid var(--tv3-line2);
}
.mxd-board__title { font-size: 15.5px; font-weight: 800; color: var(--tv3-ink); white-space: nowrap; }
.mxd-board__ctx {
  font-size: 11.5px; color: #0e7490; background: var(--tv3-gold-soft, #fdf8ec);
  border: 1px solid #ecd3a1; border-radius: 999px; padding: 3px 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 40%;
}
.mxd-board__note { font-size: 11.5px; color: var(--tv3-ink3); }
.mxd-board__body { flex: 1; min-height: 0; display: flex; background: #fff; border-radius: 0 0 14px 14px; overflow: hidden; position: relative; }
.mxd-board__lib {
  width: 232px; border-right: 1px solid var(--tv3-line2); background: #fbfcfe;
  display: flex; flex-direction: column; min-height: 0;
}
.mxd-board__lib-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px 8px; }
.mxd-board__lib-count { font-size: 11px; color: var(--tv3-ink3); font-family: var(--tv3-font-num); }
.mxd-board__lib-scroll { flex: 1; overflow-y: auto; padding: 0 10px 12px; }
.mxd-board__lib-empty {
  margin-top: 20px; text-align: center; font-size: 12px; color: var(--tv3-ink3); line-height: 1.9;
  padding: 0 12px;
}
.mxd-board__lib-item {
  display: flex; gap: 8px; padding: 7px 8px; border-radius: 10px; cursor: pointer;
  border: 1px solid transparent;
}
.mxd-board__lib-item:hover { background: #fff; border-color: var(--tv3-line); }
.mxd-board__lib-thumb {
  width: 62px; height: 42px; border-radius: 6px; border: 1px solid var(--tv3-line);
  background: #fff; flex-shrink: 0; overflow: hidden;
}
.mxd-board__lib-name { font-size: 12.5px; font-weight: 600; color: var(--tv3-ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mxd-board__lib-sub { font-size: 10.5px; color: var(--tv3-ink3); margin-top: 3px; display: flex; align-items: center; gap: 4px; }
.mxd-board__main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.mxd-board__mask {
  position: absolute; inset: 0; background: rgba(7, 26, 50, 0.35);
  display: grid; place-items: center; z-index: 10;
}
.mxd-board__save {
  width: 380px; background: #fff; border-radius: 14px; padding: 18px;
  display: flex; flex-direction: column; gap: 12px;
  box-shadow: 0 18px 50px rgba(7, 26, 50, 0.28);
}
.mxd-board__save-title { font-size: 15px; font-weight: 700; }
.mxd-board__save-thumb {
  border: 1px solid var(--tv3-line); border-radius: 10px; padding: 8px; background: #fbfcfe;
  max-height: 130px; overflow: hidden; display: grid; place-items: center;
}
.mxd-board__save-thumb img { max-width: 100%; max-height: 110px; }
.mxd-board__save-share { font-size: 12.5px; color: var(--tv3-ink2); display: inline-flex; gap: 6px; align-items: center; cursor: pointer; }
.mxd-board__save-acts { display: flex; justify-content: flex-end; gap: 8px; }
.mxd-board__toast {
  position: absolute; top: 64px; left: 50%; transform: translateX(-50%);
  background: var(--tv3-ink); color: #fff; font-size: 12.5px;
  padding: 8px 18px; border-radius: 999px; z-index: 20;
}
</style>
