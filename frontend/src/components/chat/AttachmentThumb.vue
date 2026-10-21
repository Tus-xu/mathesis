<template>
  <!--
    持久化附件展示（4.4）：
    - 图片：真实缩略图（优先 localUrl 本地快照，其次 GET /api/files/{id}/content 预签名 URL；
      端点 404/加载失败 → 降级图标 chip），点击 lightbox 放大
    - 文档：封面式卡片（图标+文件名），拿到 url 时点击新窗口预览
  -->
  <div class="att-thumb" :class="{ img: isImage && url, doc: !isImage || !url }">
    <template v-if="isImage && url">
      <img
        :src="url" :alt="attachment.name" class="att-img" loading="lazy"
        @click.stop="openLightbox(url)" @error="onImgError"
      />
    </template>
    <template v-else-if="isImage">
      <div class="att-missing" title="" @click.stop>
        <UiIcon name="image" :size="18" />
        <span>原图已丢失（存储缺失），请在新对话中重新上传/拍照该题目图片</span>
      </div>
    </template>
    <template v-else>
      <div class="att-doc" :class="{ clickable: docUrl }" :title="chipTitle" @click.stop="openDoc">
        <UiIcon :name="isImage ? 'image' : 'file'" :size="18" class="att-doc-icon" />
        <span class="att-doc-name">{{ attachment.name }}</span>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { filesApi } from '@/api'
import { openLightbox } from '@/utils/lightbox'
import UiIcon from '@/components/common/UiIcon.vue'

const props = defineProps({
  attachment: { type: Object, required: true }, // {file_id, kind, name, mime, size, localUrl?}
})

const isImage = computed(
  () => props.attachment.kind === 'image' || String(props.attachment.mime || '').startsWith('image/')
)
// localUrl：刚发送的消息带本地 objectURL，即时可见（不依赖新端点）
const url = ref(props.attachment.localUrl || '')
const docUrl = ref('')
const failed = ref(false)
// v1.4：图片加载失败降级 chip 的悬停说明（消除"图片突然不显示"的困惑）
const chipTitle = computed(() =>
  failed.value && isImage.value
    ? `${props.attachment.name}（图片已过期或存储缺失）`
    : props.attachment.name
)

onMounted(async () => {
  if (url.value || failed.value || !props.attachment.file_id) return
  try {
    const d = await filesApi.contentUrl(props.attachment.file_id)
    const u = d?.url || ''
    if (!u) throw new Error('empty url')
    if (isImage.value) url.value = u
    else docUrl.value = u
  } catch {
    failed.value = true // 旧后端 404：保持降级图标 chip，不打扰用户
  }
})

function onImgError() {
  // 预签名 URL 过期/失败：图片退回文档卡形态
  failed.value = true
  url.value = ''
}

function openDoc() {
  if (docUrl.value) window.open(docUrl.value, '_blank', 'noopener')
}
</script>

<style scoped>
.att-thumb { display: inline-block; }
.att-img {
  width: 100%; max-width: 460px; height: auto; object-fit: contain; display: block;
  border-radius: var(--radius-md); border: 1px solid rgba(255, 255, 255, 0.35);
  cursor: zoom-in; transition: transform var(--transition-fast);
  background: rgba(255, 255, 255, 0.15);
}
.att-missing {
  display: inline-flex; align-items: center; gap: var(--space-2);
  padding: 8px 12px; border-radius: var(--radius-md);
  background: rgba(160, 120, 40, 0.18); color: #8a6d1a;
  font-size: var(--text-xs); line-height: 1.5; max-width: 420px;
}
.att-img:hover { transform: scale(1.02); }
.att-doc {
  display: inline-flex; align-items: center; gap: var(--space-2);
  padding: 6px var(--space-3); border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.18); color: inherit;
  font-size: var(--text-xs); max-width: 220px;
}
.att-doc.clickable { cursor: pointer; }
.att-doc.clickable:hover { background: rgba(255, 255, 255, 0.3); }
.att-doc-icon { flex-shrink: 0; opacity: 0.9; }
.att-doc-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
