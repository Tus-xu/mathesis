/**
 * useImportPaper —— 文献导入 + 人话回执的**共享组合式函数**。
 *
 * READ 页有两个导入入口（ReadWorkspace 右上「＋」/ ProjectSidebar 文献卡「＋」），
 * 都必须给回执：成功「已导入…」/ 失败给后端人话错误 —— 禁 silently no-op（宪法 §70）。
 */
import { ref } from 'vue'
import { useRwPapersStore } from '../stores/papers'
import { useRwProjectStore } from '../stores/project'
import type { Paper } from '../api/types'

export function useImportPaper() {
  const papers = useRwPapersStore()
  const project = useRwProjectStore()

  const toast = ref('')
  let timer: ReturnType<typeof setTimeout> | null = null

  function flash(msg: string) {
    toast.value = msg
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { toast.value = '' }, 4000)
  }

  /** 执行导入；`onSettled` 供调用方在成功后追加行为（如刷新概览）。 */
  async function run(projectId: string, file: File): Promise<Paper | null> {
    if (!/\.pdf$/i.test(file.name) && file.type !== 'application/pdf') {
      flash('仅支持 PDF 文件，请重新选择。')
      return null
    }
    flash(`正在导入「${file.name}」…`)
    const paper = await papers.importPaper(projectId, file)
    if (paper) {
      papers.setActive(paper.id)
      void project.loadOverview(projectId).then(() => {
        flash(`已导入「${paper.title || file.name}」。`)
      })
    } else {
      const err = papers.error
      // 5xx/超时类给「存储/服务不可用」的人话；其余用后端映射的具体文案
      const msg = !err
        ? '导入失败，请重试。'
        : (err.status ?? 0) >= 500 || err.retryable
          ? '导入失败：文件存储服务暂时不可用，请稍后重试。'
          : err.message
      flash(msg)
    }
    return paper
  }

  return { toast, run }
}
