import { expect, test } from '@playwright/test'

const TEACHER_USER = { nickname: '王老师', roles: [{ role: 'teacher' }], active_role: 'teacher', grade: '' }

async function presetMock(page: import('@playwright/test').Page) {
  await page.addInitScript((user) => {
    localStorage.setItem('ma_token', 'mock-token-teacher-preview')
    localStorage.setItem('ma_user', JSON.stringify(user))
  }, TEACHER_USER)
  await page.request.post('/api/_mock/teacher/reset', { headers: { Authorization: 'Bearer mock-token-teacher-preview' } })
}

test.describe('Teacher Grading V2 reconstruction (mock)', () => {
  test('grades a derivative submission in a question-focused workstation', async ({ page }) => {
    await presetMock(page)
    await page.goto('/teacher/grading?submission_item_id=si-2')

    await expect(page.getByRole('heading', { name: '函数的单调性', exact: true })).toBeVisible()
    await expect(page.getByText('第 2 份作答')).toBeVisible()
    await expect(page.getByText('正确求导')).toBeVisible()
    await expect(page.getByRole('button', { name: '确认并下一份', exact: true })).toBeVisible()
    await expect(page.locator('select[data-legacy-student-select]')).toHaveCount(0)
    await page.setViewportSize({ width: 1366, height: 768 })
    await page.screenshot({ path: 'artifacts/teacher-v2/grading/v2-ready-1366x768.png', fullPage: true })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.screenshot({ path: 'artifacts/teacher-v2/grading/v2-ready-1440x900.png', fullPage: true })
  })

  test('review survives refresh and an explicit override advances via server navigation', async ({ page }) => {
    await presetMock(page)
    await page.goto('/teacher/grading?submission_item_id=si-1')
    await page.getByRole('button', { name: /稍后复看/ }).click()
    await expect(page.getByText('已标记为稍后复看')).toBeVisible()
    await page.reload()
    await expect(page.getByRole('button', { name: '第 1 份作答 人工复看', exact: true })).toBeVisible()
    await page.screenshot({ path: 'artifacts/teacher-v2/grading/v2-review-refresh.png', fullPage: true })
    await page.getByRole('button', { name: '教师明确给分' }).click()
    await page.getByLabel('最终得分').fill('7')
    await page.getByRole('button', { name: '确认并下一份', exact: true }).click()
    await expect(page).not.toHaveURL(/submission_item_id=si-1/)
  })

  test('shows a recoverable source-file failure without inventing a preview', async ({ page }) => {
    await presetMock(page)
    await page.goto('/teacher/grading?submission_item_id=si-4')
    await expect(page.getByText('原始文件暂时不可用，可重试或标记复看。')).toBeVisible()
    await expect(page.getByRole('button', { name: '重试加载' })).toBeVisible()
    await expect(page.getByAltText('学生原始作答文件')).toHaveCount(0)
    await page.locator('[data-grading-region="work"]').evaluate((element) => { element.scrollTop = element.scrollHeight })
    await page.screenshot({ path: 'artifacts/teacher-v2/grading/v2-file-error.png', fullPage: true })
  })
})
