# 统一登录注册系统实施验证报告

- 验证日期：2026-08-22
- 后端分支：`codex/unified-auth-backend`
- 前端分支：`codex/unified-auth-frontend`
- 结论：计划内的身份模型、登录注册、角色审核、会话安全、账号生命周期、前端恢复路径及认证浏览器验收均已实现并通过本地自动化验证。

## 1. 实施提交

### 后端与部署

| 提交 | 内容 |
| --- | --- |
| `c4d0366` | 统一身份数据模型与 Alembic 迁移 |
| `f1476b8` | 基于数据库批准状态的五重授权校验 |
| `0bfa616` | 短信挑战、purpose 隔离、限流与 provider 边界 |
| `d35bd77` | Argon2id 密码登录、设置和重置 |
| `e63e250` | Refresh token 轮换、重放撤销与会话管理 |
| `42b59a9` | 短信登录、学生原子注册与 onboarding |
| `fd3885a` | 教师/科研人员申请及管理员审核 |
| `52bad2e` | 手机换绑、账号注销、审计保留与 break-glass |
| `cbd6f89` | CSRF、服务端管理员重认证、部署配置与跨栈安全收尾 |
| `11b5935` | 注销敏感操作改用服务端会话重认证时间 |
| `a125b77` | 兼容登录适配到可撤销会话，同时保留旧 `data.token` 响应 |
| `879ad0e` | 兼容登录 active_role 改为确定性选择，消除执行计划漂移 |
| `655626b` | 旧 M1/M2 测试套件对齐统一授权（显式角色切换 + approved 绑定） |
| `405f01e` | conftest sessionstart 加固：建表重试 + 表数量核对告警 |

### 前端

| 提交 | 内容 |
| --- | --- |
| `796860b` | 内存 Access token、单飞刷新和冷启动 bootstrap |
| `8a243d2` | 短信/密码登录、注册、学生 onboarding、角色申请 |
| `db919b0` | 路由守卫、账号安全、管理员审核与 CSP |
| `eb7e39a` | 跨栈认证 mock 与首批浏览器验收 |
| `f516453` | 密码恢复、双验证码换绑、会话撤销、注销取消及完整 E2E |

## 2. 规格映射与自动化证据

| 设计域 | 已验证行为 | 主要证据 |
| --- | --- | --- |
| 身份与迁移 | 四态角色绑定、`security_version`、会话/凭据/审计表、旧 researcher 默认降为 pending、种子白名单保留 approved | `test_identity_models.py`、`test_identity_migration.py` |
| 授权 | 账号状态、会话撤销、security version、active role、approved binding 五重校验；pending/suspended/revoked 拒绝 | `test_authorization.py`、M3 scope 回归 |
| 短信挑战 | 单次消费、purpose 隔离、5 次失败锁定、过期、手机号限流、真实 Redis 并发双提交仅一次成功 | `test_sms_challenges.py` |
| 密码 | NIST 风格长度边界、弱密码阻断、Unicode/空格、Argon2id、重置后撤销会话、未知手机号通用响应 | `test_passwords.py` |
| 会话 | 15 分钟 Access token、哈希 Refresh token、轮换、family 重放撤销、并发刷新、idle/absolute timeout、CSRF | `test_sessions.py` |
| 学生注册 | 首次短信登录原子创建 student，重复/并发不产生重复用户，完成 profile 与 consent | `test_login_onboarding.py` |
| 专业身份 | 教师/科研申请 pending、禁止自审、管理员近期重认证、批准/驳回/补材料 | `test_role_applications.py`、`test_identity_admin.py` |
| 账号生命周期 | 双验证码换绑、会话撤销、7 天注销冷静期、取消、到期匿名化、手机号可重新注册 | `test_account_lifecycle.py` |
| 运维治理 | 180 天以上审计保留、双人 break-glass 开关、生产占位 pepper/demo provider 拒绝 | `test_break_glass.py`、`test_auth_config.py` |
| 前端安全 | Access token 仅内存、401 单飞刷新、CSRF header、角色守卫、恢复页面、CSP | Vitest auth suites、源码扫描、`nginx.conf` |
| 浏览器验收 | 学生注册/onboarding、教师申请、管理员审核与教师重新登录、科研审核、rejected/suspended 拒绝、角色切换、密码重置、会话撤销、手机号换绑、注销取消 | `e2e/auth.spec.ts` 12/12 |

