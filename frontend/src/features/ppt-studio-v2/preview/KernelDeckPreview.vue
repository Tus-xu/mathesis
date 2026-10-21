<template>
  <!--
    VNext 备课课件 · PPT 内核真实渲染预览（2026-09-14）。

    这是 `mode=vnext` 的**唯一**工作面：不再把 TeachingDeckSpec 的文本摊成两个文本框，
    而是展示 PPT 内核（8200 VPL + 8201 layout/builder/PowerPoint COM）真实渲染出的逐页版面。

    诚实性（对齐 T22 反硬编码/不假装）：
    - 生成中：显示服务端 job 的真实 stage（无进度就只显示"生成中"，不造假进度条）；
    - 失败：原样显示内核给的原因（例如「visual plan QA 未通过: summary-too-thin」）并可重试；
    - 页图缺失：按页码占位，不塞空白图冒充已生成。
  -->
  <section class="kdp" data-testid="tv3-vnext-kernel-preview">
    <header class="kdp__bar">
      <button class="kdp__btn" data-testid="kdp-back" @click="emit('back')">← 返回备课会话</button>
      <div class="kdp__title">
        <strong>{{ title || copyFor('prep.artifact.type.teaching_deck_spec') }}</strong>
        <span class="kdp__tag" :class="`kdp__tag--${status}`" data-testid="kdp-status">{{ statusLabel }}</span>
      </div>
      <div class="kdp__spacer" />
      <span v-if="progress && status === 'generating'" class="kdp__progress">{{ progress }}</span>
      <button
        v-if="pages.length"
        class="kdp__btn kdp__btn--primary"
        data-testid="kdp-download-pptx"
        :disabled="downloading"
        @click="downloadPptx"
      >
        {{ downloading ? '准备下载…' : '下载可编辑 PPTX' }}
      </button>
      <a
        v-if="lastDownloadUrl"
        class="kdp__btn kdp__btn--ok"
        :href="lastDownloadUrl"
        :download="lastDownloadName || undefined"
        data-testid="kdp-download-link"
      >保存文件</a>
    </header>

    <p v-if="status === 'failed'" class="kdp__alert" role="alert" data-testid="kdp-error">
      <span>{{ error }}</span>
      <button class="kdp__btn" data-testid="kdp-retry" @click="retry">重试</button>
    </p>

    <div v-if="!pages.length" class="kdp__empty" data-testid="kdp-empty">
      <div class="kdp__spin" aria-hidden="true" />
      <p>{{ status === 'failed' ? '尚未取得内核渲染结果。' : '正在调用 PPT 内核生成课件版面…' }}</p>
      <p class="kdp__hint">
        内核逐页渲染走 PowerPoint 引擎，约 10–60 秒一页；生成期间可以离开，稍后回来仍是这份课件。
      </p>
    </div>

    <div v-else class="kdp__body">
      <aside class="kdp__nav" data-testid="kdp-nav">
        <button
          v-for="(p, i) in pages"
          :key="p.pageNo"
          class="kdp__thumb"
          :class="{ 'is-active': i === activeIdx }"
          :data-testid="`kdp-thumb-${p.pageNo}`"
          @click="activeIdx = i"
        >
          <img :src="p.url" :alt="`第 ${p.pageNo} 页`" loading="lazy">
          <span class="kdp__thumb-no">{{ i + 1 }}</span>
        </button>
      </aside>

      <div class="kdp__stage">
        <img
          v-if="activePage"
          class="kdp__page"
          :src="activePage.url"
          :alt="`第 ${activePage.pageNo} 页`"
          :data-testid="`kdp-page-${activePage.pageNo}`"
        >
        <div class="kdp__meta">
          <span>第 {{ activeIdx + 1 }} / {{ pages.length }} 页</span>
          <span class="kdp__hint">版面由 PPT 内核渲染 · 内容改动请回到备课会话提出</span>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * 只读 + 一动作（下载）的展示组件：生成/取图全在 `useKernelDeckPreview` 里，
 * 组件自身不发明任何业务状态。
 */
import { computed, onBeforeUnmount, ref, toRef, watch } from 'vue'

import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import { createDownloadTicket, downloadUrlOf } from '@/features/teacher-prep-v2/api/prepApi'
import { pptStudioApi } from '@/features/ppt-studio-v2/api/pptStudioApi'
import { useKernelDeckPreview } from './useKernelDeckPreview'

const props = withDefaults(defineProps<{
  deckId: string | null
  revision?: number | null
  spec?: Record<string, unknown> | null
  title?: string
}>(), { revision: null, spec: null, title: '' })

const emit = defineEmits<{ (e: 'back'): void }>()

const preview = useKernelDeckPreview({
  deckId: toRef(props, 'deckId'),
  revision: toRef(props, 'revision'),
  spec: toRef(props, 'spec'),
})

const { status, error, pages, pptxArtifactId, progress } = preview

const activeIdx = ref(0)
const downloading = ref(false)
const lastDownloadUrl = ref<string | null>(null)
const lastDownloadName = ref<string | null>(null)

watch(pages, () => { activeIdx.value = 0 })

const activePage = computed(() => pages.value[activeIdx.value] ?? null)

const statusLabel = computed(() => {
  if (status.value === 'ready') return '内核已渲染'
  if (status.value === 'generating') return '正在生成版面'
  if (status.value === 'failed') return '生成失败'
  return '正在提交内核'
})

