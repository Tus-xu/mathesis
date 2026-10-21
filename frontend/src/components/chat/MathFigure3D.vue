<template>
  <!-- MathFigure3D：双师课堂交互配图（three.js + OrbitControls）
       输入 scene（受控 DSL，后端已采样折线点集），支持拖拽旋转 / 滚轮缩放 / 右键平移，
       工具栏：重置视角 / 放大 / 缩小 / 网格开关。无 WebGL 时优雅降级，绝不白屏。 -->
  <div class="mf3d" :class="{ 'mf3d--bare': bare }">
    <div v-if="!bare" class="mf3d__bar">
      <span class="mf3d__title">🧊 交互图形</span>
      <span class="mf3d__caption">{{ caption || '（无图注）' }}</span>
      <span class="mf3d__hint">拖拽旋转 · 滚轮缩放 · 右键平移</span>
      <span class="mf3d__tools">
        <button type="button" title="重置视角" @click="resetView">⟳ 重置</button>
        <button type="button" title="放大" @click="zoom(0.78)">＋</button>
        <button type="button" title="缩小" @click="zoom(1.28)">－</button>
        <button type="button" :class="{ on: gridOn }" title="显示网格与坐标轴" @click="toggleGrid">网格</button>
      </span>
    </div>
    <div ref="host" class="mf3d__canvas" :style="{ height: height + 'px' }"></div>
    <div v-if="err" class="mf3d__err">{{ err }}</div>
  </div>
</template>

<script setup>
/* MathFigure3D —— three.js 渲染双师课堂 figure（受控 DSL）。
 *
 * 关键设计（适配 LLM 输出"挤在原点"或"过密"的坐标）：
 * 1. box / cylinder / cone / sphere 渲染时强制使用绝对坐标 + 已知 size，
 *    不依赖 scaleToTarget 后再"自动取景"——避免 LLM 输出 0.5 单位物体被相机丢在远点。
 * 2. fitCamera 用 root 包围盒动态决定 dist，留 1.7x 余量 + fov 反推最终距离。
 * 3. 灯光升级为三点照明（key + fill + rim）+ 半球补光，让半透物体有明暗对比。
 * 4. 实体材质统一用 MeshStandardMaterial（metalness 0.08, roughness 0.55），
 *    半透时 depthWrite=true 避免互相穿透看不清。
 * 5. 网格大小根据场景 span 动态调整，避免网格喧宾夺主。
 * 6. 顶点/棱/标签始终绘制（点用 SphereGeometry 0.08，棱深色高亮）。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { ConvexGeometry } from 'three/addons/geometries/ConvexGeometry.js'
import { normalizeScene, toPt } from '@/utils/mathFigure3d'

const props = defineProps({
  figure: { type: Object, default: null },
  caption: { type: String, default: '' },
  height: { type: Number, default: 360 },
  /** 教师课件静态化：只显示图片边界，不显示工具栏、不响应交互 */
  bare: { type: Boolean, default: false },
})

const host = ref(null)
const err = ref('')
const gridOn = ref(true)

let renderer = null

// PHASE 5：课件导出用——截取当前 WebGL 帧（preserveDrawingBuffer 已开）
defineExpose({
  toDataURL: () => {
    try { return renderer?.domElement?.toDataURL('image/png') || '' } catch { return '' }
  },
})
let scene = null
let camera = null
let controls = null
let root = null
let gridGroup = null
let rafId = 0
let resizeObserver = null
const disposables = []

function darken(hex, f = 0.6) {
  const n = parseInt(hex.slice(1), 16)
  const r = Math.round(((n >> 16) & 255) * f)
  const g = Math.round(((n >> 8) & 255) * f)
  const b = Math.round((n & 255) * f)
  return `rgb(${r},${g},${b})`
}
function v3(v) {
  return new THREE.Vector3(v[0], v[1], v[2])
}
function trackDispose(obj) {
  disposables.push(obj)
  return obj
}

function solidMaterial(color, opacity) {
  return new THREE.MeshStandardMaterial({
    color,
    transparent: opacity < 0.95,
    opacity,
    side: THREE.DoubleSide,
    metalness: 0.08,
    roughness: 0.55,
    flatShading: false,
    depthWrite: opacity >= 0.95,
  })
}

