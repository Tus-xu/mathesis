<template>
  <div class="dfv" :class="{ 'dfv--fullscreen': fullscreen }">
    <!-- GeoGebra 交互画布 -->
    <div v-if="applets.length" class="dfv__applets">
      <div v-for="(a, ai) in applets" :key="ai" class="dfv__applet-wrap" :style="{ height: height + 'px' }">
        <div class="dfv__head" v-if="a.caption || toolbar">
          <span class="dfv__caption" v-if="a.caption">{{ a.caption }}</span>
          <span class="dfv__tag" v-if="a.view === '3d'">3D · 可旋转</span>
          <span class="dfv__tag" v-else>2D · 可拖动/缩放</span>
          <span class="dfv__spacer" />
          <span class="dfv__zoom" v-if="toolbar" title="动态交互：滚轮缩放、拖拽平移、3D 按住旋转">
            <button type="button" @click.stop="zoomIn(ai)">＋</button>
            <button type="button" @click.stop="zoomOut(ai)">－</button>
            <button type="button" @click.stop="resetView(ai)" title="适应窗口">⤢</button>
            <button type="button" @click.stop="toggleFullscreen" title="全屏">{{ fullscreen ? '✕' : '⛶' }}</button>
          </span>
        </div>
        <div :id="containerId(ai)" class="dfv__canvas" />
      </div>
    </div>

    <!-- 静态图兜底（离线 / 无 ggb 构造 / 图片加载失败） -->
    <div v-if="shownImages.length" class="dfv__static" :class="{ 'dfv__static--grid': shownImages.length > 1 }">
      <img
        v-for="(im, i) in shownImages" :key="'s' + i"
        :src="im.src" :alt="im.alt" class="dfv__img"
        @error="onImgError(im.src)"
        @click="lightbox(im.src)"
      />
    </div>
    <div v-if="allImagesFailed" class="dfv__state">配图暂不可用</div>

    <div v-if="loading" class="dfv__state">⏳ 正在加载交互图形引擎…</div>
    <div v-else-if="engineError" class="dfv__state">{{ engineError }}</div>
    <div v-else-if="!applets.length && !staticImages.length && !items.length" class="dfv__state">暂无配图</div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { openLightbox } from '@/utils/lightbox'

/**
 * 动态数学图形查看器（GeoGebra 内核，MathMover 同款交互）
 *
 * items 每项可为（后端 normalize_error_assets 规范契约，仅两类）：
 * - { type:'image', src, alt }：静态图（data URI / URL）→ <img> 兜底
 * - { type:'ggb', view:'2d'|'3d', commands:[...], caption? } → GeoGebra 交互画布
 *   （拖拽平移、滚轮缩放、3D 按住旋转、滑块动点、工具栏缩放/全屏）
 * 兼容历史字符串形态（直接当 src）。图片加载失败（如本地原图 .bin 丢失 → raw 404）
 * 时移除该图并显示"配图暂不可用"空态，不留破损图标。
 *
 * 引擎来源：www.geogebra.org/apps/deployggb.js（CDN）。
 * 引擎加载失败 → 自动降级为静态 <img>，绝不阻塞页面。
 */
const props = defineProps({
  items: { type: Array, default: () => [] },
  height: { type: Number, default: 260 },
  label: { type: String, default: '题目配图' },
  toolbar: { type: Boolean, default: true },
})

const loading = ref(true)
const engineError = ref('')
const apiMap = new Map() // containerId -> ggb api
const viewMap = new Map() // containerId -> '2d'|'3d'
const fullscreen = ref(false)
let ggbScriptPromise = null

/* 解析 items：拆成 ggb 构造 与 静态图 */
const applets = computed(() =>
  (props.items || [])
    .filter((it) => it && typeof it === 'object' && it.type === 'ggb' && Array.isArray(it.commands) && it.commands.length)
    .map((it) => ({ view: it.view === '3d' ? '3d' : '2d', commands: it.commands, caption: it.caption || '' }))
)
/* 静态图：规范 {type:'image'} 对象优先，历史字符串兼容 */
const staticImages = computed(() =>
  (props.items || [])
    .map((it) => {
      if (typeof it === 'string' && it) return { src: it, alt: props.label }
      if (it && typeof it === 'object' && it.type === 'image' && it.src) return { src: it.src, alt: it.alt || props.label }
      return null
    })
    .filter(Boolean)
)
/* 加载失败的 src（onerror 记入；items 变化时重置重试） */
const failedSrcs = ref(new Set())
const shownImages = computed(() => staticImages.value.filter((im) => !failedSrcs.value.has(im.src)))
const allImagesFailed = computed(() =>
  staticImages.value.length > 0 && shownImages.value.length === 0 && !applets.value.length
)
function onImgError(src) { failedSrcs.value.add(src) }

