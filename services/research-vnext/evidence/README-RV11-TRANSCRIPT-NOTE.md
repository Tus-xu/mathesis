# 说明（RV11 复核时补记，2026-09-14 11:0x）

本目录曾存在 `RV11-E2E-transcript.json`（FAIL 1/12）——那是 RV11 首次 E2E 探针跑：

- 它用脚本默认 `--base-url http://127.0.0.1:8300`，而 8300 被另一会话的旧进程占用，
  网关密钥不一致 ⇒ 全部 401 ⇒ 1 步失败 + 10 步 SKIPPED，verdict=FAIL。
- 它用脚本默认 `--out`（服务目录 evidence/），因此与交付目录
  `D:/ppt-rescue-control/20260911-math-ai-platform-vnext/RV11/RV11-E2E-transcript.json`（PASS 12/12）
  **同名但内容无关**。

已改名为 `RV11-E2E-transcript-INVALID-first-attempt-stale-8300.json` 留档。
权威 transcript 只认交付目录那份（sha256 见 RV11/VERIFY.md）。
