/**
 * 文件上传全链路状态机（契约以 domains/files/router.py 为准）：
 * 预检 mime/≤20MB → sha256（Web Crypto）→ uploadInit →（deduplicated 秒传）
 * → 预签名 PUT 直传（>5MB 走分片任务，整体作为 part 1 上传后 complete）
 * → parse(purpose) → detail 轮询（2s 起步指数退避，间隔上限 30s）
 * → parsed 就绪 / failed 可重试。上传中允许继续打字。
 */
import { reactive } from 'vue'
import { filesApi } from '@/api'
import { getCachedUser, authHeaders } from '@/api/client'
import { sha256Hex } from '@/utils/sha256'

export const ALLOWED_MIMES = new Set([
  'application/pdf',
  'image/jpeg',
  'image/png',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'text/markdown',
  'text/plain',
])

export const ACCEPT_EXTS = '.pdf,.jpg,.jpeg,.png,.docx,.pptx,.xlsx,.md,.txt'

const MAX_SIZE = 20 * 1024 * 1024 // 20MB
const MULTIPART_THRESHOLD = 5 * 1024 * 1024 // 5MB
const POLL_START_MS = 2000
const POLL_MAX_MS = 30000
const MAX_ATTACHMENTS = 3

let localSeq = 0

/** 浏览器 mime 不可靠时按扩展名兜底（.md/.txt 常拿到空串） */
function resolveMime(file) {
  if (ALLOWED_MIMES.has(file.type)) return file.type
  const name = (file.name || '').toLowerCase()
  if (name.endsWith('.md')) return 'text/markdown'
  if (name.endsWith('.txt')) return 'text/plain'
  return file.type || ''
}

// 再导出：HTTP 部署（http://IP 云端）无 crypto.subtle，sha256Hex 内部回退纯 JS 实现；
// 绝不能用随机哈希兜底——后端校验 PUT 字节哈希，假哈希会被静默拒收导致"解析无输出"。
export { sha256Hex }

/** 预签名 URL 直传（XHR 以拿到上传进度与 ETag；不带业务鉴权头） */
export function putPresigned(task, url) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    try { xhr.setRequestHeader('Content-Type', task.mime) } catch { /* 个别浏览器限制，忽略 */ }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) task.progress = Math.round((e.loaded / e.total) * 100)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        task.etag = (xhr.getResponseHeader('ETag') || '').replace(/"/g, '') || '1'
        resolve()
      } else {
        reject(new Error(`上传失败 (HTTP ${xhr.status})`))
      }
    }
    xhr.onerror = () => reject(new Error('网络错误，上传中断'))
    xhr.send(task.file)
  })
}

/** 拍照压缩：最长边 ≤1600px 且 ≤4MB（JPEG q0.8 起逐级降质），失败回退原图 */
export async function compressImage(file) {
  try {
    const bmp = await createImageBitmap(file)
    const scale = Math.min(1, 1600 / Math.max(bmp.width, bmp.height))
    if (scale >= 1 && file.size <= 4 * 1024 * 1024) return file
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(bmp.width * scale))
    canvas.height = Math.max(1, Math.round(bmp.height * scale))
    canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height)
    let blob = null
    for (let q = 0.8; q >= 0.4; q -= 0.2) {
      blob = await new Promise((r) => canvas.toBlob(r, 'image/jpeg', q))
      if (blob && blob.size <= 4 * 1024 * 1024) break
    }
    if (!blob) return file
    const name = (file.name || 'photo').replace(/\.\w+$/, '') + '.jpg'
    return new File([blob], name, { type: 'image/jpeg' })
  } catch {
    return file
  }
}

