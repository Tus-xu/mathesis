# B3 M1 · 端口冲突记录（学生端 API 占用 8100）

> 2026-09-07 · 环境事实，非本工程缺陷

## 发现

`127.0.0.1:8100` 被**学生端 API**占用：

```
C:\python13\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100   (PID 18508)
```

架构（02-ARCHITECTURE §1.3）约定：teacher-api=**8100**、student-api=**8000**（只读事实）。
当前运行实例把学生端显式起在 8100，与教师平台冲突。

## 处置（本工程内）

- `docker-compose.yml` 的 api 主机端口改为可配置：`"${TEACHER_API_PORT:-8100}:8100"`。
- 本地验证使用 `TEACHER_API_PORT=8101`，不杀学生端进程。
- 学生端进程未触碰（非本工程所有权）。

## 建议（提给用户/学生端维护）

- 学生端 dev 启动回到 8000（`uvicorn app.main:app --port 8000`），释放 8100 给教师平台；
- 或前端 vite 代理目标 `VITE_TEACHER_API_PROXY_TARGET` 指向实际可用端口。
- 学生端 Redis 浮动 tag 维护项仍在 02-ARCHITECTURE §1.4 登记（与本问题无关）。
