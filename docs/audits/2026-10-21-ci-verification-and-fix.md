# CI 失败归因与修复审计（2026-10-21）

> 对象：GitHub Actions run #4（`39637741538`，commit `d25cd29`）
> 结论先行：**11 个 Backend Test 失败 100% 是既有问题，与本次 lint / README / 迁移补齐三个提交无关**（已用原始提交 `faeffcf` 对照实验证明失败集完全一致）。随后已全部修复并本地全量验证转绿。

---

## 1. Run #4 三 job 结果

| Job | 结果 | 耗时 | 说明 |
|---|---|---|---|
| Backend Lint | ✅ success | 21s | ruff + black 门禁通过（本次 UP031 修复生效） |
| Protocol Consistency | ✅ success | 13s | `pnpm --filter @math-arena/protocol type-check` |
| Backend Test | ❌ failure | 608s | **11 failed, 1898 passed, 18 skipped** |

页面另显示 10 条 `Event loop is closed` 的 **failure 级注解**——经查证**不是测试失败**：
它们来自 pytest-asyncio 的 async generator fixture teardown 阶段，SQLAlchemy 异步连接池在
已关闭的事件循环里执行 `close()` 抛出的 `RuntimeError`，被日志 sink 渲染成 GitHub
annotation。属装饰性噪声，不影响退出码（退出码 1 来自 pytest 的 11 个失败）。

---

## 2. 判决性对照实验（零回归证明）

在原始提交 `faeffcf`（"init: 三端 AI 教学平台"，即本次所有修改的起点）上用
`git worktree` 跑同一套全量测试：

| 运行 | 提交 | 环境 | passed | failed | skipped |
|---|---|---|---|---|---|
| CI | `d25cd29` | ubuntu, py3.12, 全新 PG15 service 库 | 1898 | **11** | 18 |
| 对照 | `faeffcf` | 本地, py3.13, 测试库 | 1899 | **11** | 17 |
| 本地 | `d25cd29` | 本地, py3.13, 测试库（已污染） | 1894 | **17** + 2 errors | 14 |

程序化集合比对（`git diff` 无关，纯失败用例 ID 集合）：

```
CI == CTL            → True      # 失败集完全相同
CI - CTL             → 空集      # 不存在「仅 CI 挂而原始提交过」的用例 ⇒ 零回归
LOC - CI             → 6 个      # 本地多出的失败，全部源于本地测试库漂移
```

**结论：CI 的 11 个失败在本次修改之前就存在，不是回归。**

### 2.1 为何 diff 触及数百个 app/tests 文件却不是行为变更

`git diff --stat faeffcf d25cd29 -- services/api` 显示 301 files / +11346 / -4477，
其中绝大部分是 `black .` 全量格式化（引号规范化 `'` → `"`、行重排、魔法尾逗号），
CI 的 `black --check .` 要求如此。逐项核实过语义风险最高的三类 ruff 自动修复：

| 规则 | 数量 | 实际改动 | 语义判定 |
|---|---|---|---|
| UP017 `datetime-timezone-utc` | 14 | 仅 `timezone.utc` → `UTC` 导入别名（3.11+ 同一对象） | **等价**，无 `utcnow()` → `now(UTC)` 转换 |
| F401 `unused-import` | 23 | 集中在 tests/scripts；`app/models/__init__.py` 仅排序无删除 | 安全 |
| F811 `redefined-while-unused` | 2 | 移除未被引用的前置定义 | 安全 |
| UP031 `printf-string-formatting` | 3 | `'%s' % k` → `f"{k!s}"`（均走 `str()`） | 等价（带注释说明） |

另加一道安全网：**导入当前树全部 201 个 `app.*` 模块**，仅 1 处导入期断言失败
（`app/domains/classroom/_clean_prompt.py` 的模块内自检 `assert old1 in text`），
且该失败在 `faeffcf` 上**完全相同**——既有缺陷，非本次引入。

---

## 3. 11 个失败的根因（四类）

### 3.1 `knowledge_points` 白名单缺失（4 个）——CI hermetic 缺陷

`tests/conftest.py::_seed_kp_whitelist()` 原本只从**开发库 `math_arena`** 复制
MATH 知识点树。CI 只有 service 起的空 PG，没有开发库，于是打印
`开发库 math_arena 不可达，跳过 kp 白名单种子` 后直接返回 → 测试库
`knowledge_points` 为空表。

- **3 个 butler 用例**：planner 产出 `student.practice.generate(kp_code="函数")`，
  `_resolve_kp_code()` 在空表零命中 → 工具走「没有找到知识点」引导分支、
  **不建任务行** → router 按幂等键反查 `tasks` 表查不到 → `envelope.actions == []`
  → `assert envelope["actions"]` 挂。
  插桩证据：`AgentRun.status=succeeded`、回复文本「已完成」、
  `ToolInvocation.status=executed`，但 `tasks` 表 0 行。
- **1 个 KB 导入用例**：硬编码 `kp_codes=["MATH-PEP-BIXI1-B21C8FA0DE"]`，
  空表白名单校验不过 → `accepted=False` → `assert False is True`。

### 3.2 CI 无 LLM 凭据（6 个）——验收测试的环境前提

