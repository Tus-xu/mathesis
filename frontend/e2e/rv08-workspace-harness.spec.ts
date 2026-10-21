import { test, expect } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const OUT = 'D:/ppt-rescue-control/20260911-math-ai-platform-vnext/RV08'

test.describe('RV08 workspace harness visuals', () => {
  for (const viewport of [
    { name: '1440x900', width: 1440, height: 900 },
    { name: '1920x1080', width: 1920, height: 1080 },
  ]) {
    test(`workspace harness ${viewport.name}`, async ({ page }) => {
      const consoleErrors: string[] = []
      page.on('console', (m) => {
        if (m.type() === 'error') consoleErrors.push(m.text())
      })
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await page.goto('/src/features/research-vnext/__harness__/index.html')
      await expect(page.getByTestId('rv08-research-workspace')).toBeVisible({ timeout: 15000 })
      await expect(page.getByTestId('ma-region-left')).toBeVisible()
      await expect(page.getByTestId('ma-region-center')).toBeVisible()
      await expect(page.getByTestId('ma-region-status')).toBeVisible()
      await expect(page.getByTestId('rv08-composer')).toBeVisible()
      await expect(page.getByTestId('rv08-stream-status')).toBeVisible()
      await expect(page.getByTestId('rv08-artifact-canvas')).toBeVisible()
      mkdirSync(`${OUT}/screenshots`, { recursive: true })
      await page.screenshot({
        path: `${OUT}/screenshots/rv08-workspace-${viewport.name}.png`,
        fullPage: true,
      })
      mkdirSync(`${OUT}/evidence`, { recursive: true })
      const report = `${viewport.name} consoleErrors=${consoleErrors.length}\n${consoleErrors.join('\n')}\n`
      const { appendFileSync } = await import('node:fs')
      appendFileSync(`${OUT}/evidence/e2e-harness-console.log`, report)
      expect(consoleErrors.filter((e) => !e.includes('favicon'))).toEqual([])
    })
  }
})
