import { expect, test } from '@playwright/test'
import { appendFileSync } from 'node:fs'
import { snapshotFor } from '../test/harness/teacher-prep-v2/teacherPrepStates'

const root = process.env.T23_EVIDENCE_DIR ?? 'D:/ppt-rescue-control/20260912-sol-luna-frontend/T23/screenshots'
for (const viewport of [{ name: '1280x800', width: 1280, height: 800 }, { name: '1440x900', width: 1440, height: 900 }, { name: '1920x1080', width: 1920, height: 1080 }]) {
  test(`T23 review artifacts at ${viewport.name}`, async ({ page }) => {
    test.skip(process.env.E2E_HARNESS !== '1', 'sanitized fixture harness only')
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.route('**/api/teacher-v3/prep/conversations/**', async (route) => {
      if (route.request().url().includes('/events')) return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ping\n\n' })
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(snapshotFor('t23-review')) })
    })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/src/features/teacher-prep-v2/__harness__/index.html#/teacher-v3/prep-immersive/harness-session')
    await expect(page.getByTestId('prep-conversation-stage')).toBeVisible()
    await page.evaluate(() => {
      const store = (window as Window & { __T22_PREP_STORE__?: { streamState: string; gapCount: number } }).__T22_PREP_STORE__
      if (!store) throw new Error('harness store exposure missing')
      store.streamState = 'live'
      store.gapCount = 0
    })
    await expect(page.getByTestId('prep-connection-tone')).toContainText('已连接')
    await expect(page.getByTestId('t23-direct-chunk-locator')).toBeVisible()
    await expect(page.getByTestId('t23-diagnostic-proposal')).toBeVisible()
    await expect(page.getByTestId('t23-learning-profile')).toBeVisible()
    await expect(page.getByTestId('t23-blueprint-revision')).toBeVisible()
    await expect(page.getByTestId('t23-run-context')).toBeVisible()
    await page.screenshot({ path: `${root}/t23-review-${viewport.name}.png`, fullPage: true })
    appendFileSync(`${root}/console-network.log`, `t23-review@${viewport.name}\n`)
  })
}