`tests/test_classroom_acceptance.py` 是**真实 LLM 外呼**黑盒验收（文件头即声明
「需 PG 测试库 + LLM 可用」）。CI 不注入任何密钥，模型路由
`All model providers failed` → 会话落 `failed` → `assert d["status"] == "ready"` 挂。

同文件内部**守卫不一致**：`test_a_contains_correct_math` 等有
`if d["status"] != "ready": pytest.skip("LLM 生成未完成")`，
而 `test_{a,b,c}_generated_via_generic_path` 与
`test_variant_generated_via_generic_path[C-v1/v2/v3]` 直接硬断言。

### 3.3 FSRS 浮点精度（1 个）——测试自身的时序缺陷

`assert items[0]["current_t"] == fsrs.days_since(rec_due.updated_at)`
左边是服务端在 T1 算的「距 updated_at 天数」，右边是测试在 T2 重算的；
两次 `now` 相隔微秒，**精确相等本身就是缺陷**（CI 实测差 `1.2e-9` 天）。

### 3.4 本地库漂移额外造成的 6 个失败（仅本地，CI 不出现）

`test_variant_contains_3d_figure[C-v1/C-v3]`、
`TestSameGenericPath::test_all_problems_use_same_engine`、
`TestHistoryEndpoints::test_clone_session`、
`TestBankModelAndMigration` 2 个、`TestAttachmentStateCheck` 2 个 error。
同样是缺 KP 数据 / 本地库残留状态所致——修复 3.1 后一并转绿。

---

## 4. 修复内容（4 处改动）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `services/api/tests/fixtures/kp_whitelist.csv`（新增 68KB） | 301 个 `MATH-PEP-*` 正式知识点树（5 册人教A版2019），源自旧仓库 `exports/20260914/math_arena_csv/knowledge_points.csv` |
| 2 | `services/api/tests/conftest.py` | `_seed_kp_whitelist()` 增加**内置夹具回退**：开发库不可达时改读 CSV；抽出 `_insert_kp_rows`（树深排序 + `ON CONFLICT DO NOTHING`，幂等）、`_load_kp_fixture`、`_parse_pg_array`（PG 数组字面量解析） |
| 3 | `services/api/tests/test_learning_events_backflow.py` | 改 `pytest.approx(..., abs=1e-6)`（≈86ms，远大于两次计算间隔，仍能抓量级错误） |
| 4 | `services/api/tests/test_classroom_acceptance.py` | 新增 `_llm_configured()`（ARK / DeepSeek / Spark 任一有凭据即为真）+ `_require_llm()`，守卫下沉到 `_create_and_wait()` **单一入口** |

### 4.1 设计取舍

- **KP 夹具优先开发库**：开发库仍是实时事实源（优先级 1），夹具只是 CI hermetic 的回退
  （优先级 2）。开发库恢复后行为不变。
- **LLM 守卫不洗红**：仅在**未配置任何凭据**时跳过；配置了凭据却仍失败照常红。
  瞬时故障的 3 次重试约定（`_create_and_wait` 原有逻辑）不受影响。
  这把原先「全部落 failed → `engines` 空集 → vacuously pass」的
  `test_all_problems_use_same_engine` 也转成了诚实 skip。
- **守卫放在 `_create_and_wait` 而非每个测试**：单一入口覆盖全部调用方，
  同时把该文件从 508s 降到 2.56s（不再空等必然失败的生成）。

---

## 5. 验证证据

```
# 门禁
ruff check .   → All checks passed!
black --check . → 451 files would be left unchanged

# 全量回归（修复后）
1903 passed, 24 skipped, 0 failed, 0 error in 221.15s
[conftest] 开发库 math_arena 不可达，已用内置夹具复制 301/301 个知识点

# 分文件
test_classroom_acceptance.py → 14 skipped in 2.56s（修复前 508s，且 6 个失败）
test_learning_events_backflow.py → 9 passed
原 17 个本地失败逐一定位 → 11 passed / 1 skipped / 0 failed
```

账目核对：修复前本地 1927 个用例 = 1894 passed + 17 failed + 14 skipped + 2 errors；
修复后 1927 个用例 = 1903 passed + 24 skipped。用例总数一致，失败与错误归零。

---

## 6. 遗留事项（需人工决策）

1. **CI 是否要配 LLM 凭据**：当前课堂验收在 CI 恒跳过。若希望 CI 真验内容正确性，
   需在 repo secrets 注入 `ARK_API_KEY` / `ARK_BASE_URL` / `ARK_MODEL`
   （或 DeepSeek / Spark 任一），并在 ci.yml 的 `backend-test` job 加 `env:`。
   不配则维持跳过——这比伪装成红色更诚实，但意味着这道门禁在 CI 是空转的。
2. **`_clean_prompt.py` 模块内自检失败**（`assert old1 in text`）：`faeffcf` 即存在，
   不影响测试（该模块无对应用例），但 import 期抛异常值得单独修。
3. **本地测试库漂移**：本次修复后本地也已转绿，但根因是开发库不可达；
   建议后续把「开发库 knowledge_points 与夹具的同步」写成脚本，避免夹具过期。
