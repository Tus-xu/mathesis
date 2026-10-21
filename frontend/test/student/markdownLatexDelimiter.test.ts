import { describe, expect, it } from 'vitest'
import { renderMarkdown } from '@/utils/markdown'

describe('学生对话中的 LaTeX 定界符', () => {
  it('保留并渲染 \\(...\\) 内的完整数学内容，而不是显示替换占位符', () => {
    const html = renderMarkdown('定义域需要满足：\\(x^2\\le 1\\)，且 \\(-2\\sqrt{x}\\le 1\\)。')

    expect(html).toContain('katex')
    expect(html).toContain('x^2\\le 1')
    expect(html).toContain('-2\\sqrt{x}\\le 1')
    expect(html).not.toContain('$1$')
  })
})