function retry() {
  void preview.refresh()
}

/** 下载真 PPTX：先拿 8200 字节，再经 8100 票据换取受控下载链接（浏览器不拼存储路径）。 */
async function downloadPptx() {
  const artifactId = pptxArtifactId.value
  if (!artifactId || downloading.value) return
  downloading.value = true
  try {
    const ticket = await createDownloadTicket(artifactId)
    const url = downloadUrlOf(ticket as { url?: string | null; ticket?: string | null })
    if (url) {
      lastDownloadUrl.value = url
      const name = (ticket as { file_name?: string | null }).file_name
      lastDownloadName.value = typeof name === 'string' && name ? name : null
      // 直接触发一次下载（票据短时有效，同时保留链接供二次保存）。
      const a = document.createElement('a')
      a.href = url
      if (lastDownloadName.value) a.download = lastDownloadName.value
      document.body.appendChild(a)
      a.click()
      a.remove()
      return
    }
    // 无票据能力时退回字节直取（仍是内核产物，不退化成空壳）。
    const blob = await pptStudioApi.fetchDeckPptx(artifactId)
    const objUrl = URL.createObjectURL(blob)
    lastDownloadUrl.value = objUrl
    const a = document.createElement('a')
    a.href = objUrl
    a.download = `${props.title || '课件'}.pptx`
    document.body.appendChild(a)
    a.click()
    a.remove()
  } catch (e) {
    error.value = `下载课件失败：${e instanceof Error ? e.message : String(e)}`
  } finally {
    downloading.value = false
  }
}

watch(() => [props.deckId, props.revision, props.spec] as const, () => { void preview.refresh() }, { immediate: true })

onBeforeUnmount(() => {
  for (const p of pages.value) URL.revokeObjectURL(p.url)
  if (lastDownloadUrl.value?.startsWith('blob:')) URL.revokeObjectURL(lastDownloadUrl.value)
})
</script>

<style scoped>
.kdp {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--tv3-bg, #f6f8fb);
  color: var(--tv3-ink, #16233b);
}
.kdp__bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--tv3-line, #e2e8f0);
  background: var(--tv3-card, #fff);
}
.kdp__title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.kdp__title strong { font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kdp__spacer { flex: 1; }
.kdp__tag {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--tv3-line, #e2e8f0);
  color: var(--tv3-ink3, #64748b);
}
.kdp__tag--ready { border-color: #a5f3fc; background: #cffafe; color: #0e7490; }
.kdp__tag--generating { border-color: var(--tv3-gold-border, #a5f3fc); color: var(--tv3-gold-deep, #0e7490); }
.kdp__tag--failed { border-color: #fecaca; background: #fef2f2; color: #b91c1c; }
.kdp__progress { font-size: 12px; color: var(--tv3-ink3, #64748b); }
.kdp__btn {
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--tv3-line, #e2e8f0);
  border-radius: 8px;
  background: var(--tv3-card, #fff);
  color: var(--tv3-ink2, #334155);
  font-size: 12.5px;
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
}
.kdp__btn--primary { border-color: var(--tv3-gold, #0891b2); color: var(--tv3-gold-deep, #0e7490); }
.kdp__btn--ok { border-color: #86efac; color: #15803d; }
.kdp__btn:disabled { opacity: .55; cursor: not-allowed; }
.kdp__alert {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px 16px 0;
  padding: 10px 12px;
  border: 1px solid #fecaca;
  border-radius: 10px;
  background: #fef2f2;
  color: #b91c1c;
  font-size: 12.5px;
  line-height: 1.6;
}
.kdp__alert span { flex: 1; }
.kdp__empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--tv3-ink2, #334155);
  font-size: 13px;
}
.kdp__hint { color: var(--tv3-ink3, #64748b); font-size: 11.5px; }
.kdp__spin {
  width: 26px; height: 26px;
  border-radius: 50%;
  border: 3px solid var(--tv3-line, #e2e8f0);
  border-top-color: var(--tv3-gold, #0891b2);
  animation: kdp-spin .9s linear infinite;
}
@keyframes kdp-spin { to { transform: rotate(360deg); } }
.kdp__body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 168px 1fr;
  gap: 12px;
  padding: 12px 16px 16px;
}
.kdp__nav {
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 4px;
}
.kdp__thumb {
  position: relative;
  padding: 0;
  border: 2px solid transparent;
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(15, 23, 42, .08);
}
.kdp__thumb.is-active { border-color: var(--tv3-gold, #0891b2); }
.kdp__thumb img { display: block; width: 100%; height: auto; }
.kdp__thumb-no {
  position: absolute; left: 4px; bottom: 4px;
  font-size: 10px; padding: 0 5px; border-radius: 999px;
  background: rgba(15, 23, 42, .62); color: #fff;
}
.kdp__stage {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.kdp__page {
  max-width: 100%;
  max-height: calc(100% - 26px);
  border-radius: 10px;
  border: 1px solid var(--tv3-line, #e2e8f0);
  background: #fff;
  box-shadow: 0 6px 24px rgba(15, 23, 42, .12);
}
.kdp__meta {
  display: flex; gap: 12px; align-items: baseline;
  font-size: 11.5px; color: var(--tv3-ink3, #64748b);
}
</style>
