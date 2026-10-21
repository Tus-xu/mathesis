import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { mockApi } from './src/mock/server'
import { canonicalLocalOrigin } from './src/utils/localOrigin.js'
import { isEnvFlagOn } from './src/utils/envFlag.js'

// 默认：连真实后端（/api 代理到 127.0.0.1:8000）
// 需要演示假数据时显式开启 mock：VITE_USE_MOCK=1 npm run dev（mock 中间件完整模拟 /api，代理不启用）
//
// ⚠️ 2026-09-14 真实踩坑：此前写作 `!!process.env.VITE_USE_MOCK`，而环境变量是字符串，
// `!!'false' === true`、`!!'0' === true` —— 任何显式传值（包括想「关掉」的写法）
// 都会**反而打开 mock**，同时下面的 `proxy: useRealApi ? {...} : undefined` 整块不启用，
// 所有 /api 请求落到 Vite 自身返回 404，前端表现为「教师备课服务暂时不可用」。
// 现改用显式真值白名单（isEnvFlagOn），只有 1/true/yes/on 才算开。
const useMock = isEnvFlagOn(process.env.VITE_USE_MOCK)
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'
// 教师平台独立栈（02-ARCHITECTURE §1.3）：/api/teacher-v3 优先于 /api 命中 :8100
const teacherApiProxyTarget = process.env.VITE_TEACHER_API_PROXY_TARGET || 'http://127.0.0.1:8100'
// PPT Studio V2（8200 独立服务）：开发代理仅由统一路径进入 8200（R15；V2 不并入 8100 legacy 域）
const pptStudioProxyTarget = process.env.VITE_PPT_STUDIO_API_PROXY_TARGET || 'http://127.0.0.1:8200'
// Research VNext Core（8300 独立服务，P00 registry 固化端口）：
// 开发代理由统一同源路径 /api/research-vnext 进入 8300 —— 浏览器仍只看到统一 origin，
// 不直连 8300；生产由统一域名/gateway 承担同一转发（registry browser_boundary）。
// 兼容旧开关：VITE_REAL_API=1 无副作用（真实后端已是默认）
const useRealApi = !useMock
const devPort = Number(process.env.PORT) || 5176

// ── Research DEV 签名桥：已于 2026-09-17 移除 ─────────────────────────────
// 原实现读取 services/research-vnext/.env 的 RESEARCH_GATEWAY_HMAC_SECRET，并自行注入
// X-Research-Access-Context / -Sig 到 /api/research-vnext/*。移除理由：
//   ① 违反本仓 AGENTS.md 科研端铁律「禁止前端自造 X-Research-Access-* 签名头」；
//   ② 网关注入已在 8000 实现（见下方 proxy 注释），本桥纯属冗余；
//   ③ 它注入的 window.__MA_RESEARCH_CONTEXT__ 在前端**已无任何读取点**（全仓 0 命中）；
//   ④ 原判断逻辑为「默认开启」（仅 VITE_RESEARCH_SIGN_BRIDGE=0 才关）＝默认即读密钥。


/** Keep the dev server on one origin so ma_refresh/ma_csrf are always sent. */
function canonicalLocalHost() {
  return {
    name: 'canonical-local-auth-origin',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const location = canonicalLocalOrigin(req.url, req.headers.host, devPort)
        if (!location) return next()
        res.statusCode = 307
        res.setHeader('Location', location)
        res.end()
      })
    },
  }
}

export default defineConfig({
  plugins: [
    vue(),
    canonicalLocalHost(),
    useMock && {
      name: 'mock-api-server',
      configureServer(server) {
        server.middlewares.use('/api', (req, res, next) => {
          mockApi(req, res, next)
        })
      },
    },
  ].filter(Boolean),
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    // 双师课堂验收契约：主入口必须是 http://127.0.0.1:5176/dual
    // （显式绑定 IPv4，避免 localhost 解析成 ::1 导致 127.0.0.1 无法访问）
    // PORT 环境变量优先（预览面板 autoPort 派发）；缺省 5176 保持验收契约
    host: '127.0.0.1',
    port: devPort,
    // 内网穿透（cloudflared quick tunnel / cpolar）访问时 Host 是公网域名，
    // vite 5+ 默认拦截未知 Host（403 Blocked request）；放行穿透域名。
    allowedHosts: ['.trycloudflare.com', '.cpolar.cn', '.cpolar.top', '.ngrok-free.app', '.natappfree.cc'],
    proxy: useRealApi
      ? {
          // 教师平台优先命中（键序即匹配序，前缀更长者在前）
          // 生成接口为真 LLM（大纲/教案/逐页），单次可长达 2-3 分钟：proxyTimeout 必须放开，
          // 否则 Vite 默认 30s 断开 → 前端误报「mock 未启动」
          '/api/teacher-v3': { target: teacherApiProxyTarget, changeOrigin: true, proxyTimeout: 300000, timeout: 300000 },
          // PPT Studio V2（8200）：统一路径 /api/ppt-studio-v2 进入独立服务；浏览器不可直连 8201
          '/api/ppt-studio-v2': { target: pptStudioProxyTarget, changeOrigin: true, proxyTimeout: 300000, timeout: 300000 },
          // Research VNext（BACKEND-CONTRACT §1 D2）：统一路径 /api/research-vnext 必须经
          // **8000 网关**（JWT 校验 + 派生 AccessContext + 注入 HMAC）再转发 8300。
          // 直连 8300 会因缺少网关注入的 X-Research-Access-* 而**恒 401 fail-closed**。
          // 必须排在 '/api' 之前（键序即匹配序），否则会被 8000 兜底吞掉。
          // SSE 由该前缀下的 /sessions/{id}/events 承载，故同样放开 proxyTimeout。
          '/api/research-vnext': { target: apiProxyTarget, changeOrigin: true, proxyTimeout: 300000, timeout: 300000 },
          // 学生 AI（对话/引导解题/思考/直接看答案）与知识库统一切回 :8000：
          // /api/agent/* 由 agent_router + skills 承接；/api/v1/* 由 v1_compat_router 承接。
          // 原 :8012 集成服务（B1/B2）已下线且源码不在仓库，不得再作为学生 AI 的代理目标。
          '/api': { target: apiProxyTarget, changeOrigin: true, proxyTimeout: 300000, timeout: 300000 },
        }
      : undefined,
    // research-repos 是克隆的参考仓库（工作材料，非本应用源码），不参与 Vite 监听，避免 full-reload 抖动
    watch: { ignored: ['**/research-repos/**', '**/dist/**'] },
  },
  build: {
    chunkSizeWarningLimit: 1600,
  },
})
