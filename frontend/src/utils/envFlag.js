/**
 * 环境开关判定：**只有显式真值才算开启**。
 *
 * 为什么需要这个函数（2026-09-14 真实踩坑）：
 *   vite.config.js 此前写作 `const useMock = !!process.env.VITE_USE_MOCK`。
 *   但环境变量的值永远是**字符串**，`!!` 只判空：
 *
 *     !!'1'     === true
 *     !!'false' === true   ← 想「关掉」，结果打开了
 *     !!'0'     === true   ← 最自然的关闭写法，也打开了
 *     !!''      === false
 *     !!undefined === false
 *
 *   于是 `VITE_USE_MOCK=false npm run dev` 会**反而进入 mock 模式**，
 *   同时 vite.config.js 里 `proxy: useRealApi ? {...} : undefined` 整块不启用，
 *   所有 /api 请求落到 Vite 自身返回 404 —— 前端表现为
 *   「教师备课服务暂时不可用」，且 mock 假数据会污染班级等真实目录。
 *
 * 语义约定：'1' / 'true' / 'yes' / 'on'（大小写不敏感，去首尾空格）= 开；
 * 其它一切值（含 'false' / '0' / 'no' / 'off' / '' / undefined）= 关。
 */
const TRUTHY = new Set(['1', 'true', 'yes', 'on'])

export function isEnvFlagOn(value) {
  return TRUTHY.has(String(value ?? '').trim().toLowerCase())
}
