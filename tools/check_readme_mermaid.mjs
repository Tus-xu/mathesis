/**
 * 校验 README 中的 mermaid 图能通过官方 mermaid 解析器（防 GitHub 渲染失败）。
 *
 * 依赖（任意目录，mermaid + jsdom 装进同一个 node_modules）：
 *   npm install mermaid jsdom
 *
 * 用法：
 *   MERMAID_NODE_MODULES=/path/to/node_modules node tools/check_readme_mermaid.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const NM = process.env.MERMAID_NODE_MODULES;
if (!NM) {
  console.error("请设置 MERMAID_NODE_MODULES 指向已安装 mermaid + jsdom 的 node_modules 目录");
  process.exit(2);
}

const { JSDOM } = await import(
  pathToFileURL(path.join(NM, "jsdom/lib/api.js")).href
);

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
  await import(pathToFileURL(path.join(NM, "mermaid/dist/mermaid.esm.mjs")).href)
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
