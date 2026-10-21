# B5 · G9 性能压测 harness（tests/perf/）

> 压测环境 = docker compose 全栈真实容器（Valkey/PG/MinIO/sidecar 全真实，**禁进程内模拟**，红线 5）。
> SLO 契约表：`03-ACCEPTANCE §5`（P1–P10）——超预算 = Gate 不通过，不得以已知债务豁免。
> 原始数据归档：`acceptance/perf/`（按 Gate 命名，如 `m2b-p3-20260910.csv`）。

## 前置

```bash
# 依赖（一次性，进 dev group 由 B3 合并 pyproject；临时可 pip install 进 .venv）
uv pip install locust python-pptx pywin32   # pywin32 仅 Windows COM 版需要
# 全栈
docker compose up -d   # teacher-api:8100 / teacher-postgres:5433 / teacher-valkey:6380 / teacher-minio:9002
```

## SLO → 脚本映射

| SLO | 脚本 | 跑法 | 首测 Gate |
|---|---|---|---|
| P1 首 token ≤2s P50/≤4s P95 | `locustfile_teacher.py` butler task + 跑后 `ai_runs.latency_ms` SQL 交叉 | `LOCUST_TASKS=butler locust -f tests/perf/locustfile_teacher.py -u 10 -r 2 -t 3m` | M2-C |
| P2 语音公式 ≤3s P95 | locust voice-formula task（M3 加） | 同上 `LOCUST_TASKS=voice` | M3 |
| P3 课件 30 页 ≤90s / slide 间隔 ≤5s / **P3-O 感知速度（观测项必记录）** | SSE 时间戳分析（M2-B 起随 deck 生成压测脚本） | E2E/locust 均可 | M2-B |
| P5 题库 5000 题 ≤500ms P95 | `locustfile_teacher.py` quiz task | `LOCUST_TASKS=rest locust ... -u 20 -t 3m` | M2-B |
| P6 课堂 100 学生（提交 ≤500ms P95、统计 ≤2s、零崩溃、内存 <1.5GB/worker） | `classroom_sim.py`（bootstrap → locust → teardown） | 见文件头 | M4（M2-A 冒烟） |
| P7 join ≤800ms P95 | `classroom_sim.py` [P7] 计时 | 同上 | M2-A |
| P8 50 SSE 长连 10min（零崩溃、内存 <1.5GB/worker） | `locustfile_teacher.py` SseHoldUser | `LOCUST_TASKS=sse LOCUST_CLASSROOM_SESSION_ID=<sid> locust -u 50 -t 10m` | M4 |
| P9 断线恢复零丢失 | `classroom_sim.py` [P9] reconnect + Playwright setOffline 演练 | M2-A 起随 L9 走查 | M2-A |
| P10 worker 崩溃恢复 ≤60s | 故障注入脚本（M4：`docker kill teacher-worker` → 任务不丢） | M4 专项 | M4 |

## 取数口径（B5 §2.5 定案）

- **P1 双源交叉**：客户端 SSE 首 token 墙钟（本 harness）+ `ai_runs.latency_ms`（B1 台账）。差异 >30% 以 ai_runs 为准并标注。
- **P8 零崩溃判据**：locust 全程无 5xx 且 `docker compose ps` 容器无重启；内存用 `docker stats --no-stream` 采样入档。
- P3-O 感知速度是**观测项**：必记录（outline 首现 ≤10s / 首张 slide ≤15s），不触发 G9 FAIL（R0 可据此开 DEF-PRODUCT）。

## Render Smoke（M2-B 生死 Gate 配套）

```bash
python tests/perf/render_smoke.py <导出的 deck.pptx> --com
# 输出逐页 PNG → acceptance/screenshots/m2b-cruel/；XML 契约断言（OMML/字号≥20pt/无空占位）
# CI 无 Office 走 XML 兜底，但 Gate 验收必须 Windows 真机 COM 版
```
