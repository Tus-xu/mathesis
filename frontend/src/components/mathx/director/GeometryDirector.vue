<template>
  <div class="gdir" data-testid="mxd-director">
    <!-- 能力声明（诚实边界） -->
    <div class="gdir__note">
      <b>实验区 · 首批支持六类任务</b>：多面体截面 / 球的截面 / 线面位置关系 / 圆锥曲线切线与焦点弦 / 函数交点与参数 / 立体展开。
      构造步骤与图形全部<b>确定性生成</b>；逐步显隐动画与任意约束构图属后续/后端能力，不做假装。
    </div>

    <!-- 自然语言入口 -->
    <div class="gdir__nl">
      <textarea
        v-model="nlText" class="tv3-textarea" rows="2" style="font-size: 12.5px"
        placeholder="例如：正方体过三条棱的中点作截面 / 球被平面截出的截面 / 椭圆的切线与焦点弦 / k 变化时交点怎么动 / 正方体展开图"
        data-testid="gdir-nl-input"
      />
      <button class="tv3-btn tv3-btn--sm tv3-btn--ghost-ai" type="button" :disabled="!nlText.trim()" data-testid="gdir-nl-run" @click="runNL">✦ 生成构造方案</button>
    </div>
    <div v-if="nlFallback" class="gdir__fallback" data-testid="gdir-fallback">
      该构造不在首批支持范围。当前支持：{{ supportedSummary }}。请在下方选择任务模板，或换一种说法（含关键词）。
    </div>

    <!-- 任务选择 -->
    <div class="gdir__tasks" data-testid="gdir-tasks">
      <button
        v-for="t in tasks" :key="t.id"
        class="gdir__task" :class="{ 'is-active': task?.id === t.id }"
        :data-testid="`gdir-task-${t.id}`"
        type="button" @click="pick(t)"
      >
        <b>{{ t.title }}</b>
        <span class="gdir__taskcat">{{ t.category }}</span>
      </button>
    </div>

    <!-- 选中任务：能力 + 步骤 + 预览 -->
    <template v-if="task">
      <div class="gdir__cap" data-testid="gdir-capability">
        <div><b>能做什么：</b>{{ task.capability }}</div>
        <div v-if="task.limitation" style="color: #b45309"><b>边界：</b>{{ task.limitation }}</div>
      </div>

      <div class="gdir__cols">
        <!-- 步骤卡（依赖可解释） -->
        <div class="gdir__steps" data-testid="gdir-steps">
          <div class="gdir__sec">构造步骤（{{ steps.length }} 步 · 依赖关系可解释）</div>
          <div v-for="st in steps" :key="st.no" class="gdir__step" :data-testid="`gdir-step-${st.no}`">
            <span class="gdir__stepno">{{ st.no }}</span>
            <div style="flex: 1; min-width: 0">
              <div style="font-size: 12px; line-height: 1.55">{{ st.text }}</div>
              <div style="font-size: 10.5px; color: var(--tv3-ink3); margin-top: 2px">
                生成：<span class="tv3-tag" style="font-size: 9.5px">{{ st.object }}</span>
                <template v-if="st.deps.length">　依赖：<span style="color: var(--tv3-ink4)">{{ st.deps.join('、') }}</span></template>
              </div>
            </div>
          </div>
        </div>
        <!-- 预览（GeoFigure 自带参数滑杆，参数即步骤中的量） -->
        <div class="gdir__preview">
          <div class="gdir__sec">实时预览（拖动滑杆 = 执行对应步骤的量）</div>
          <GeoFigure
            :key="task.presetId"
            :preset-id="task.presetId"
            :params="params"
            :interactive="true"
            :height="300"
            boxed
            show-badge
            data-testid="gdir-preview"
            @update:params="params = $event"
          />
        </div>
      </div>

      <div class="gdir__ops">
        <span style="font-size: 11px; color: var(--tv3-ink4); flex: 1">插入为结构化几何元素（参数可继续调整，teacher_confirmed=false）</span>
        <button class="tv3-btn tv3-btn--sm" type="button" data-testid="gdir-save-recipe" @click="saveRecipe">存为配方（备课组共享）</button>
        <button class="tv3-btn tv3-btn--sm tv3-btn--gold" type="button" data-testid="gdir-insert" @click="onInsert">插入当前课件</button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * GeometryDirector —— 几何构图导演（B7 实验区）
 * 自然语言 → 任务路由（关键词，诚实 fallback）→ 构造步骤卡（依赖可解释）→ 确定性图形预览 → 插入课件/存配方。
 * 首批六类任务；不在范围的请求如实拒绝（不做无效按钮）。
 */