function addMesh(geometry, material) {
  const m = new THREE.Mesh(geometry, material)
  root.add(m)
  trackDispose(geometry)
  trackDispose(material)
  return m
}

function addLine(points3, opts = {}) {
  const pts = points3.map((p) => (Array.isArray(p) ? v3(p) : p))
  const geo = trackDispose(new THREE.BufferGeometry().setFromPoints(pts))
  const color = opts.color || '#0f172a'
  if (opts.dashed) {
    const mat = trackDispose(new THREE.LineDashedMaterial({ color, dashSize: 0.18, gapSize: 0.1 }))
    const line = new THREE.Line(geo, mat)
    line.computeLineDistances()
    root.add(line)
    return line
  }
  const mat = trackDispose(new THREE.LineBasicMaterial({ color, linewidth: 2 }))
  const line = new THREE.Line(geo, mat)
  root.add(line)
  return line
}

function addEdgeSegments(segments3, color, opacity = 1) {
  const all = []
  segments3.forEach(([a, b]) => all.push(v3(a), v3(b)))
  const geo = trackDispose(new THREE.BufferGeometry().setFromPoints(all))
  const mat = trackDispose(new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity }))
  const line = new THREE.LineSegments(geo, mat)
  root.add(line)
  return line
}

const _SUB = ['₀', '₁', '₂', '₃', '₄', '₅', '₆', '₇', '₈', '₉']
// P1-16B：标签清洗——数字转下标字符（S4→S₄、A0→A₀、SA40→SA₄₀），避免数字贴尾的"乱码"观感
function cleanLabel(raw) {
  const t = String(raw ?? '').replace(/\s+/g, '').slice(0, 6)
  return t.replace(/\d/g, (d) => _SUB[+d] ?? d)
}
function makeLabel(text, pos) {
  text = cleanLabel(text)
  const c = document.createElement('canvas')
  const ctx = c.getContext('2d')
  const fs = 64
  c.width = Math.ceil(fs * Math.max(1.6, text.length * 0.85))
  c.height = Math.ceil(fs * 1.25)
  ctx.font = `700 ${fs}px "PingFang SC","Microsoft YaHei",sans-serif`
  ctx.textBaseline = 'middle'
  ctx.lineWidth = 10
  ctx.strokeStyle = 'rgba(255,255,255,0.95)'
  ctx.strokeText(text, 6, c.height / 2)
  ctx.fillStyle = '#0f172a'
  ctx.fillText(text, 6, c.height / 2)
  const tex = trackDispose(new THREE.CanvasTexture(c))
  tex.colorSpace = THREE.SRGBColorSpace
  tex.minFilter = THREE.LinearFilter
  const mat = trackDispose(
    new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }),
  )
  const spr = new THREE.Sprite(mat)
  spr.position.copy(v3(pos))
  const w = text.length > 2 ? 1.1 : 0.7
  spr.scale.set(w, 0.65, 1)
  spr.renderOrder = 100
  root.add(spr)
}

