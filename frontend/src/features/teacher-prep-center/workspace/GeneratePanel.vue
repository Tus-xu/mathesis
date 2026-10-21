<script setup lang="ts">
/**
 * S4/S5 空态生成面板（G11 空态出路 / G13 真实进度 / G14 单页重试）。
 *
 * - 空态：真实可达下一步按钮（确认教学路径并生成 / 生成课件 / 生成教案）；
 * - 生成中：阶段文案 + 真实 n/N（enrich 作业 durable 事件），无假进度条；
 * - 失败：保留已完成部分 + 单页「重试该页」（ppt_v2 /regen），不整份重生成。
 */
import { computed } from 'vue'

import { GENERATE } from '@/features/teacher-prep-center/copy/prepCopy'
import { useDeckEditorStore } from '@/features/teacher-prep-center/stores/deckEditor'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import { useGenerate } from './useGenerate'

const props = defineProps<{
  /** 空态主标题（如「课件尚未生成」）。 */
  emptyTitle: string
  /** 空态说明（同源投影语义）。 */
  emptyDesc: string
  /** 生成按钮目标标签（如「生成课件」）。 */
  genLabel: string
}>()

const prep = usePrepSessionStore()
const deckStore = useDeckEditorStore()
const gen = useGenerate()

/** 蓝图未确认 → 按钮自陈「确认并生成」；无蓝图 → 引导去课堂设计（真实跳转）。 */
const hasBlueprint = computed(() => !!prep.current?.artifact?.blueprint)
const needsConfirm = computed(
  () => prep.current?.artifact?.blueprint?.status === 'proposal',
)
const buttonLabel = computed(() =>
  needsConfirm.value ? `${GENERATE.confirmThenGen}（${props.genLabel}）` : props.genLabel,
)

/** G13：真实 n/N（enrich 作业 progress 事件）；无事件时不显示数字。 */
const progress = computed(() => deckStore.enrichProgress)
const failedPages = computed(() => deckStore.enrichFailedPages)

async function onGenerate() {
  await gen.generate()
}

function onRetryPage(slideId: string) {
  void deckStore.retryFailedPage(slideId, '请重新充实本页完整板书内容')
}
</script>

<template>
  <div class="pc-gen" role="status">
    <!-- 空态（G11：可点击且真实有效的下一步） -->
    <template v-if="gen.phase.value === 'idle'">
      <p class="pc-gen__title">{{ emptyTitle }}</p>
      <p class="pc-gen__desc">{{ emptyDesc }}</p>
      <button
        v-if="hasBlueprint"
        type="button"
        class="pc-gen__btn"
        :disabled="gen.busy.value"
        @click="onGenerate"
      >
        {{ buttonLabel }}
      </button>
      <p v-else class="pc-gen__desc">尚未生成教学路径，请先在「课堂设计」中与 AI 确认教学路径。</p>
    </template>

    <!-- 生成中（G13：阶段 + 真实 n/N） -->
    <template v-else-if="gen.phase.value !== 'done' && gen.phase.value !== 'error'">
      <span class="pc-gen__spinner" aria-hidden="true"></span>
      <p class="pc-gen__title">{{ gen.stage.value }}</p>
      <p v-if="gen.phase.value === 'enriching' && progress" class="pc-gen__progress">
        {{ GENERATE.pageProgress(progress.done, progress.total) }}
      </p>
    </template>

    <!-- 失败（G14：保留已完成部分 + 单页重试） -->
    <template v-else-if="gen.phase.value === 'error'">
      <p class="pc-gen__title pc-gen__title--error">{{ GENERATE.genFailed }}</p>
      <p v-if="gen.error.value" class="pc-gen__desc">{{ gen.error.value }}</p>
      <ul v-if="failedPages.length" class="pc-gen__failed">
        <li v-for="f in failedPages" :key="f.slideId" class="pc-gen__failed-row">
          <span>
            {{ f.index >= 0 ? GENERATE.pageFailed(f.index + 1) : GENERATE.genFailed }}
          </span>
          <button type="button" class="pc-gen__retry-page" @click="onRetryPage(f.slideId)">
            {{ GENERATE.retryPage }}
          </button>
        </li>
      </ul>
      <button type="button" class="pc-gen__btn" :disabled="gen.busy.value" @click="gen.retryAll()">
        {{ GENERATE.retryAll }}
      </button>
    </template>

    <!-- 完成（产物由父组件按真实快照渲染） -->
    <template v-else>
      <p class="pc-gen__title">{{ GENERATE.genDone }}</p>
    </template>
  </div>
</template>

<style scoped>
.pc-gen {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 28px 24px;
  text-align: center;
}
.pc-gen__title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-gen__title--error {
  color: var(--pc-danger);
}
.pc-gen__desc {
  margin: 0;
  max-width: 420px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--pc-text-muted);
}
/* G13：真实数字（服务端 progress 事件），非进度条 */
.pc-gen__progress {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--pc-primary);
}
.pc-gen__btn {
  height: 34px;
  padding: 0 20px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: var(--pc-primary);
  color: #fff;
  font-size: 13.5px;
  font-family: inherit;
  cursor: pointer;
}
.pc-gen__btn:hover:not(:disabled) {
  background: var(--pc-primary-hover, #1d4fd8);
}
.pc-gen__btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
/* G14：失败页清单（保留已完成页，只重试失败页） */
.pc-gen__failed {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  max-width: 380px;
}
.pc-gen__failed-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 10px;
  border: 1px solid var(--pc-danger);
  border-radius: var(--pc-radius-btn);
  font-size: 12.5px;
  color: var(--pc-danger);
}
.pc-gen__retry-page {
  flex: none;
  height: 26px;
  padding: 0 12px;
  border: 1px solid var(--pc-danger);
  border-radius: var(--pc-radius-full);
  background: transparent;
  color: var(--pc-danger);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.pc-gen__retry-page:hover {
  background: var(--pc-danger);
  color: #fff;
}
.pc-gen__spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--pc-primary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: pc-gen-spin 0.8s linear infinite;
}
@keyframes pc-gen-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
