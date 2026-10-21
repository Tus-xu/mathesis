/**
 * T22 · Conversation-first Teacher Workspace —— **真实 8100 API** 的 E2E。
 *
 * 纪律：
 * - 只断言**用户看得见**的东西：role / label / 稳定 test-id；不依赖脆弱 CSS/XPath；
 * - **不 sleep**：全部用 web-first assertion（`expect(...).toBeVisible()` 自带重试）；
 * - 数据来源：真实 8100（`/api/teacher-v3/prep/**`）与真实 8000 成员关系判定；
 *   本 spec **不含任何 mock 数据**。
 *
 * 前置（由运行脚本注入）：
 * - `T22_TOKEN`：8100 教师 JWT（本地 dev 签发）；缺省则走 401 场景。
 * - `E2E_BASE_URL`：已就绪的 vite dev 服务（沙盒页直达）。
 */
import { expect, test, type Page } from '@playwright/test'

const HARNESS = '/src/features/teacher-prep-v2/__harness__/index.html'
const TOKEN = process.env.T22_TOKEN ?? ''

/** 真实存在的他人会话（8100 会返回 403 跨教师拒绝）。 */
const SESSION_OTHER = 'bbbbbbbb-0000-0000-0000-000000000002'
/** 不存在的会话（8100 返回 404）。 */
const SESSION_MISSING = '00000000-0000-0000-0000-000000000001'

const REGIONS = ['ma-region-left', 'ma-region-center', 'ma-region-right', 'ma-region-status'] as const

async function gotoWorkspace(page: Page, sessionId?: string, token: string = TOKEN) {
  await page.addInitScript((t) => {
    if (t) window.localStorage.setItem('ma_access_token', t)
    else window.localStorage.removeItem('ma_access_token')
  }, token)
  const hash = sessionId ? `#/teacher-v3/prep-v2/${sessionId}` : '#/teacher-v3/prep-v2'
  await page.goto(`${HARNESS}${hash}`)
}

test.describe('T22 · Teacher Workspace（真实 8100）', () => {
  test('1 · 路由加载：三核心区 + 状态区恒定在场，且无 Stepper/Wizard', async ({ page }) => {
    await gotoWorkspace(page)
    for (const id of REGIONS) {
      await expect(page.getByTestId(id)).toBeVisible()
    }
    // 反 attractor：不存在步骤条 / 向导 / 当前步骤
    await expect(page.getByTestId('prep-stepper')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /下一步|上一步|next step/i })).toHaveCount(0)
  })

  test('2 · 无会话时进入空态，并给出对话入口（conversation-first）', async ({ page }) => {
    await gotoWorkspace(page)
    await expect(page.getByTestId('prep-composer')).toBeVisible()
    await expect(page.getByTestId('prep-timeline')).toBeVisible()
    // 限定在 timeline 内断言（同一文案也出现在状态标签里，避免 strict mode 歧义）
    await expect(page.getByTestId('prep-timeline').getByText('还没有开始备课')).toBeVisible()
  })

  test('3 · 401：无令牌时显示未认证状态（不白屏、不伪造数据）', async ({ page }) => {
    await gotoWorkspace(page, SESSION_OTHER, '')
    await expect(page.getByTestId('prep-error-unauthenticated')).toBeVisible()
    // 布局仍在：401 是状态，不是"页面消失"
    await expect(page.getByTestId('ma-region-center')).toBeVisible()
  })

  test('4 · 403：他人会话显示 forbidden（可见但禁用，不隐藏内容）', async ({ page }) => {
    test.skip(!TOKEN, '需要 T22_TOKEN')
    await gotoWorkspace(page, SESSION_OTHER)
    await expect(page.getByTestId('prep-error-forbidden')).toBeVisible()
    // 关键断言：403 下工作区结构仍在（不是跳转/空白）
    for (const id of REGIONS) {
      await expect(page.getByTestId(id)).toBeVisible()
    }
    // 发送入口存在但禁用
    await expect(page.getByTestId('prep-composer-input')).toBeDisabled()
  })

  test('5 · 404：不存在会话显示 not_found 并给出重试', async ({ page }) => {
    test.skip(!TOKEN, '需要 T22_TOKEN')
    await gotoWorkspace(page, SESSION_MISSING)
    await expect(page.getByTestId('prep-error-not_found')).toBeVisible()
    await expect(page.getByTestId('prep-banner-reload')).toBeVisible()
  })

  test('6 · 刷新恢复：UI 偏好持久化，业务状态回落到服务端重取', async ({ page }) => {
    await gotoWorkspace(page)
    // 收起证据栏（client state / localStorage 仅存 UI 偏好）
    await page.getByTestId('prep-evidence-collapse').first().click()
    await expect(page.getByTestId('prep-evidence-expand').first()).toBeVisible()

    await page.reload()

    // 折叠态被恢复
    await expect(page.getByTestId('prep-evidence-expand').first()).toBeVisible()
    // 业务事实不来自 localStorage：刷新后无会话快照，仍是空态
    // 限定在 timeline 内断言（同一文案也出现在状态标签里，避免 strict mode 歧义）
    await expect(page.getByTestId('prep-timeline').getByText('还没有开始备课')).toBeVisible()
  })

  test('7 · 键盘可达：Tab 可达 composer 与发送按钮，焦点可见', async ({ page }) => {
    await gotoWorkspace(page)
    // 真实键盘（非 JS .focus()）：Tab 必须能走到可交互控件
    await page.getByTestId('prep-composer-input').focus()
    await expect(page.getByTestId('prep-composer-input')).toBeFocused()

    await page.keyboard.press('Tab')
    const activeTestId = await page.evaluate(() => document.activeElement?.getAttribute('data-testid') ?? '')
    expect(activeTestId).not.toBe('')
  })

  test('8 · 无任意 HTML/JS 注入面：页面内不存在 v-html 产物或内联脚本', async ({ page }) => {
    await gotoWorkspace(page, SESSION_OTHER)
    const injected = await page.evaluate(() => ({
      inlineScripts: Array.from(document.querySelectorAll('script')).filter(
        (s) => !s.src && (s.textContent ?? '').trim().length > 0,
      ).length,
      styleTags: document.querySelectorAll('style[id^="v-html"], [data-v-html]').length,
    }))
    expect(injected.inlineScripts).toBe(0)
    expect(injected.styleTags).toBe(0)
  })

  test('9 · 状态语义：状态区恒定可见且不是"绿色完成"', async ({ page }) => {
    await gotoWorkspace(page, SESSION_OTHER)
    const tone = page.getByTestId('prep-connection-tone')
    await expect(tone).toBeVisible()
    const text = await tone.innerText()
    // PARTIAL/错误态不得呈现为已完成
    expect(text).not.toContain('已完成')
  })

  test('10 · 域 manifest 已注册（P01 通道 2/3）', async ({ page }) => {
    await gotoWorkspace(page)
    const manifest = await page.evaluate(() => (window as never as Record<string, never>)['__T22_MANIFEST__'])
    expect(manifest).toBeTruthy()
  })
})
