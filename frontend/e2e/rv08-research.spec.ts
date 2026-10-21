import { test, expect } from '@playwright/test'
import { appendFileSync, writeFileSync } from 'node:fs'

const viewports = [{ name: '1280x800', width: 1280, height: 800 }, { name: '1440x900', width: 1440, height: 900 }, { name: '1920x1080', width: 1920, height: 1080 }]

for (const viewport of viewports) {
  test(`RV08 contract harness ${viewport.name}`, async ({ page }) => {
    const consoleErrors: string[] = []
    const requestFailures: string[] = []
    page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()) })
    page.on('requestfailed', (request) => { requestFailures.push(`${request.method()} ${request.url()} — ${request.failure()?.errorText ?? 'unknown'}`) })
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.goto('/src/features/research-vnext/__harness__/index.html')
    await expect(page.getByTestId('rv08-research-surface')).toBeVisible()
    await expect(page.getByTestId('rv08-capability-disabled')).toBeVisible()
    await expect(page.getByTestId('rv08-open-evidence')).toBeDisabled()
    await expect(page.getByTestId('rv08-open-artifact')).toBeDisabled()
    await page.screenshot({ path: `D:/ppt-rescue-control/20260912-sol-luna-frontend/RV08/screenshots/rv08-empty-${viewport.name}.png`, fullPage: true })
    const report = [`${viewport.name}`, `consoleErrors=${consoleErrors.length}`, ...consoleErrors, `requestFailures=${requestFailures.length}`, ...requestFailures, ''].join('\n')
    if (viewport.name === '1280x800') writeFileSync('D:/ppt-rescue-control/20260912-sol-luna-frontend/RV08/RV08-console-network.txt', report)
    else appendFileSync('D:/ppt-rescue-control/20260912-sol-luna-frontend/RV08/RV08-console-network.txt', report)
  })
}
