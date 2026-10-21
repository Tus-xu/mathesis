---
feature: student-chain-fix
status: delivered
updated: 2026-09-14
branch: master (main worktree, uncommitted — 等待人工验收后提交)
commits: (待验收提交)
---

# 学生端对话学习修复优化轮

## Report

**What was built** — 六项核心修复使引导式解题链路从"间歇降级/粘连断裂/走错模型"变为全链稳定达标：①`GUIDED_SOLVE_MODEL` 场景路由真正生效（chat/chat_stream 双路 + 变式生成并入 80B）；②教材检索 source_kind 三路过滤 + 引用回链册/章/节（ScoredChunk.meta 透出、citation kind 徽标）；③判答粘连 P0 修复（judge 选项卡纪律 + new_problem 硬闸，杜绝点选项被当新题弃会话）；④断流保护（plan/判答/降级三持久化点显式 commit）；⑤自由文本引导请求 L0 确定性路由（真机实测曾掉自由对话直出答案）；⑥选项 `\frac` 转义腐蚀修复 + 悬浮球遮挡发送按钮修复 + 存量测试基建修复（gin_trgm_ops）。

**Verification** — API 直驱全链注入：q1 四棱锥图片题（OCR 2.6s → 首屏 22.4s → 全链 108.1s，2 道变式）；q2 复杂立体几何（首屏 47.5s → 全链 103.2s，3/3 变式过闸）；两题零 socratic_degraded 零答案泄露。浏览器 Playwright E2E 6 步全过（专用引导框 .soc-choices 实证、坏味道 0 命中）。后端相关测试 210/210；前端 881/883（与基线一致）；后端全量 1686 过/16 败全部归因存量。证据：`deliverables/student-chain-fix/`（FINAL-GATE.md + timings ×2 + 截图 14 张）。

**Journey log** —
1. API 注入脚本在首个卡片处断开 SSE 暴露了真实缺陷：未提交的 TutorSession 随请求回滚丢失——测试工具的"不真实"反而是缺陷探测器，修了服务端断流保护。
2. judge 把"思路选项描述"误判 new_problem 的根因是 prompt 只定义了"新题"却没定义"选项作答"——LLM 判定类 bug 的修复要 prompt 纪律 + 代码硬闸双保险，且硬闸要区分 regenerate 测试锁定的路径。
3. `\frac` 腐蚀的排查走了弯路：smart_quiz 已有 v1.11 防护让人以为链路安全，实际 socratic 的 _parse_step_choices 是独立解析点——同一仓库内同一问题的修复必须 grep 所有解析点。
4. 全量测试 41 失败的元凶是 research.py 索引缺 gin_trgm_ops 挡住 create_all——一个基建 bug 会伪装成大面积功能回归，先看 conftest 初始化日志再归因。

## 共享阻塞（仅记录）

- alembic 双头（迁移收口属教师端轮次）；侧栏会话标题泄露思考片段；判分链 test_judge_grading 存量回归。

## Tasks

- [x] T6: 起服预检 — acceptance: 8000/5176 在线、登录、题图资产 (covers: Report)
- [x] T7: GUIDED_SOLVE_MODEL 场景路由 — acceptance: ai_calls 实证 guided/80B (covers: Report)
- [x] T8: source_kind 过滤+引用回链 — acceptance: 检索脚本 PASS + citation 带册/章/节 (covers: Report)
- [x] T9: 前端引导链路验证修复 — acceptance: 专用卡实证 + 三缺陷修复 (covers: Report)
- [x] T10: 注入测试×2 — acceptance: 两题全链计时达标、零降级 (covers: Report)
- [x] T11: FINAL-GATE — acceptance: 测试/截图/对照报告齐备 (covers: Report)