export function useFileUpload(toast) {
  const tasks = reactive([])
  const state = reactive({ rateLimitedUntil: 0 })
  const pollers = new Map() // localId -> timer

  function friendly(e) {
    return (e && e.message) || '操作失败，请稍后重试'
  }

  function stopPoller(localId) {
    const t = pollers.get(localId)
    if (t) clearTimeout(t)
    pollers.delete(localId)
  }

  function stopAll() {
    pollers.forEach((t) => clearTimeout(t))
    pollers.clear()
  }

  async function addFiles(fileList, { purpose = 'chat_attachment' } = {}) {
    for (const raw of [...fileList]) {
      const file = purpose === 'question_photo' ? await compressImage(raw) : raw
      if (tasks.length >= MAX_ATTACHMENTS) {
        toast.error('单条消息最多 3 个附件')
        break
      }
      const mime = resolveMime(file)
      if (!ALLOWED_MIMES.has(mime)) {
        toast.error(`不支持的文件类型：${file.name}`)
        continue
      }
      if (file.size > MAX_SIZE) {
        toast.error(`文件超过 20MB 限制：${file.name}`)
        continue
      }
      const task = reactive({
        localId: ++localSeq,
        file,
        fileId: '',
        filename: file.name,
        mime,
        size: file.size,
        kind: mime.startsWith('image/') ? 'image' : 'doc',
        purpose,
        status: 'hashing', // hashing|uploading|uploaded|parsing|parsed|failed
        progress: 0,
        error: '',
        engine: '',
        etag: '',
        pollRetried: false,
      })
      tasks.push(task)
      runPipeline(task).catch((e) => {
        if (e?.code === 42901) {
          state.rateLimitedUntil = Date.now() + 60000
          toast.error('操作太频繁，60 秒后再试')
        }
        task.status = 'failed'
        task.error = e?.code === 42901 ? '上传频率超限' : friendly(e)
      })
    }
  }

  async function runPipeline(task) {
    task.status = 'hashing'
    const sha256 = await sha256Hex(task.file)
    const init = await filesApi.uploadInit({
      filename: task.filename,
      mime: task.mime,
      size_bytes: task.size,
      sha256,
      multipart: task.size > MULTIPART_THRESHOLD,
    })
    task.fileId = init.file_id
    if (!init.deduplicated) {
      task.status = 'uploading'
      // 说明：后端 uploadInit 只返回 part 1 的预签名 URL（签名含 PartNumber=1），
      // 无法为后续分片取得合法 URL，故大文件整体作为单个 part 1 PUT，再 complete 合并。
      await putPresigned(task, init.upload_url)
      if (init.upload_id) {
        await filesApi.complete(task.fileId, {
          upload_id: init.upload_id,
          parts: [{ part_no: 1, etag: task.etag }],
        })
      }
    }
    task.status = 'uploaded'
    await startParse(task)
  }

  async function startParse(task) {
    task.status = 'parsing'
    try {
      await filesApi.parse(task.fileId, { purpose: task.purpose })
    } catch (e) {
      if (e?.code !== 40901) throw e // 40901=解析中，属正常，直接转轮询
    }
    schedulePoll(task, POLL_START_MS)
  }

  /** 上传即入库（S2/S4）：parse 产物推送 student-api 切片向量化；静默进行，失败不阻断对话 */
  function scheduleKnowledgeIngest(task, detail) {
    try {
      const asset = (detail.assets || []).find((a) => a.asset_type === 'markdown')
      const content = (asset && asset.content) || ''
      if (content.length < 80) return // 过短内容不值得入库；后端质量闸做最终判定
      const user = getCachedUser()
      if (!user || !user.id) return
      fetch('/api/v1/kb/ingest-text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          user_id: user.id,
          title: task.name || '未命名资料',
          content,
          source_file_id: task.fileId,
          purpose: 'textbook',
        }),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => {
          task.ingestStatus = j ? j.status : 'error'
          if (j && j.status === 'rejected') console.warn('[ingest] 已拒绝入库：', j.reason)
        })
        .catch(() => { /* 静默：入库失败不影响对话 */ })
    } catch (e) {
      /* 静默 */
    }
  }

  function schedulePoll(task, delay) {
    stopPoller(task.localId)
    const timer = setTimeout(async () => {
      pollers.delete(task.localId)
      let d
      try {
        d = await filesApi.detail(task.fileId)
      } catch (e) {
        // 网络层错误允许重试 1 次，再失败转 failed
        if (!task.pollRetried) {
          task.pollRetried = true
          schedulePoll(task, delay)
          return
        }
        task.status = 'failed'
        task.error = friendly(e)
        return
      }
      if (d.status === 'parsed') {
        task.status = 'parsed'
        task.engine = d.parse_engine || ''
        // P0（用户实测）：把解析文本带回给对话发送链——图片题的识别内容必须到模型手里
        try {
          const assets = d.assets || []
          task.ocrText = assets
            .filter((a) => a.asset_type === 'markdown' || a.asset_type === 'text')
            .sort((x, y) => (x.page_no || 0) - (y.page_no || 0))
            .map((a) => a.content || '')
            .join('\n')
            .trim()
        } catch { task.ocrText = '' }
        scheduleKnowledgeIngest(task, d) // 上传即入库（S2/S4 链路）：静默进行，不阻断发送
      } else if (d.status === 'failed') {
        task.status = 'failed'
        task.error = d.error || '解析失败'
      } else {
        schedulePoll(task, Math.min(POLL_MAX_MS, Math.round(delay * 1.6)))
      }
    }, delay)
    pollers.set(task.localId, timer)
  }

  async function retry(task) {
    if (task.status !== 'failed') return
    task.error = ''
    task.pollRetried = false
    try {
      if (task.fileId) await startParse(task) // 已有 file_id：重走 parse
      else await runPipeline(task) // 上传阶段失败：完整重传
    } catch (e) {
      task.status = 'failed'
      task.error = friendly(e)
    }
  }

  function removeTask(task) {
    stopPoller(task.localId)
    const i = tasks.indexOf(task)
    if (i >= 0) tasks.splice(i, 1)
  }

  /** 发送时组装 attachments（只允许 status=parsed，≤3 个） */
  function parsedAttachments() {
    return tasks
      .filter((t) => t.status === 'parsed' && t.fileId)
      .slice(0, MAX_ATTACHMENTS)
      .map((t) => ({ file_id: t.fileId, kind: t.kind, filename: t.filename }))
  }

  /** 发送成功后清理已随消息发出的附件任务 */
  function clearSent() {
    for (let i = tasks.length - 1; i >= 0; i--) {
      if (tasks[i].status === 'parsed') tasks.splice(i, 1)
    }
  }

  return { tasks, state, addFiles, retry, removeTask, stopAll, parsedAttachments, clearSent }
}
