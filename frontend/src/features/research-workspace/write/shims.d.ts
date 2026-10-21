/**
 * Vite 资源导入的局部类型声明（本仓 `tsconfig` 未启用 `vite/client` types）。
 *
 * 仅覆盖本项目用到的一类：`*?url`（Vite 返回资源 URL 字符串）。
 * 作用域限定在 `features/research-workspace/write/**`，不影响全局类型。
 */

declare module '*?url' {
  const src: string
  export default src
}

declare module '*?worker&url' {
  const src: string
  export default src
}
