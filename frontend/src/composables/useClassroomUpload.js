/**
 * 课堂来源上传（拍题 OCR / 教案文档解析）——复用 files 平台既有链路：
 * compressImage → uploadInit → putPresigned → parse → detail 轮询 → 提取文本。
 *
 * 与 useSolutionPhoto（拍照作答）同一套契约；此处抽象成课堂来源（photo/file）通用入口。
 * 返回 { uploading, stageText, lastFileId, lastFileName, selectAndParse }
 *   selectAndParse(file) → { fileId, filename, text } | null
 */
import { ref } from 'vue'
import { filesApi } from '@/api'
import { compressImage, putPresigned, sha256Hex } from '@/components/chat/useFileUpload'

const POLL_START_MS = 2000
const POLL_MAX_MS = 8000
const TIMEOUT_MS = 120000

/** 将文件解析响应归一化为课堂可消费的文本与可审计识别质量。 */
export function normalizeClassroomParseResult(detail = {}) {
  const text = (detail.assets || [])
    .filter((asset) => asset.asset_type === 'markdown' || asset.asset_type === 'text')
    .sort((a, b) => (a.page_no || 0) - (b.page_no || 0))
    .map((asset) => asset.content || '')
    .join('\n')
    .trim()
  const quality = detail.parse_quality || {}
  return {
    text,
    parseQuality: {
      provider: quality.provider || '',
      confidence: Number.isFinite(Number(quality.confidence)) ? Number(quality.confidence) : 0,
      conditions: Array.isArray(quality.conditions) ? quality.conditions : [],
      diagramEntities: quality.diagram_entities && typeof quality.diagram_entities === 'object'
        ? quality.diagram_entities : {},
      uncertainties: Array.isArray(quality.uncertainties) ? quality.uncertainties : [],
      needs_confirmation: Boolean(quality.needs_confirmation),
    },
  }
}

/**
 * “确认题意”是学生对识别结果的审阅动作，而非将 OCR 结果改写为高置信度。
 * 保留原始条件、置信度与不确定项，供课堂生成和后续审计使用。
 */
export function confirmClassroomPhotoParseQuality(parseQuality = {}) {
  return {
    ...parseQuality,
    confirmed_by_user: true,
  }
}

export function useClassroomUpload(toast) {
  const uploading = ref(false)
  const stageText = ref('')
  const lastFileId = ref('')
  const lastFileName = ref('')

  /** 选择文件后全链路：压缩/直传 → 解析 → 轮询 → 提取 markdown/text 资产文本 */
  async function selectAndParse(file) {
    if (!file) return null
    uploading.value = true
    stageText.value = '上传中…'
    try {
      let payload
      let purpose
      if (file.type && file.type.startsWith('image/')) {
        const img = await compressImage(file)
        payload = { file: img, mime: img.type || file.type || 'image/jpeg' }
        purpose = 'question_photo'
      } else {
        payload = { file, mime: file.type || 'application/octet-stream' }
        purpose = 'chat_attach'
      }
      const sha256 = await sha256Hex(payload.file)
      const init = await filesApi.uploadInit({
        filename: file.name || 'upload.jpg',
        mime: payload.mime,
        size_bytes: payload.file.size,
        sha256,
        multipart: false,
      })
      const fileId = init.file_id
      if (!init.deduplicated) {
        await putPresigned({ file: payload.file, mime: payload.mime, progress: 0 }, init.upload_url)
      }
      stageText.value = '解析中…'
      try {
        await filesApi.parse(fileId, { purpose })
      } catch (e) {
        if (e?.code !== 40901) throw e // 40901=解析中，属正常
      }
      const started = Date.now()
      let delay = POLL_START_MS
      for (;;) {
        await new Promise((r) => setTimeout(r, delay))
        const d = await filesApi.detail(fileId)
        if (d.status === 'parsed') {
          const parsed = normalizeClassroomParseResult(d)
          const text = parsed.text
          uploading.value = false
          lastFileId.value = fileId
          lastFileName.value = file.name
          toast.success(text ? '识别/解析完成，请确认内容后生成课堂' : '文件已解析，但未提取到可读文本')
          return { fileId, filename: file.name, text, parseQuality: parsed.parseQuality }
        }
        if (d.status === 'failed') throw new Error(d.error || '解析失败')
        if (Date.now() - started > TIMEOUT_MS) throw new Error('解析超时，请重试')
        delay = Math.min(POLL_MAX_MS, Math.round(delay * 1.5))
      }
    } catch (e) {
      uploading.value = false
      toast.error(e?.message || '上传/解析失败，请重试')
      return null
    } finally {
      // uploading 在成功/失败分支已复位；此处在异常路径兜底
      setTimeout(() => { if (!uploading.value) stageText.value = '' }, 0)
    }
  }

  return { uploading, stageText, lastFileId, lastFileName, selectAndParse }
}
