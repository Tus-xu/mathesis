# acceptance/ · B5 验收交付物目录（B5 独占所有权，02-ARCHITECTURE §9）

```
acceptance/
├── M{1,2-A,2-B,2-C,3,4}-report.md   # 串行 Gate 验收报告（03-ACCEPTANCE §8 强制结构）
├── M1-contract-baseline.md          # M1 契约基线红表（后端骨架 vs 前端契约差距清单）
├── defects/DEF-<n>.md               # 缺陷登记（复现步骤/证据/所属人/严重级 P0–P3）
├── screenshots/                     # 用户链路步骤截图（全页带 URL，<链路编号>-<步骤>.png）
├── comparisons/                     # AI 产物 vs 基准并排图 + 盲评分数表（M3 起）
├── perf/                            # G9 压测原始数据 + 图表（按 Gate 归档）
└── audit/                           # 数据真实性审计输出（tests/integration/test_data_audit.py 落点）
```

纪律：
- **R0 影子审查零写入**——全部 R0 产物在 `D:\teacher-research\reviews\r0\`（V2.2.1 收口①）；
- 每份 Gate 报告含「上一 Gate 回归复测结论」与「用户会怎么想」体验评述节；
- 「已知债务」显式列出并等用户点头，默认隐藏债务 = B5 失职。