function addSolid(spec) {
  const color = spec.color || '#4f8ef7'
  if (spec.kind === 'box') {
    const [w, h, d] = spec.size
    const geo = trackDispose(new THREE.BoxGeometry(w, h, d))
    const m = addMesh(geo, solidMaterial(color, spec.opacity))
    m.position.copy(v3(spec.center))
    const edges = trackDispose(new THREE.EdgesGeometry(geo))
    const em = trackDispose(
      new THREE.LineBasicMaterial({ color: darken(color, 0.45), transparent: true, opacity: 0.95 }),
    )
    const ls = new THREE.LineSegments(edges, em)
    ls.position.copy(v3(spec.center))
    root.add(ls)
    ;(spec.labels || []).forEach((lb) => makeLabel(lb.text, lb.pos))
    return
  }
  if (spec.kind === 'sphere') {
    const geo = trackDispose(new THREE.SphereGeometry(spec.radius, 48, 48))
    const m = addMesh(geo, solidMaterial(color, spec.opacity))
    m.position.copy(v3(spec.center))
    // 球面用一条水平圆作"经线"增强立体感
    const circGeo = trackDispose(new THREE.BufferGeometry())
    const circPts = []
    const r = spec.radius
    for (let i = 0; i <= 64; i++) {
      const a = (i / 64) * Math.PI * 2
      circPts.push(new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r))
    }
    circGeo.setFromPoints(circPts)
    const circMat = trackDispose(
      new THREE.LineBasicMaterial({ color: darken(color, 0.45), transparent: true, opacity: 0.85 }),
    )
    const circ = new THREE.Line(circGeo, circMat)
    circ.position.copy(v3(spec.center))
    circ.rotation.x = Math.PI / 2
    root.add(circ)
    ;(spec.labels || []).forEach((lb) => makeLabel(lb.text, lb.pos))
    return
  }
  if (spec.kind === 'cylinder' || spec.kind === 'cone') {
    const base = v3(spec.base)
    const top = spec.top ? v3(spec.top) : base.clone().add(new THREE.Vector3(0, 2, 0))
    const axis = new THREE.Vector3().subVectors(top, base)
    const height = axis.length() || 1
    const dir = axis.clone().normalize()
    const geo =
      spec.kind === 'cone'
        ? trackDispose(new THREE.ConeGeometry(spec.radius, height, 56))
        : trackDispose(new THREE.CylinderGeometry(spec.radius, spec.radius, height, 56, 1, false))
    const m = addMesh(geo, solidMaterial(color, spec.opacity))
    m.position.copy(base.clone().add(dir.clone().multiplyScalar(height / 2)))
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir)
    const edges = trackDispose(new THREE.EdgesGeometry(geo))
    const em = trackDispose(
      new THREE.LineBasicMaterial({ color: darken(color, 0.45), transparent: true, opacity: 0.95 }),
    )
    const ls = new THREE.LineSegments(edges, em)
    ls.position.copy(m.position)
    ls.quaternion.copy(m.quaternion)
    root.add(ls)
    ;(spec.labels || []).forEach((lb) => makeLabel(lb.text, lb.pos))
    return
  }
  if (spec.kind === 'polyhedron') {
    const verts = spec.vertices.map((v) => v3(v.pos))
    const nameIndex = new Map(spec.vertices.map((v, i) => [v.name, i]))
    const lineColor = darken(color, 0.35)
    // 1. 凸包面：LLM 只给顶点没给 faces 时用 ConvexGeometry 自动算凸包面。
    //    没有面就只看到线框，观感极差（"一堆线 + 8 个点"）。
    if (verts.length >= 4) {
      try {
        const hullGeo = trackDispose(new ConvexGeometry(verts))
        // 凸包面比其它面更透一些，让边线清晰可见
        const hullOpacity = Math.max(0.18, Math.min(0.55, (spec.opacity || 0.35) * 0.85))
        addMesh(hullGeo, solidMaterial(color, hullOpacity))
      } catch (e) {
        // ConvexGeometry 失败（<4 顶点等），降级为纯骨架
      }
    }
    // 2. 边线（保留）：底层深色阴影 + 上层彩色细线，增强对比
    ;(spec.edges || []).forEach((e) => {
      const a = nameIndex.get(e[0])
      const b = nameIndex.get(e[1])
      if (a !== undefined && b !== undefined) {
        const thickMat = trackDispose(
          new THREE.LineBasicMaterial({ color: '#0f172a', transparent: true, opacity: 0.55 }),
        )
        const thickGeo = trackDispose(new THREE.BufferGeometry().setFromPoints([verts[a], verts[b]]))
        root.add(new THREE.Line(thickGeo, thickMat))
        addLine([verts[a], verts[b]], { color: lineColor })
      }
    })
    // 3. 顶点：SphereGeometry 0.1（比原 0.08 更大，更醒目）
    const dotGeo = trackDispose(new THREE.SphereGeometry(0.1, 18, 18))
    const dotMat = trackDispose(
      new THREE.MeshStandardMaterial({ color: '#0f172a', roughness: 0.4 }),
    )
    spec.vertices.forEach((v) => {
      const dot = new THREE.Mesh(dotGeo, dotMat)
      dot.position.copy(v3(v.pos))
      root.add(dot)
    })
    spec.vertices.forEach((v) => makeLabel(v.name, v.pos))
    ;(spec.labels || []).forEach((lb) => makeLabel(lb.text, lb.pos))
    return
  }
  if (spec.kind === 'prism' || spec.kind === 'pyramid') {
    const bottom = spec.bottom || spec.base
    const top = spec.kind === 'prism' ? spec.top : null
    const apex = spec.kind === 'pyramid' ? v3(spec.apex) : null
    const n = bottom.length
    const bPts = bottom.map((p) => v3(p))
    const tPts = top ? top.map((p) => v3(p)) : null
    const positions = []
    bPts.forEach((p) => positions.push(p.x, p.y, p.z))
    if (tPts) tPts.forEach((p) => positions.push(p.x, p.y, p.z))
    else if (apex) positions.push(apex.x, apex.y, apex.z)
    const geo = trackDispose(new THREE.BufferGeometry())
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
    geo.computeVertexNormals()
    const indices = []
    const offTop = n
    // 底面扇形（法线朝下：0,i+1,i）
    for (let i = 1; i < n - 1; i++) indices.push(0, i + 1, i)
    if (tPts) {
      // 顶面扇形（法线朝上：offTop, offTop+i, offTop+i+1）
      for (let i = 1; i < n - 1; i++) indices.push(offTop, offTop + i, offTop + i + 1)
      // 侧面四边形
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n
        indices.push(i, offTop + i, offTop + j)
        indices.push(i, offTop + j, j)
      }
    } else if (apex) {
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n
        indices.push(i, j, offTop)
      }
    }
    geo.setIndex(indices)
    addMesh(geo, solidMaterial(color, spec.opacity))
    const edgesSeg = []
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n
      edgesSeg.push([bPts[i], bPts[j]])
      if (tPts) edgesSeg.push([tPts[i], tPts[j]])
      if (tPts) edgesSeg.push([bPts[i], tPts[i]])
      else if (apex) edgesSeg.push([bPts[i], apex])
    }
    addEdgeSegments(edgesSeg, darken(color, 0.4))
    ;(spec.labels || []).forEach((lb, i) => {
      const p = i < n ? bottom[i] : null
      if (p) makeLabel(lb.text, p)
    })
    if (apex) {
      const apLabel = spec.labels && spec.labels.length > n ? spec.labels[n] : null
      if (apLabel) makeLabel(apLabel.text, spec.apex)
    }
    return
  }
}