import { computed, ref } from 'vue'
import GeoFigure from '../GeoFigure.vue'
import {
  DIRECTOR_TASKS, SUPPORTED_SUMMARY, parseDirectorInput, taskDefaults, taskPreset,
  type DirectorStep, type DirectorTask,
} from './tasks'
import type { V3DrawInsert } from '../draw/drawCore'
import { v3Api } from '@/api/teacherV3'
import { useToastStore } from '@/stores/toast'

const emit = defineEmits<{ (e: 'insert', payload: V3DrawInsert): void }>()

const tasks = DIRECTOR_TASKS
const supportedSummary = SUPPORTED_SUMMARY
const task = ref<DirectorTask | null>(null)
const nlText = ref('')
const nlFallback = ref(false)
const params = ref<Record<string, number>>({})
const savingRecipe = ref(false)
let toast: ReturnType<typeof useToastStore> | null = null
const toastOf = () => (toast ??= useToastStore())

const steps = computed<DirectorStep[]>(() => (task.value ? task.value.buildSteps(params.value) : []))

function pick(t: DirectorTask) {
  task.value = t
  nlFallback.value = false
  params.value = { ...taskDefaults(t) }
}

function runNL() {
  const r = parseDirectorInput(nlText.value)
  if (r.fallback || !r.task) {
    nlFallback.value = true
    task.value = null
    return
  }
  nlFallback.value = false
  pick(r.task)
  toastOf().info(`已按「${r.matchedKeywords.join('、')}」匹配任务：${r.task.title}`)
}

function onInsert() {
  if (!task.value) return
  const payload: V3DrawInsert = {
    type: 'geometry',
    preset_id: task.value.presetId,
    params: { ...params.value },
    label: task.value.title,
  }
  emit('insert', payload)
}

async function saveRecipe() {
  if (!task.value || savingRecipe.value) return
  savingRecipe.value = true
  try {
    await v3Api.figures.saveRecipe({ name: task.value.title + '（导演）', preset_id: task.value.presetId, params: { ...params.value }, school_shared: true, note: task.value.capability })
    toastOf().success('配方已存入备课组共享（可在资源中心查看）')
  } catch {
    toastOf().error('保存失败，请检查网络后重试')
  } finally {
    savingRecipe.value = false
  }
}

void taskPreset
</script>

<style scoped>
.gdir { display: flex; flex-direction: column; gap: 10px; padding: 4px 2px; }
.gdir__note {
  font-size: 11.5px; line-height: 1.6; color: var(--tv3-ink2);
  padding: 7px 10px; border-radius: 10px;
  background: var(--tv3-gold-soft, #fdf6e3); border: 1px dashed var(--tv3-gold, #c99735);
}
.gdir__nl { display: flex; flex-direction: column; gap: 6px; }
.gdir__nl .tv3-btn { align-self: flex-end; }
.gdir__fallback {
  font-size: 12px; color: #b1382c; padding: 8px 10px; border-radius: 10px;
  background: #fdf3f3; border: 1px solid #f0c9c6; line-height: 1.6;
}
.gdir__tasks { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 6px; }
.gdir__task {
  border: 1px solid var(--tv3-line); border-radius: 10px; background: #fff;
  padding: 7px 10px; cursor: pointer; display: flex; flex-direction: column; gap: 3px; text-align: left;
}
.gdir__task.is-active { border-color: var(--tv3-gold, #c99735); background: var(--tv3-gold-soft, #fdf6e3); }
.gdir__task b { font-size: 12px; }
.gdir__taskcat { font-size: 10px; color: var(--tv3-ink3); }
.gdir__cap {
  font-size: 11.5px; line-height: 1.6; color: var(--tv3-ink2);
  padding: 7px 10px; border-radius: 10px; background: var(--tv3-bg2);
  display: flex; flex-direction: column; gap: 3px;
}
.gdir__cols { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; align-items: start; }
.gdir__sec { font-size: 11.5px; font-weight: 700; margin-bottom: 6px; }
.gdir__steps { display: flex; flex-direction: column; gap: 5px; max-height: 300px; overflow-y: auto; }
.gdir__step {
  display: flex; gap: 8px; padding: 6px 8px;
  border: 1px solid var(--tv3-line); border-radius: 10px; background: #fff;
}
.gdir__stepno {
  width: 20px; height: 20px; border-radius: 6px; flex-shrink: 0;
  background: var(--tv3-navy, #0f4787); color: #fff; font-size: 11px; font-weight: 700;
  display: grid; place-items: center; margin-top: 2px;
}
.gdir__preview { border: 1px solid var(--tv3-line); border-radius: 10px; padding: 8px; }
.gdir__ops { display: flex; gap: 8px; align-items: center; }
</style>
