# 角色可选短信认证验证报告

- 验证日期：2026-08-25
- 后端分支：`codex/unified-auth-backend`
- 前端分支：`codex/unified-auth-frontend`
- 结论：`2026-08-24-role-selective-sms-authentication.md` 计划的 Task 1–7 已全部完成并通过本地自动化验证；学生、教师、科研人员三类身份均可在注册与登录时显式选择目标身份，未审核的专业身份不再被静默重定向到学生端。

## 1. 实施提交

### 后端（`codex/unified-auth-backend`）

| 提交 | 内容 |
| --- | --- |
| `9f113bf` | 角色可选短信认证实施计划 |
| `7236560` | AuthSession 持久化 pending_role（Task 1） |
| `eb257d2` | 校验 pending 角色意图 |
| `56e6326` | `/auth/register/sms` 三类角色 purpose=registration 注册（Task 2） |
| `13dec2b` | 已批准角色禁止重复走注册通道 |
| `f2da17e` | 短信/密码登录按已批准目标角色签发会话（Task 3） |
| `85f625f` | 未申请角色的登录选择返回 AUTH_ROLE_NOT_AVAILABLE |
| `38d26a6` | 腾讯云 SMS 适配器安全接线 + 旧教师注册接口 410 退役（Task 4） |

### 前端（`codex/unified-auth-frontend`）

| 提交 | 内容 |
| --- | --- |
| `98a9b0d` | 客户端状态保留服务器返回的身份意图（Task 5） |
| `6f7009c` / `7f0eb10` | mock 待审状态跨刷新保留 |
| `4db64c5` | 收尾修复：suspended mock 状态不再回退为 approved |
| `2d9a727` | 登录/注册页角色选择 UI + 响应导向导航 + 浏览器验收（Task 6/7） |

## 2. 新鲜验证结果（2026-08-25）

| 验证项 | 结果 |
| --- | --- |
| `pytest -q tests/identity`（后端工作树） | 102 passed |
| 计划指定的 8 个 identity 测试文件 | 88 passed |
| `npm test`（前端 Vitest 全量） | 80 passed（14 个文件） |
| `npm run typecheck`（vue-tsc） | 通过，无诊断 |
| `npm run build`（生产构建） | 通过 |
| `npm run e2e:mock -- e2e/auth.spec.ts`（Playwright） | 15 passed |
| `git diff --check`（两仓库） | 干净 |

## 3. 浏览器验收旅程（`e2e/auth.spec.ts`，15 条）

1. 学生短信注册（role=student，registration purpose）→ onboarding → `/overview`，令牌不落 localStorage/sessionStorage。
2. 已登录学生提交教师申请并看到待审状态。
3. 登录页提供学生端/教师端/科研端三选（无 admin 选项）。
4. **已批准教师选择教师端登录 → 直达 `/teacher/today`，不再落到学生 overview。**
5. **已批准科研人员密码登录选择科研端 → 直达 `/research`。**
6. **教师注册（提交学校资料）→ 进入 `/identity/pending` 审核进度页，而非学生端。**
7. 管理员重认证后批准教师申请，教师重新登录进入教师工作台。
8. 管理员批准科研人员申请。
9. rejected / suspended 教师均被拦截到 `/identity/pending`。
10. 双角色学生切换进入教师工作台。
11. 密码重置使用独立 SMS challenge。
12. 账号安全撤销其他设备会话。
13. 双验证码手机号换绑并登出会话。
14. 无会话状态下取消账号注销。

## 4. 收尾修复（本次验证过程中发现）

- **suspended mock 状态回退 bug**：`resolveMockStartupIdentity` 的 `isMockState` 未识别 `suspended`，导致携带 `ma_mock_state=suspended` + 教师 `ma_user` 的启动回退为 `approved`，suspended 教师可进入教师工作台（E2E 用例失败暴露）。已在 `4db64c5` 修复并补充单测。

## 5. 已知限制与后续事项

- 腾讯云真实短信发送需要部署期凭证与已审核签名/模板；自动化验证使用 fake SDK client，生产缺少配置时 fail-closed 拒绝发送（不降级 demo）。
- 后端全仓单进程测试仍有历史遗留的共享测试库 schema / Windows asyncio 隔离级联失败（见 `2026-08-22-unified-authentication-verification.md` 第 5 节），与本轮角色可选认证无关；合并分支前需专项修复全仓测试基础设施。
- 生产 CAPTCHA 供应商仍属部署接线范围。
