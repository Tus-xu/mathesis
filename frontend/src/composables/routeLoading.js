import { ref } from 'vue'

/**
 * 路由级加载态（三端共用）。
 *
 * 背景：学生/教师/科研都使用路由懒加载；外壳先渲染、页面 chunk 后到，
 * 期间内容区没有任何反馈，用户看起来就是“白屏，需要刷新”。
 * 这里用 120ms 延迟避免正常秒开时闪烁；超时 15s 自动收起，避免异常时永久遮罩。
 */
export const routeLoading = ref(false)

let showTimer = null
let guardTimer = null

export function beginRouteLoading() {
  if (showTimer) clearTimeout(showTimer)
  if (guardTimer) clearTimeout(guardTimer)
  showTimer = setTimeout(() => { routeLoading.value = true }, 120)
  guardTimer = setTimeout(() => { endRouteLoading() }, 15000)
}

export function endRouteLoading() {
  if (showTimer) clearTimeout(showTimer)
  if (guardTimer) clearTimeout(guardTimer)
  showTimer = null
  guardTimer = null
  routeLoading.value = false
}
