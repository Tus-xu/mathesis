/**
 * pdfjs worker 的 Vite `?url` 资源说明符声明。
 * `tsconfig.types` 未包含 `vite/client`，故此处为本命名空间补一份最小声明
 * （只声明本项目实际使用的那一个说明符，避免影响其它模块）。
 */
declare module 'pdfjs-dist/build/pdf.worker.min.mjs?url' {
  const src: string
  export default src
}
