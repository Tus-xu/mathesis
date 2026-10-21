/**
 * useEcharts —— ECharts 按需注册 + 组件生命周期封装（全仓第一个 echarts 消费者）
 *
 * 设计要点：
 * - echarts/core 按需注册（Line/Bar + Grid/Tooltip/MarkLine/Legend + Canvas 渲染器），
 *   不用 `import * as echarts from 'echarts'` 全量包，控制 bundle 体积；
 * - 主题色从 tokens.css 的 CSS 变量读取（--brand/--ok/--warn/--err…），取不到给同值兜底，
 *   图表配色与学生端视觉 token 保持一致；
 * - 容器无尺寸（jsdom 单测 / v-show 隐藏 / 卸载中）时跳过 init，只暂存 option——
 *   调用方在容器变为可见后再次 render() 即可补初始化，绝不让图表崩掉整卡；
 * - 每个实例自带 window resize 监听与卸载清理（防泄漏，对齐 FloatingButler B0 纪律）。
 */
import { onBeforeUnmount, onMounted } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

let registered = false

function ensureRegistered() {
  if (registered) return
  echarts.use([
    LineChart,
    BarChart,
    GridComponent,
    TooltipComponent,
    MarkLineComponent,
    LegendComponent,
    CanvasRenderer,
  ])
  registered = true
}

/** 读取主题 CSS 变量（styles/tokens.css），失败/缺失回退同值兜底 */
export function themeColor(name, fallback) {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
    return v || fallback
  } catch {
    return fallback
  }
}

/** 学生端图表配色（对齐 styles/tokens.css:7-14 的品牌/语义色） */
export function chartPalette() {
  return {
    brand: themeColor('--brand', '#4f46e5'),
    brand2: themeColor('--brand2', '#6366f1'),
    ok: themeColor('--ok', '#059669'),
    warn: themeColor('--warn', '#d97706'),
    err: themeColor('--err', '#dc2626'),
    ink: themeColor('--ink', '#0f172a'),
    ink2: themeColor('--ink2', '#334155'),
    ink3: themeColor('--ink3', '#94a3b8'),
    line: themeColor('--line', '#e2e8f0'),
    card: themeColor('--card', '#ffffff'),
  }
}

/**
 * 图表实例绑定到容器 ref。
 * 返回 { render, resize, dispose }：render(option) 幂等覆盖式更新；
 * 容器暂无尺寸时只暂存，onMounted / 下一次 render 会重试。
 */
export function useEcharts(containerRef) {
  ensureRegistered()
  let chart = null
  let pending = null

  function ensureInit() {
    const el = containerRef?.value
    if (chart || !el) return chart
    if (!el.clientWidth || !el.clientHeight) return null // 无尺寸：不 init（jsdom/隐藏态）
    try {
      chart = echarts.init(el)
    } catch {
      chart = null // init 失败不影响整卡渲染
    }
    return chart
  }

  function render(option) {
    pending = option || null
    const c = ensureInit()
    if (c && pending) c.setOption(pending, true)
  }

  function resize() {
    try {
      chart?.resize()
    } catch { /* 已 dispose 等场景静默 */ }
  }

  function dispose() {
    try {
      chart?.dispose()
    } catch { /* 忽略重复 dispose */ }
    chart = null
    pending = null
  }

  function onWinResize() {
    resize()
  }

  onMounted(() => {
    if (pending) render(pending) // 挂载后容器尺寸就绪，补初始化
    window.addEventListener('resize', onWinResize)
  })
  onBeforeUnmount(() => {
    window.removeEventListener('resize', onWinResize)
    dispose()
  })

  return { render, resize, dispose }
}