## 3. 新鲜验证结果

| 验证项 | 结果 |
| --- | --- |
| `pytest tests/identity -q` | 72 passed |
| identity + 完整 `test_api_integration.py` 联合回归 | 91 passed，2 skipped |
| 核心 auth/admin/classroom 回归 | 46 passed |
| 15 个 M3 文件逐文件独立进程运行 | 82 passed |
| Alembic upgrade/downgrade/upgrade | 1 passed |
| Ruff（全部本次后端改动及 identity tests） | passed |
| `pip check` | No broken requirements found |
| 前端 `npm test` | 68 passed |
| 前端 `npm run typecheck` | passed |
| 前端 `npm run build` | passed；仅保留既有 router 静态/动态导入分包警告 |
| Playwright `e2e/auth.spec.ts` | 12 passed |
| `src` token 持久化扫描 | 无 `ma_token`、localStorage/sessionStorage token 读写 |
| CSP 静态检查 | Report-Only 与 enforced policy 均包含 `object-src 'none'`、`base-uri 'self'`、`frame-ancestors 'none'` |

全仓单进程复跑（含全部 M3 文件在同一进程）已通过：`1386 passed, 8 skipped`，M3 逐文件隔离运行不再是必需。此前"M3 必须逐文件独立进程"的结论源于共享测试库 schema 受损与连接池跨循环关闭噪声的叠加，根因与修复见下方"全仓测试诊断（已闭环）"。

### 全仓测试诊断（已闭环）

首轮全仓 `pytest -q` 的 `1246 passed, 11 skipped, 100 failed, 37 errors` 在干净环境（重启 PG/Redis 容器、sessionstart 全量重建 schema）单进程复现后收敛为 12 个稳定失败，定位出两类叠加问题：

1. **真实授权回归（已修复，`879ad0e` + `655626b`）**
   - 旧 M1/M2 测试以"裸用户（无 role_bindings 行）+ 旧式 JWT"访问业务路由，被五重校验的 approved-binding 检查拒绝（403 `AUTH_ROLE_NOT_APPROVED`）。修复：受影响的 6 个测试文件在造用户时补建 approved student 绑定。
   - 兼容登录 `/api/auth/login` 的 `active_role = role_names[0]` 取自无 ORDER BY 查询，随 Postgres 执行计划漂移：小表走唯一索引序（admin 字典序在前）→ 单文件测试通过；长会话大表走堆序（student 先插入）→ admin 用例全仓 403。这正是"单文件绿、全仓红"假象的来源。修复：兼容登录改为与新登录（`identity/router.login_password`）一致的确定性选择（last_active_role → student → 首个已批准角色）；admin 白名单引导用例显式调用 `/api/auth/role/switch` 切换后访问 admin 端点。
2. **环境级联（已加固，`405f01e`）**
   - 原报告的"共享 schema 缺失（`mastery_records`、`users.phone_verified_at`）级联 + asyncio/asyncpg 跨循环关闭异常"在干净库 + 单进程复跑中不再复现，判断为当时共享 `test_math_arena` 被污染（建表静默失败）或与其他 pytest 进程并发共用所致。conftest sessionstart 已加固：建表失败自动重试一次；建表后核对 public schema 表数量（当前 63/63），不符时输出醒目告警并列出排查方向，杜绝静默失败再次级联。
   - Windows 上 asyncpg 连接池回收"已死循环连接"产生的 `Exception closing connection` ERROR 日志为噪声（proactor 已关闭），不影响测试结果，本轮全仓运行中未造成任何失败。

