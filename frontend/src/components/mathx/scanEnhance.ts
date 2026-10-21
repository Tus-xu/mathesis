/**
 * mathx/scanEnhance —— 照片扫描增强纯逻辑（P2，可单测，不依赖 DOM）
 * 对标「扫描王」：提亮 + 高对比 + 背景去阴影归一，把劣质手写纸拍照变清晰。
 * 只做像素级数学变换；真正渲染走组件 PhotoInsertPanel 里的 <canvas>。
 */

export interface ScanParams {
  /** 亮度平移 -40..40，0 为不调 */
  brightness: number
  /** 对比度 -40..100，0 为中性；正值增强明暗差异 */
  contrast: number
  /** 去阴影 / 提白纸背：把浅色背景向 255 推，暗部墨迹基本不动 */
  whiten: boolean
  /** 去阴影强度 0..1 */
  strength: number
}

export const SCAN_DEFAULTS: ScanParams = { brightness: 6, contrast: 30, whiten: true, strength: 0.55 }

const clamp = (x: number, lo: number, hi: number) => (x < lo ? lo : x > hi ? hi : x)

/** 单个亮度通道的增强：对比 → 亮度 → 背景提白（顺序固定，可单测） */
export function enhanceLuma(v: number, p: ScanParams): number {
  const f = 1 + p.contrast / 100
  let c = (v - 128) * f + 128 + p.brightness
  if (p.whiten && p.strength > 0) {
    // 「纸面」参考灰：高于此视为背景阴影/纸灰，乘以 (1+strength) 向白拉；墨迹(c<paper)保留
    const paper = 148
    if (c >= paper) c = paper + (c - paper) * (1 + p.strength)
  }
  return clamp(c, 0, 255)
}

const lumOf = (r: number, g: number, b: number) => 0.299 * r + 0.587 * g + 0.114 * b

/**
 * 增强 RGBA 像素。输出按灰阶映射（扫描件风格）：r=g=b=增强后亮度。
 * 返回新数组，不改动输入。alpha 原样保留。
 */
export function enhancePixels(rgba: Uint8ClampedArray, p: ScanParams): Uint8ClampedArray {
  const out = new Uint8ClampedArray(rgba.length)
  const blackout = !p.whiten && p.contrast === 0 && p.brightness === 0
  for (let i = 0; i < rgba.length; i += 4) {
    const v = blackout ? rgba[i] : enhanceLuma(lumOf(rgba[i], rgba[i + 1], rgba[i + 2]), p)
    out[i] = v; out[i + 1] = v; out[i + 2] = v; out[i + 3] = rgba[i + 3]
  }
  return out
}

/* ---------- 浏览器端：Image → 增强绘制的 dataURL ---------- */

/** 把一个图片源（dataURL / url）经增强后绘制回 dataURL（<canvas> 实现） */
export function enhanceSrcToDataUrl(start: string | HTMLImageElement, p: ScanParams, maxSide = 1600): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = start instanceof HTMLImageElement ? start : new Image()
    const done = (image: HTMLImageElement) => {
      try {
        const s = Math.min(1, maxSide / Math.max(image.naturalWidth || image.width, image.naturalHeight || image.height))
        const w = Math.max(1, Math.round((image.naturalWidth || image.width) * s))
        const h = Math.max(1, Math.round((image.naturalHeight || image.height) * s))
        const cv = document.createElement('canvas')
        cv.width = w; cv.height = h
        const ctx = cv.getContext('2d')
        if (!ctx) { reject(new Error('canvas 不可用')); return }
        ctx.drawImage(image, 0, 0, w, h)
        const src = ctx.getImageData(0, 0, w, h)
        const out = enhancePixels(src.data, p)
        ctx.putImageData(new ImageData(out, w, h), 0, 0)
        resolve(cv.toDataURL('image/png'))
      } catch (e) { reject(e) }
    }
    if (img.complete || img.naturalWidth > 0) done(img)
    else {
      img.onload = () => done(img)
      img.onerror = () => reject(new Error('图片加载失败'))
      img.src = typeof start === 'string' ? start : start.src
    }
  })
}