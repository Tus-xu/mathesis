import { test, expect } from '@playwright/test'
import { appendFileSync, writeFileSync } from 'node:fs'

const viewports = [{ name: '1280x800', width: 1280, height: 800 }, { name: '1440x900', width: 1440, height: 900 }, { name: '1920x1080', width: 1920, height: 1080 }]
for (const viewport of viewports) {
  test(`RV07 reader/evidence contract harness ${viewport.name}`, async ({ page }) => {
    const errors: string[] = []; const failures: string[] = []
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
    page.on('requestfailed', (request) => { failures.push(`${request.method()} ${request.url()} — ${request.failure()?.errorText ?? 'unknown'}`) })
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.goto('/src/features/research-vnext/__harness__/index.html')
    await expect(page.getByTestId('rv08-research-surface')).toBeVisible()
    await expect(page.getByTestId('rv08-capability-disabled')).toBeVisible()
    await page.screenshot({ path: `D:/ppt-rescue-control/20260912-sol-luna-frontend/RV07/screenshots/rv07-shell-${viewport.name}.png`, fullPage: true })
    const report = [`${viewport.name}`, `consoleErrors=${errors.length}`, ...errors, `requestFailures=${failures.length}`, ...failures, ''].join('\n')
    if (viewport.name === '1280x800') writeFileSync('D:/ppt-rescue-control/20260912-sol-luna-frontend/RV07/RV07-console-network.txt', report)
    else appendFileSync('D:/ppt-rescue-control/20260912-sol-luna-frontend/RV07/RV07-console-network.txt', report)
  })
}
