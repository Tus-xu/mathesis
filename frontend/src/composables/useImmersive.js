/**
 * 沉浸模式共享状态（模块级 ref，跨组件共享开关）
 */
import { ref } from 'vue'

export const immersiveOpen = ref(false)

export function openImmersive() {
  immersiveOpen.value = true
}

export function closeImmersive() {
  immersiveOpen.value = false
}