function addPlane(p) {
  const [p1, p2, p3] = p.points.map((v) => v3(v))
  const a = new THREE.Vector3().subVectors(p2, p1)
  const b = new THREE.Vector3().subVectors(p3, p1)
  const n = new THREE.Vector3().crossVectors(a, b).normalize()
  const center = new THREE.Vector3().add(p1).add(p2).add(p3).divideScalar(3)
  const size = Math.max(a.length(), b.length()) || 2
  const geo = trackDispose(new THREE.PlaneGeometry(size * 2, size * 2))
  const m = addMesh(geo, solidMaterial(p.color, p.opacity))
  m.position.copy(center)
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), n)
  addLine([p1, p2, p3, p1], { color: darken(p.color, 0.55) })
}

function buildScene(sceneData) {
  root = new THREE.Group()
  scene.add(root)
  gridGroup = new THREE.Group()
  scene.add(gridGroup)

  ;(sceneData.solids || []).forEach((s) => addSolid(s))
  ;(sceneData.curves || []).forEach((c) => {
    if (c.closed) {
      const pts = c.points.map((p) => v3(p))
      const geo = trackDispose(new THREE.BufferGeometry().setFromPoints(pts))
      const mat = trackDispose(new THREE.LineBasicMaterial({ color: c.color, linewidth: 2 }))
      const line = new THREE.LineLoop(geo, mat)
      root.add(line)
    } else {
      addLine(c.points, { color: c.color })
    }
  })
  ;(sceneData.planes || []).forEach((p) => addPlane(p))
  ;(sceneData.segments || []).forEach((s) => {
    const line = addLine([s.a, s.b], { color: s.color, dashed: s.dashed })
    if (s.label) {
      const mid = [(s.a[0] + s.b[0]) / 2, (s.a[1] + s.b[1]) / 2, (s.a[2] + s.b[2]) / 2]
      makeLabel(s.label, mid)
    }
  })

  // 网格 + 坐标轴：根据场景 span 调整大小
  const box = new THREE.Box3().setFromObject(root)
  const sizeVec = box.isEmpty() ? new THREE.Vector3(8, 0, 8) : box.getSize(new THREE.Vector3())
  const span = Math.max(sizeVec.length(), 2)
  const gridDiv = Math.max(6, Math.min(12, Math.round(span * 1.2)))
  const gridSize = Math.max(8, Math.ceil(span * 1.5))
  const grid = new THREE.GridHelper(gridSize, gridDiv, 0x94a3b8, 0xe2e8f0)
  grid.position.y = box.isEmpty() ? 0 : box.min.y
  gridGroup.add(grid)
  const axesLen = Math.min(8, Math.max(2, span * 0.6))
  const axes = new THREE.AxesHelper(axesLen)
  gridGroup.add(axes)
  gridGroup.visible = sceneData.grid
}

