/** Checkpoint 1: actual Vue harness captures, never production mock data. */
import { expect, test } from '@playwright/test'
import { appendFileSync } from 'node:fs'
import { snapshotFor } from '../test/harness/teacher-prep-v2/teacherPrepStates'

const evidenceRoot = process.env.CP1_EVIDENCE_DIR ?? 'D:/ppt-rescue-control/20260912-sol-luna-frontend/01-authority-harness/screenshots'
const viewports = [
  { name: '1280x800', width: 1280, height: 800 },
  { name: '1440x900', width: 1440, height: 900 },
  { name: '1920x1080', width: 1920, height: 1080 },
]
const states = ['empty', 'blueprint', 'confirmation', 'artifact', 'error', 'reconnecting'] as const

test.describe('Checkpoint 1 · actual Teacher Vue route visual harness', () => {
  for (const viewport of viewports) {
    for (const state of states) {
      test(`${state} at ${viewport.name}`, async ({ page }) => {
        test.skip(process.env.E2E_HARNESS !== '1', 'fixture interception is harness-only')
        await page.setViewportSize({ width: viewport.width, height: viewport.height })
        const consoleErrors: string[] = []
        const requestFailures: string[] = []
        page.on('console', (message) => {
          if (message.type() === 'error') consoleErrors.push(message.text())
        })
        page.on('requestfailed', (request) => {
          requestFailures.push(`${request.method()} ${request.url()} :: ${request.failure()?.errorText ?? 'unknown'}`)
        })
        const sessionId = state === 'empty' ? undefined : 'harness-session'
        await page.route('**/api/teacher-v3/prep/conversations/**', async (route) => {
          const request = route.request()
          if (request.url().includes('/events')) {
            if (state === 'reconnecting') {
              await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'harness reconnecting' }) })
            } else {
              await route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ping\n\n' })
            }
            return
          }
          if (state === 'error') {
            await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'harness unavailable' }) })
            return
          }
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(snapshotFor(state)) })
        })
        // Freeze presentation-only entrance transitions so evidence captures the
        // fully rendered state without changing production behavior.
        await page.emulateMedia({ reducedMotion: 'reduce' })
        await page.goto(`/src/features/teacher-prep-v2/__harness__/index.html#/teacher-v3/prep-immersive${sessionId ? `/${sessionId}` : ''}`)
        if (state === 'error') {
          await expect(page.getByTestId('prep-primary-error')).toBeVisible()
        } else {
          await expect(page.getByTestId('prep-conversation-stage')).toBeVisible()
        }
        if (state === 'reconnecting') {
          await page.evaluate(() => {
            const store = (window as Window & { __T22_PREP_STORE__?: { streamState: string } }).__T22_PREP_STORE__
            if (!store) throw new Error('harness store exposure missing')
            store.streamState = 'reconnecting'
          })
          await expect(page.getByTestId('prep-connection-tone')).toContainText(/连接|重连|服务/)
        } else if (sessionId && state !== 'error') {
          // The one-shot harness SSE response closes after its heartbeat. Restore
          // the intended ordinary fixture transport state through the real store;
          // the production stream lifecycle remains unchanged.
          await page.evaluate(() => {
            const store = (window as Window & { __T22_PREP_STORE__?: { streamState: string; gapCount: number } }).__T22_PREP_STORE__
            if (!store) throw new Error('harness store exposure missing')
            store.streamState = 'live'
            store.gapCount = 0
          })
          await expect(page.getByTestId('prep-connection-tone')).toContainText('已连接')
        }
        const safeState = state.replace(/[^a-z0-9-]/gi, '_')
        await page.screenshot({ path: `${evidenceRoot}/${safeState}-${viewport.name}.png`, fullPage: true })
        appendFileSync(
          `${evidenceRoot}/console-network.log`,
          `${safeState}@${viewport.name}\nconsoleErrors=${JSON.stringify(consoleErrors)}\nrequestFailures=${JSON.stringify(requestFailures)}\n`,
        )
      })
    }
  }
})