function containerId(ai) { return `dfv-ggb-${uid}-${ai}` }
const uid = Math.random().toString(36).slice(2, 8)

/* 加载 GeoGebra 部署脚本（单例） */
function ensureGgbScript() {
  if (ggbScriptPromise) return ggbScriptPromise
  if (window.GGBApplet) { ggbScriptPromise = Promise.resolve(); return ggbScriptPromise }
  ggbScriptPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = 'https://www.geogebra.org/apps/deployggb.js'
    s.async = true
    s.onload = () => { if (window.GGBApplet) resolve(); else reject(new Error('GeoGebra 引擎加载失败')) }
    s.onerror = () => reject(new Error('GeoGebra 引擎加载失败（离线？）'))
    document.head.appendChild(s)
  })
  return ggbScriptPromise
}

/* 逐行执行 GGB 命令（移植 Any2GGB ggb_host.js 判错逻辑） */
const ASSIGN_RE = /^([A-Za-z][A-Za-z0-9_']*)\s*(?:\([a-zA-Z ,]*\))?\s*=/
const PERSPECTIVE_RE = /^#\s*perspective\s*:\s*(2d|3d)\s*$/i
const VIEW_RE = /^#\s*view\s*:\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)/i
const VIEW3D_RE = /^#\s*view3d\s*:\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)/i
const ANIM_RE = /^StartAnimation\(\s*([A-Za-z][A-Za-z0-9_]*)\s*\)/
const CREATION_RE = /^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*([A-Za-z][A-Za-z0-9_]*)\s*\(/

function execLine(api, raw) {
  const s = String(raw || '').trim()
  const pm = s.match(PERSPECTIVE_RE)
  if (pm) { setPerspective(api, pm[1].toLowerCase()); return }
  const v3 = s.match(VIEW3D_RE)
  if (v3) {
    try { api.setCoordSystem(+v3[1], +v3[4], +v3[2], +v3[5], +v3[3], +v3[6], true) } catch (e) { /* ignore */ }
    return
  }
  const vm = s.match(VIEW_RE)
  if (vm) {
    try { api.setCoordSystem(+vm[1], +vm[3], +vm[2], +vm[4]) } catch (e) { /* ignore */ }
    return
  }
  if (!s || s.startsWith('#')) return
  if (ASSIGN_RE.test(s)) {
    const m = ASSIGN_RE.exec(s)
    try {
      const labels = api.evalCommandGetLabels(s)
      if ((labels === null || labels === undefined || labels === '') && !api.exists(m[1])) {
        console.warn('[DynamicFigure] GGB 命令执行失败:', s)
      }
    } catch (e) {
      console.warn('[DynamicFigure] GGB 命令异常:', s, e)
    }
    return
  }
  try { api.evalCommand(s) } catch (e) { console.warn('[DynamicFigure] GGB 命令异常:', s, e) }
  const anim = s.match(ANIM_RE)
  if (anim) { try { api.setAnimating(anim[1], true); api.startAnimation() } catch (e) { /* ignore */ } }
}

function setPerspective(api, space) {
  try {
    if (space === '3d') { api.enable3D(true); api.setPerspective('T') }
    else api.setPerspective('G')
  } catch (e) { /* ignore */ }
}

function syncSize(id) {
  const api = apiMap.get(id)
  const el = document.getElementById(id)
  if (!api || !el) return
  const w = Math.max(320, el.clientWidth || 600)
  const h = Math.max(240, el.clientHeight || 400)
  try { api.setSize(w, h) } catch (e) { /* ignore */ }
}

async function mountApplet(ai) {
  const applet = applets.value[ai]
  if (!applet) return
  const id = containerId(ai)
  try {
    await ensureGgbScript()
  } catch (e) {
    engineError.value = e.message || 'GeoGebra 引擎不可用，已显示静态图'
    loading.value = false
    return
  }
  await new Promise((resolve) => setTimeout(resolve, 20))
  const el = document.getElementById(id)
  if (!el) { loading.value = false; return }
  const ggb = new window.GGBApplet(
    {
      id,
      appName: 'classic',
      width: Math.max(320, el.clientWidth || 640),
      height: Math.max(240, el.clientHeight || 360),
      showToolBar: false,
      showToolBarHelp: false,
      showAlgebraInput: false,
      showMenuBar: false,
      enableRightClick: true,
      showResetIcon: false,
      errorDialogsActive: false,
      language: 'zh-CN',
      appletOnLoad: (api) => {
        apiMap.set(id, api)
        viewMap.set(id, applet.view)
        try { setPerspective(api, applet.view) } catch (e) { /* ignore */ }
        applet.commands.forEach((c) => execLine(api, c))
        syncSize(id)
        loading.value = false
      },
    },
    true,
  )
  try { ggb.inject(id) } catch (e) { engineError.value = 'GeoGebra 画布初始化失败'; loading.value = false }
}

function zoomIn(ai) { zoom(ai, 1.35) }
function zoomOut(ai) { zoom(ai, 1 / 1.35) }
function zoom(ai, factor) {
  const api = apiMap.get(containerId(ai))
  if (!api) return
  try { api.zoom(factor) } catch (e) {
    // 降级：按视窗范围缩放
    try {
      const p = api.getViewProperties(1)
      const props = typeof p === 'string' ? JSON.parse(p) : p
      if (props) {
        const cx = (props.xmin + props.xmax) / 2
        const cy = (props.ymin + props.ymax) / 2
        const sx = (props.xmax - props.xmin) / factor / 2
        const sy = (props.ymax - props.ymin) / factor / 2
        api.setCoordSystem(cx - sx, cx + sx, cy - sy, cy + sy)
      }
    } catch (e2) { /* ignore */ }
  }
}

function resetView(ai) {
  const api = apiMap.get(containerId(ai))
  if (!api) return
  try { api.setCoordSystem(-8, 8, -5.4, 5.4) } catch (e) { /* ignore */ }
}

function toggleFullscreen() {
  fullscreen.value = !fullscreen.value
  setTimeout(() => {
    applets.value.forEach((_, ai) => syncSize(containerId(ai)))
  }, 50)
}

function lightbox(src) { openLightbox(src) }

watch(
  () => props.items,
  () => {
    // items 变化（如错题本生成动态图后）→ 重新挂载；失败记录重置（新图重试）
    failedSrcs.value = new Set()
    applets.value.forEach((_, ai) => {
      const id = containerId(ai)
      const el = document.getElementById(id)
      if (el) { try { el.innerHTML = '' } catch (e) { /* ignore */ } }
      apiMap.delete(id)
    })
    loading.value = applets.value.length > 0
    engineError.value = ''
    if (applets.value.length) setTimeout(() => mountApplet(0), 0)
    else loading.value = false
  },
)

onMounted(() => {
  if (applets.value.length) {
    loading.value = true
    mountApplet(0)
  } else {
    loading.value = false
  }
})

onBeforeUnmount(() => {
  applets.value.forEach((_, ai) => {
    const api = apiMap.get(containerId(ai))
    if (api && typeof api.removeApplet === 'function') { try { api.removeApplet() } catch (e) { /* ignore */ } }
  })
  apiMap.clear()
  viewMap.clear()
})
</script>

<style scoped>
.dfv { position: relative; width: 100%; }
.dfv--fullscreen {
  position: fixed; inset: 0; z-index: 90; background: #fff;
  padding: 14px; box-sizing: border-box; overflow: auto;
}
.dfv__applets { display: flex; flex-direction: column; gap: 12px; }
.dfv__applet-wrap {
  width: 100%; border: 1px solid var(--line, #e5e7eb);
  border-radius: 10px; overflow: hidden; background: #fff;
}
.dfv__head {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px;
  border-bottom: 1px solid var(--line, #eef1f5); background: #fafbfc;
}
.dfv__caption { font-size: 12px; font-weight: 700; color: var(--ink, #1f2937); }
.dfv__tag { font-size: 10.5px; font-weight: 700; color: #1f5f98; background: #eef5ff; border-radius: 999px; padding: 2px 8px; }
.dfv__spacer { flex: 1; }
.dfv__zoom { display: inline-flex; gap: 4px; }
.dfv__zoom button {
  border: 1px solid var(--line, #dbe3ee); background: #fff; color: #1f5f98;
  border-radius: 6px; min-width: 26px; height: 24px; cursor: pointer; font-weight: 800; font-size: 13px; line-height: 1;
}
.dfv__zoom button:hover { background: #eef5ff; }
.dfv__canvas { width: 100%; height: 100%; min-height: 220px; }
.dfv__static { margin: 8px 0; text-align: center; }
.dfv__static--grid { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.dfv__img {
  max-width: 100%; max-height: 260px; border: 1px solid var(--line, #e5e7eb);
  border-radius: 8px; background: #fff; cursor: zoom-in;
}
.dfv__state { padding: 18px; text-align: center; color: var(--ink3, #9aa1ac); font-size: 12.5px; }
</style>
