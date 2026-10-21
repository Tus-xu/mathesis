/**
 * 解答题手写解答拍照上传（迭代10 v1.4，对齐愿景 03：拍照 → OCR 识别 → 文本可编辑 → 提交判分）
 *
 * 上传契约与 useFileUpload 一致：sha256 → uploadInit → 预签名 PUT → parse(question_photo)
 * → detail 轮询（2s 起步指数退避，90s 超时）。识别文本回填答案文本框（学生可再编辑），
 * file_id 随提交携带（后端 student_router OCR 兜底回填双保险，拍照作答不再误判 0 分）。
 *
 * 用法（QuizView / ExamPaperView 解答题区）：
 *   const sp = useSolutionPhoto(toast)
 *   <button @click="sp.pick(item.item_no)">📷 拍照上传解答</button>
 *   <input :ref="sp.setInput(item.item_no)" type="file" accept="image/*" hidden
 *          @change="sp.onPicked(item.item_no, $event, { onText: (t) => appendAnswer(item, t) })" />
 *   提交时：...(sp.fileIds[it.item_no] ? { file_id: sp.fileIds[it.item_no] } : {})
 */
import { reactive } from 'vue'
import { filesApi } from '@/api'
import { compressImage, putPresigned, sha256Hex } from '@/components/chat/useFileUpload'

const POLL_START_MS = 2000
const POLL_MAX_MS = 8000
const TIMEOUT_MS = 90000

export function useSolutionPhoto(toast) {
  const busy = reactive({}) // item_no -> bool
  const busyText = reactive({}) // item_no -> 阶段文案（上传中…/识别中…）
  const fileIds = reactive({}) // item_no -> file_id（提交时携带）
  const inputs = new Map() // item_no -> hidden file input el

  const setInput = (no) => (el) => {
    if (el) inputs.set(no, el)
  }

  function pick(no) {
    if (busy[no]) return
    inputs.get(no)?.click()
  }

  /** 选中照片后全链路：压缩 → 上传 → 解析 → 轮询 → onText(识别文本) 回填 */
  async function onPicked(no, event, { onText } = {}) {
    const raw = event.target.files?.[0]
    event.target.value = '' // 允许重选同一文件
    if (!raw) return
    busy[no] = true
    busyText[no] = '上传中…'
    try {
      const file = await compressImage(raw)
      const mime = file.type || 'image/jpeg'
      const sha256 = await sha256Hex(file)
      const init = await filesApi.uploadInit({
        filename: file.name || 'solution.jpg',
        mime,
        size_bytes: file.size,
        sha256,
        multipart: false,
      })
      const fileId = init.file_id
      if (!init.deduplicated) {
        await putPresigned({ file, mime, progress: 0 }, init.upload_url)
      }
      fileIds[no] = fileId
      try {
        await filesApi.parse(fileId, { purpose: 'question_photo' })
      } catch (e) {
        if (e?.code !== 40901) throw e // 40901=解析中，属正常，直接转轮询
      }
      busyText[no] = '识别中…'
      const started = Date.now()
      let delay = POLL_START_MS
      for (;;) {
        await new Promise((r) => setTimeout(r, delay))
        const d = await filesApi.detail(fileId)
        if (d.status === 'parsed') {
          const text = (d.assets || [])
            .filter((a) => a.asset_type === 'markdown' || a.asset_type === 'text')
            .sort((a, b) => (a.page_no || 0) - (b.page_no || 0))
            .map((a) => a.content || '')
            .join('\n')
            .trim()
          if (text) onText?.(text)
          toast.success(text ? '手写解答已识别，可在文本框中修改后提交' : '照片已附上，将随提交批改')
          return
        }
        if (d.status === 'failed') throw new Error(d.error || '照片识别失败')
        if (Date.now() - started > TIMEOUT_MS) throw new Error('识别超时，请重试或直接输入')
        delay = Math.min(POLL_MAX_MS, Math.round(delay * 1.5))
      }
    } catch (e) {
      delete fileIds[no]
      toast.error(e?.message || '照片上传失败，请重试或直接输入')
    } finally {
      busy[no] = false
      busyText[no] = ''
    }
  }

  return { busy, busyText, fileIds, setInput, pick, onPicked }
}
