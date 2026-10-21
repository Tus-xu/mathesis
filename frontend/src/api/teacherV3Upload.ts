/**
 * 预签名直传助手（A0 M2-C，02-ARCHITECTURE §5 / IFC-004）：
 *   1) POST /teacher-v3/upload/presign 拿 {key, url?}
 *   2) url 存在 → XHR PUT 裸文件体直传 MinIO（原图原样，红线 3：前端零有损压缩），
 *      XHR.upload.onprogress 提供进度（fetch 无上传进度，见 notes/A0-notes.md 主题二）
 *   3) 失败自动重新 presign 再传一次（URL 短时效，不盲试过期 URL）
 *   4) url 为空 = mock/演示模式：只发 key（不发字节），demo=true
 * 业务端点随后只收 key（DB 只存 key 不存 URL，02-ARCHITECTURE §5 规则）。
 */
import { teacherRequest } from './teacher/client'

export interface PresignResult {
  key: string
  url?: string | null
  headers?: Record<string, string>
  demo?: boolean
}

export interface UploadHandle {
  key: string
  demo: boolean
}

export interface PresignUploadOptions {
  /** 0-1；用于最小上传进度条（不改布局） */
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
}

function putWithProgress(url: string, file: File, headers: Record<string, string> | undefined, onProgress?: (f: number) => void, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url, true)
    for (const [k, v] of Object.entries(headers || {})) xhr.setRequestHeader(k, v)
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(e.loaded / e.total) }
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`上传失败 (HTTP ${xhr.status})`)))
    xhr.onerror = () => reject(new Error('上传失败：网络中断'))
    xhr.onabort = () => reject(new Error('上传已取消'))
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    // 裸文件体（非 FormData）：presign PUT 路线（API.md presignedPutObject）
    xhr.send(file)
  })
}

/** 拍照/扫图直传：原图 File 原样上传，返回对象 key 交给业务端点 */
export async function presignUpload(file: File, opts: PresignUploadOptions = {}): Promise<UploadHandle> {
  const first = await teacherRequest<PresignResult>('POST', '/teacher-v3/upload/presign', {
    body: { filename: file.name, content_type: file.type || 'application/octet-stream', size: file.size },
    signal: opts.signal,
  })
  let { key, url, headers, demo } = first.data
  if (url) {
    try {
      await putWithProgress(url, file, headers, opts.onProgress, opts.signal)
    } catch {
      // URL 过期/单次网络失败：重新 presign 再传一次（不盲试同一 URL）
      const retry = await teacherRequest<PresignResult>('POST', '/teacher-v3/upload/presign', {
        body: { filename: file.name, content_type: file.type || 'application/octet-stream', size: file.size, retry_of: key },
        signal: opts.signal,
      })
      key = retry.data.key
      url = retry.data.url
      headers = retry.data.headers
      demo = retry.data.demo
      if (!url) return { key, demo: !!demo }
      opts.onProgress?.(0)
      await putWithProgress(url, file, headers, opts.onProgress, opts.signal)
    }
  }
  return { key, demo: !!demo }
}

/**
 * 服务端中转上传（2026-10-05 修复「拍照入库/自编题图不可用」）：
 * 浏览器 → 8100 → MinIO（`POST /teacher-v3/upload/file`，multipart）。
 *
 * 为什么替换 presign 直传（presignUpload 保留给旧调用方）：
 *   1. 线上 `teacher-minio` 只在 compose 内网，**浏览器根本 PUT 不到**；
 *   2. 本地直传会被 MinIO CORS 拦（跨源 XHR PUT）；
 *   3. 直传失败后调用方曾回落把整个 base64 dataURL 塞进 `stem_image`，
 *      库里留下一批裂图垃圾行（用户看到的「题库图片显示不出来」正是这批）。
 * key 语义不变（DB 仍只存 key 不存 URL），返回的 `url` 是 presigned GET，
 * `<img :src>` 可直接渲染。
 */
export async function uploadFile(file: File, opts: PresignUploadOptions = {}): Promise<UploadHandle> {
  const form = new FormData()
  form.append('file', file)
  const r = await teacherRequest<{ key: string; bucket: string; url: string }>(
    'POST',
    '/teacher-v3/upload/file',
    { body: form as unknown as Record<string, unknown>, signal: opts.signal },
  )
  opts.onProgress?.(1)
  return { key: r.data.key, demo: false }
}
