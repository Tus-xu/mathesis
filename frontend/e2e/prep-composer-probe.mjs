import { chromium } from "@playwright/test"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const base = process.env.E2E_BASE_URL || "http://127.0.0.1:5176"
const STATE = path.resolve(__dirname, "..", "..", ".tmp", "pw_state.json")
const url = base + process.argv[2]
const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({ viewport: { width: 1672, height: 941 }, deviceScaleFactor: 1, locale: "zh-CN", ...(fs.existsSync(STATE) ? { storageState: STATE } : {}) })
const page = await ctx.newPage()
await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 })
await page.waitForTimeout(Number(process.argv[3] || 6000))
const out = await page.evaluate(() => {
  const roots = [...document.querySelectorAll(".pc-composer")]
  const tas = [...document.querySelectorAll("textarea")]
  const deckAi = [...document.querySelectorAll("[class*=\"pc-deck__ai\"]")]
  return {
    composerRoots: roots.length,
    textareas: tas.length,
    textareaPlaceholders: tas.map((t) => t.placeholder),
    deckInlineAiNodes: deckAi.length,
    pagerText: document.querySelector(".pc-deck__pager-text")?.textContent?.trim() || null,
    zoomText: document.querySelector(".pc-deck__zoom-val")?.textContent?.trim() || null,
    toolbar: [...document.querySelectorAll(".pc-deck__tool")].map((b) => (b.textContent || "").trim()),
    canvasHeader: document.querySelector(".pc-slide__header-title")?.textContent?.trim() || null,
    slideTitle: document.querySelector(".pc-slide__title")?.textContent?.trim() || null,
    thumbTitles: [...document.querySelectorAll(".pc-deck__item-title")].map((e) => (e.textContent || "").trim()),
    emojiInToolbar: /[\u{1F000}-\u{1FAFF}\u{2B00}-\u{2BFF}\u{FE0F}]/u.test(document.querySelector(".pc-deck__toolbar")?.innerText || ""),
  }
})
console.log(JSON.stringify(out, null, 1))
await browser.close()