function fitCamera(sceneData) {
  const box = root ? new THREE.Box3().setFromObject(root) : null
  if (box && !box.isEmpty()) {
    const center = box.getCenter(new THREE.Vector3())
    const size = box.getSize(new THREE.Vector3())
    const maxDim = Math.max(size.x, size.y, size.z, 0.001)
    // fov 反推：让 maxDim 占满视野约 90%（更醒目，避免相机过远物体显小）
    const fov = camera.fov * (Math.PI / 180)
    const distFov = (maxDim / 2) / Math.tan(fov / 2) / 0.9
    // 按包围盒对角线 0.55x 留余量（避免贴边，原始 0.75 显得太远）
    const distDiag = size.length() * 0.55
    // 多个物体横向铺开时（多面体家族/旋转体家族）适当进一步拉近，让每个物体更醒目
    const isWide = size.x > 4 && size.x > size.y * 1.5 && size.x > size.z * 1.5
    const finalDist = isWide ? distFov * 0.85 : Math.max(distFov, distDiag, 3)
    const cam = sceneData.camera && sceneData.camera.pos
    if (cam) camera.position.copy(v3(cam))
    else {
      // 用更"等距/侧视"角度（不是俯视）：azimuth ≈ 35°，elevation ≈ 25°
      // 原 (0.7, 0.6, 0.95) 偏俯视导致 y 方向物体贴底
      const az = Math.cos(THREE.MathUtils.degToRad(35))
      const el = Math.sin(THREE.MathUtils.degToRad(25))
      camera.position.set(
        center.x + finalDist * az,
        center.y + finalDist * el,
        center.z + finalDist * az,
      )
    }
    const tgt = sceneData.camera && sceneData.camera.target
    controls.target.copy(tgt ? v3(tgt) : center)
  } else {
    camera.position.set(6, 5, 7)
    controls.target.set(0, 0, 0)
  }
  camera.near = 0.05
  camera.far = Math.max(500, camera.position.length() * 20)
  camera.updateProjectionMatrix()
  controls.update()
}

function renderTick() {
  rafId = requestAnimationFrame(renderTick)
  controls.update()
  renderer.render(scene, camera)
}

function initThree() {
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true })  // PHASE 5：preserve 供导出 toDataURL 截图
  } catch (e) {
    err.value = `当前环境不支持 WebGL，无法渲染 3D 图形（${e?.message || ''}）`
    throw e
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setSize(1, 1)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.1
  host.value.appendChild(renderer.domElement)

  scene = new THREE.Scene()
  scene.background = new THREE.Color(0xfbfcfe)

  camera = new THREE.PerspectiveCamera(45, 1, 0.05, 1000)
  camera.position.set(6, 5, 7)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.minDistance = 0.5
  controls.maxDistance = 80
  controls.target.set(0, 0, 0)
  if (props.bare) {
    controls.enableRotate = false
    controls.enableZoom = false
    controls.enablePan = false
  }

  // 三点照明 + 半球补光
  scene.add(new THREE.HemisphereLight(0xffffff, 0xe2e8f0, 0.55))
  const key = new THREE.DirectionalLight(0xffffff, 1.1)
  key.position.set(5, 9, 6)
  scene.add(key)
  const fill = new THREE.DirectionalLight(0xffffff, 0.35)
  fill.position.set(-6, 2, 4)
  scene.add(fill)
  const rim = new THREE.DirectionalLight(0xfff2dd, 0.6)
  rim.position.set(-2, 4, -8)
  scene.add(rim)
}

function sizeCanvas() {
  const el = host.value
  if (!el || !renderer) return
  const w = el.clientWidth || 320
  const h = el.clientHeight || props.height
  renderer.setSize(w, h)
  camera.aspect = w / h
  camera.updateProjectionMatrix()
}

