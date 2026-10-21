/**
 * 校验 README 中的 mermaid 图能通过官方 mermaid 解析器（防 GitHub 渲染失败）。
 *
 * 依赖（装在受管 node workspace，非仓库依赖）：
 *   cd C:/Users/<user>/.workbuddy/binaries/node/workspace && npm install mermaid jsdom
 *
 * 用法：
 *   NODE_PATH=C:/Users/<user>/.workbuddy/binaries/node/workspace/node_modules \
 *     C:/Users/<user>/.workbuddy/binaries/node/versions/22.22.2-6/node.exe tools/check_readme_mermaid.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "file:///C:/Users/<user>/.workbuddy/binaries/node/workspace/node_modules/jsdom/lib/api.js";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const README = path.join(REPO, "README.md");

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
  pretendToBeVisual: true,
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.DOMParser = dom.window.DOMParser;
Object.defineProperty(globalThis, "navigator", {
  value: dom.window.navigator,
  configurable: true,
});

const mermaid = (
  await import(
    "file:///C:/Users/<user>/.workbuddy/binaries/node/workspace/node_modules/mermaid/dist/mermaid.esm.mjs"
  )
).default;
mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });

const text = fs.readFileSync(README, "utf8");
const blocks = [...text.matchAll(/```mermaid\n([\s\S]*?)```/g)].map((m) => m[1]);

if (blocks.length === 0) {
  console.log("README 中没有 mermaid 图块，跳过");
  process.exit(0);
}

let failed = 0;
for (let i = 0; i < blocks.length; i++) {
  try {
    await mermaid.parse(blocks[i]);
    console.log(`✅ mermaid 块 #${i + 1} 解析通过（${blocks[i].split("\n").length} 行）`);
  } catch (e) {
    console.log(`❌ mermaid 块 #${i + 1} 解析失败: ${e.message}`);
    failed++;
  }
}

process.exit(failed > 0 ? 1 : 0);
