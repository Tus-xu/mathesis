#!/usr/bin/env node
/**
 * katex_check.mjs —— 题库暂存 JSONL 的 KaTeX 全量编译校验（B2 §5.1 质检）。
 *
 * 用法：node corpus/tools/katex_check.mjs <staging.jsonl> [report.json]
 * KaTeX 来源：frontend/node_modules/katex（前端既有 MIT 依赖；B4 katex-validator
 * sidecar 就绪后切换为调用 8105 服务，本脚本保留为管线离线质检工具）。
 * 可用环境变量 KATEX_DIR 覆盖（node_modules 不随仓库分发，需先 pnpm install）。
 *
 * 输出 report：{ total, records_with_math, segments_checked, failed_segments,
 *              failed_records, ok, failures[] }——题库 LaTeX 错误率要求 = 0。
 */
import { createRequire } from "node:module";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");

const require = createRequire(import.meta.url);
// 本文件位于 services/teacher-platform/corpus/tools/，上溯 4 级到仓库根。
const KATEX_DIR = process.env.KATEX_DIR || resolve(__dirname, "..", "..", "..", "..", "frontend", "node_modules", "katex");
const katex = require(KATEX_DIR);

const [inputArg, outputArg] = process.argv.slice(2);
if (!inputArg) {
  console.error("usage: node katex_check.mjs <staging.jsonl> [report.json]");
  process.exit(2);
}

const DISPLAY = /\$\$([\s\S]+?)\$\$/g;
const INLINE = /(?<![\\$])\$([^$\n]+?)\$(?!\$)/g;

function validateSegment(seg, where, failures) {
  const latex = seg.trim();
  if (!latex) return { ok: true, checked: 0 };
  try {
    katex.renderToString(latex, { throwOnError: true, displayMode: false, strict: false });
    return { ok: true, checked: 1 };
  } catch (e) {
    failures.push({ where, latex: latex.slice(0, 160), error: String(e.message || e).slice(0, 240) });
    return { ok: false, checked: 1 };
  }
}

function checkRecord(rec, failures) {
  const fields = [
    ["stem_latex", rec.stem_latex],
    ["options", rec.options ? Object.values(rec.options).join("\n") : null],
    ["answer", rec.answer],
    ["analysis", rec.analysis],
  ];
  let checked = 0, ok = true, hasMath = false;
  for (const [name, value] of fields) {
    if (!value) continue;
    for (const m of value.matchAll(DISPLAY)) {
      hasMath = true;
      const r = validateSegment(m[1], `${rec.stem_hash}:${name}`, failures);
      checked += r.checked; ok = ok && r.ok;
    }
    const withoutDisplay = value.replace(DISPLAY, " ");
    for (const m of withoutDisplay.matchAll(INLINE)) {
      hasMath = true;
      const r = validateSegment(m[1], `${rec.stem_hash}:${name}`, failures);
      checked += r.checked; ok = ok && r.ok;
    }
  }
  return { checked, ok, hasMath };
}

const lines = readFileSync(resolve(inputArg), "utf-8").split("\n").filter(Boolean);
const failures = [];
let total = 0, withMath = 0, segments = 0, failedRecords = 0;

for (const line of lines) {
  let rec;
  try { rec = JSON.parse(line); } catch { failures.push({ where: "?", error: "JSONL 解析失败" }); continue; }
  total += 1;
  const r = checkRecord(rec, failures);
  segments += r.checked;
  if (r.hasMath) withMath += 1;
  if (!r.ok) failedRecords += 1;
}

const report = {
  generated_at: new Date().toISOString(),
  input: resolve(inputArg),
  katex_from: KATEX_DIR,
  total, records_with_math: withMath, segments_checked: segments,
  failed_segments: failures.length, failed_records: failedRecords,
  ok: failures.length === 0,
  failures: failures.slice(0, 200),
};
const out = outputArg || resolve(inputArg, "../quiz_gaokao_katex_report.json");
writeFileSync(out, JSON.stringify(report, null, 2), "utf-8");
console.log(JSON.stringify({ ...report, failures: undefined,
  failures_preview: failures.slice(0, 5) }, null, 2));
process.exit(report.ok ? 0 : 1);