function resetView() {
  if (!root || !controls) return
  const sceneData = normalizeScene(props.figure) || {}
  fitCamera(sceneData)
}

function zoom(factor) {
  if (!camera || !controls) return
  const dir = camera.position.clone().sub(controls.target)
  const d = dir.length() * factor
  if (d < 0.2 || d > 200) return
  camera.position.copy(controls.target).add(dir.normalize().multiplyScalar(d))
  controls.update()
}

function toggleGrid() {
  gridOn.value = !gridOn.value
  if (gridGroup) gridGroup.visible = gridOn.value
}

function disposeAll() {
  if (rafId) cancelAnimationFrame(rafId)
  rafId = 0
  if (resizeObserver) resizeObserver.disconnect()
  disposables.forEach((o) => {
    try {
      if (o && typeof o.dispose === 'function') o.dispose()
    } catch {
      /* 忽略 */
    }
  })
  disposables.length = 0
  if (renderer) {
    try {
      renderer.dispose()
    } catch {
      /* 忽略 */
    }
    const el = renderer.domElement
    if (el && el.parentNode) el.parentNode.removeChild(el)
    renderer = null
  }
  controls = null
  scene = null
  root = null
  gridGroup = null
}

function build() {
  disposeAll()
  const sceneData = normalizeScene(props.figure)
  if (!sceneData || err.value) return
  if (!renderer) initThree()
  if (err.value) return
  buildScene(sceneData)
  sizeCanvas()
  fitCamera(sceneData)
  renderTick()
}

watch(
  () => props.figure,
  () => {
    err.value = ''
    try {
      build()
    } catch (e) {
      /** 已降级显示 err */
    }
  },
  { deep: false },
)

onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => sizeCanvas())
    try {
      resizeObserver.observe(host.value)
    } catch (e) {
      /** 忽略 */
    }
  }
  window.addEventListener('resize', sizeCanvas)
  try {
    build()
  } catch (e) {
    /** WebGL 不可用等已由 initThree 置 err，此处不抛出 */
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', sizeCanvas)
  disposeAll()
})
</script>

<style scoped>
.mf3d {
  width: 100%;
  border: 1px solid var(--line, #e5e7eb);
  border-radius: var(--radius-lg, 14px);
  overflow: hidden;
  background: #fbfcfe;
  box-shadow: var(--shadow-sm, none);
}
.mf3d--bare {
  border: none;
  border-radius: 0;
  background: #fff;
  box-shadow: none;
}
.mf3d--bare .mf3d__canvas {
  cursor: default;
}
.mf3d__bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 14px;
  border-bottom: 1px solid var(--line, #eef1f5);
  background: var(--card, #fff);
}
.mf3d__title {
  font-size: 12px;
  font-weight: 800;
  color: var(--brand-deep, #b45309);
  background: var(--brand-soft, #fef3c7);
  border-radius: 999px;
  padding: 3px 10px;
}
.mf3d__caption {
  font-size: 12.5px;
  color: var(--ink2, #475569);
  flex: 1;
  min-width: 120px;
}
.mf3d__hint {
  font-size: 11px;
  color: var(--ink3, #94a3b8);
}
.mf3d__tools {
  display: inline-flex;
  gap: 6px;
}
.mf3d__tools button {
  border: 1px solid var(--line, #dbe3ee);
  background: var(--card, #fff);
  color: var(--ink2, #475569);
  border-radius: 8px;
  height: 27px;
  padding: 0 10px;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease;
}
.mf3d__tools button:hover {
  border-color: var(--brand2, #fb923c);
  color: var(--brand-deep, #b45309);
  background: var(--brand-faint, #fffbeb);
}
.mf3d__tools button.on {
  background: var(--brand, #f59e0b);
  color: #fff;
  border-color: var(--brand, #f59e0b);
}
.mf3d__canvas {
  width: 100%;
  position: relative;
}
.mf3d__canvas canvas {
  display: block;
  width: 100%;
  height: 100%;
}
.mf3d__err {
  padding: 22px;
  text-align: center;
  color: var(--err, #dc2626);
  font-size: 12.5px;
  border-top: 1px dashed var(--line, #fecaca);
}
</style>
