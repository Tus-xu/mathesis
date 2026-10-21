// P2 扫描增强纯逻辑（对标扫描王：提亮 / 高对比 / 背景去阴影归一）
import { describe, it, expect } from 'vitest'
import { enhanceLuma, enhancePixels } from '@/components/mathx/scanEnhance'

const NEUTRAL = { brightness: 0, contrast: 0, whiten: false, strength: 0 }

describe('enhanceLuma', () => {
  it('中性参数下恒等（扫描增强不破坏无噪图）', () => {
    expect(enhanceLuma(0, NEUTRAL)).toBe(0)
    expect(enhanceLuma(128, NEUTRAL)).toBe(128)
    expect(enhanceLuma(255, NEUTRAL)).toBe(255)
  })

  it('对比度增强：亮部更亮、暗部更暗', () => {
    const p = { ...NEUTRAL, contrast: 40 }
    expect(enhanceLuma(200, p)).toBeGreaterThan(200)
    expect(enhanceLuma(80, p)).toBeLessThan(80)
  })

  it('亮度平移：整体提亮', () => {
    const p = { ...NEUTRAL, brightness: 15 }
    expect(enhanceLuma(120, p)).toBe(135)
  })

  it('去阴影：亮部（纸灰背景）向白拉，暗部墨迹保留不变', () => {
    const p = { ...NEUTRAL, whiten: true, strength: 0.6 }
    // 暗部（墨迹）不动
    expect(enhanceLuma(120, p)).toBe(120)
    // 亮部/灰背景向 255 靠拢
    expect(enhanceLuma(200, p)).toBeGreaterThan(230)
    // 全提亮随强度增大（上限受纸面基准钳制，>248）
    const strong = { ...p, strength: 1 }
    expect(enhanceLuma(200, strong)).toBeGreaterThan(248)
  })

  it('输出 clamp 到 [0,255]，不越界', () => {
    const p = { ...NEUTRAL, brightness: 200 }
    expect(enhanceLuma(255, p)).toBe(255)
    const pp = { ...NEUTRAL, brightness: -200 }
    expect(enhanceLuma(0, pp)).toBe(0)
  })
})

describe('enhancePixels', () => {
  it('输出为灰阶（r=g=b）且保留 alpha，不改动输入数组', () => {
    const src = new Uint8ClampedArray([10, 10, 10, 255, 55, 200, 8, 128])
    const out = enhancePixels(src, { ...NEUTRAL, contrast: 30 })
    expect(out[0]).toBe(out[1])
    expect(out[1]).toBe(out[2])
    expect(out[3]).toBe(255)
    expect(out[7]).toBe(128)
    // 输入未被修改
    expect(src[0]).toBe(10)
    expect(src[4]).toBe(55)
  })

  it('中性全参数下近似恒等（保持原像素值）', () => {
    const src = new Uint8ClampedArray([200, 0, 90, 255, 7, 8, 9, 255])
    const out = enhancePixels(src, { ...NEUTRAL, whiten: false, contrast: 0, brightness: 0 })
    expect(out[0]).toBe(200)
    out[1] === 200 // 灰阶：第一像素 r=g=b
    out[3] === 255 && out[7] === 255 // alpha 保留
    expect(out[4]).toBe(7)
  })

  it('扫描场景：灰背景被拉白、墨迹保留，形成清晰黑白稿', () => {
    const src = new Uint8ClampedArray([185, 185, 185, 255, 60, 60, 60, 255])
    const p = { brightness: 6, contrast: 30, whiten: true, strength: 0.55 }
    const out = enhancePixels(src, p)
    // 背景 185 → 更白（接近 255）
    expect(out[0]).toBeGreaterThan(200)
    // 前景墨迹 60 → 更黑
    expect(out[4]).toBeLessThan(60)
  })
})