全仓单进程复跑最终结果：`1386 passed, 8 skipped, 0 failed, 0 errors`（6 分 40 秒），满足完成分支门禁，`codex/unified-auth-backend` 可进入合并流程。遗留事项（非本分支范围，CI 专项处理）：`.github/workflows/ci.yml` backend-test 的数据库端口接线（conftest 固定 54329，CI service 映射 5432）；全仓既有 Ruff 债务约 95 处（与本分支改动无关，本分支改动文件 Ruff 全过）。

## 4. 迁移数据核对

迁移测试在全新 PostgreSQL 数据库先升级到 `m3_002_fullstack_closure`，实际插入以下数据，再升级到 `auth_001_unified_identity`：

| 数据 | 升级前 | 升级后 | downgrade 后仍保留 |
| --- | ---: | ---: | ---: |
| users | 4 | 4 | 4 |
| role_bindings | 4 | 4 | 4 |
| classes | 1 | 1 | 1 |
| class_members | 1 | 1 | 1 |
| events（学习行为） | 1 | 1 | 1 |

角色结果：student approved、teacher approved、普通历史 researcher pending、配置白名单 researcher approved。随后 downgrade 到 `m3_002_fullstack_closure` 并重新 upgrade 到 head 均成功。

生产执行前仍必须先做数据库备份和真实数据数量快照。回滚命令：

```powershell
python -m alembic downgrade m3_002_fullstack_closure
```

回滚会移除新认证会话、Refresh token、申请、同意记录等新表/字段；必须在维护窗口内执行并保留备份。

## 5. 安全探针结果

- OTP 重放、purpose mismatch、过期、锁定、Redis 并发消费：通过。
- Refresh 并发轮换、已使用 token 重放、family 撤销：通过。
- CSRF cookie/header 不一致拒绝；Refresh HttpOnly、CSRF 非 HttpOnly 且 Path `/`：通过。
- pending/suspended 角色、撤销会话、security version 不匹配、token/session active role 不一致：拒绝通过。
- 邀请码最后一次并发兑换：仅一次成功。
- 密码重置对已注册与未注册手机号返回相同成功信封：通过。
- 管理员审核与用户注销均只信任服务端 `AuthSession.reauthenticated_at`；伪造 `X-Reauth-At` 无效。
- 生产启动拒绝 demo 短信、开发/示例 pepper 和不足 32 字符的 pepper：通过。
- Access token 不进入浏览器 localStorage/sessionStorage：单元、E2E 与源码扫描通过。

## 6. 外部限制与上线前动作

以下内容按实施计划明确留在部署集成范围，本报告不声称已完成实网验证：

1. 腾讯云短信领域适配器和稳定错误映射已存在，但真实 SDK sender、签名、模板及生产凭证尚未接线；未接线时返回 `SMS_PROVIDER_UNAVAILABLE`，不会降级到 demo。
2. CAPTCHA 仅提供配置入口，具体生产供应商未实现；SSO、二维码登录和邮件登录不在本次范围。
3. E2E 使用本地 mock provider，不代表腾讯短信送达率或供应商限流验收。
4. CSP 当前仍允许 `style-src 'unsafe-inline'`；脚本、对象、base URI 与 framing 已收紧，后续应在样式迁移完成后移除 inline style 例外。
5. 仓库原有 M3 前端 Playwright 用例含已过期页面文案/选择器；认证验收套件独立 12/12 通过，但旧 M3 UI 套件需在其专项迭代中更新。

上线前必须完成：接线并实测腾讯短信、配置随机 JWT/三类 pepper、设置真实 demo allowlist 为空、确认可信代理网段、执行备份与迁移演练、检查审计归档任务。

## 7. 兼容窗口

旧 `/api/auth/sms-code` 与旧登录兼容入口只保留到 **2026-09-05**。兼容期内记录 deprecation 指标；到期删除旧端点和 `AUTH_ALLOW_LEGACY_TOKENS` 兼容路径，避免旧 7 天 JWT 长期残留。
