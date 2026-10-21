# API 接口文档（学生端 M2）

> 基于 OpenAPI + 实测整理。信封约定：{code, message, data}，code=0 成功；HTTP 40001 参数错、40400 不存在、42901 限流、50301 服务不可用。
> 鉴权：登录后携带 Authorization: Bearer {token}。

## 0. 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/auth/sms-code | 发送验证码 {phone}（60s 限流，dev 环境 code=123456） |
| POST | /api/auth/login | 登录 {phone, code} → data.token / data.user（新手机号自动注册） |

## 1. 练题中心

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/student/practice/start | 开练。body {mode: special/retry/daily, kp_code?, count?}。special 支持 count 5~30；题库优先（真题标注 source + ai_generated），缺口才走 LLM（四闸+日限） |
| GET | /api/student/practice/group-recommend | 今日训练组 {count=5, kp_code?}。新用户空态返回摸底知识点（集合），3:2:1 配比 + BKT 外推 |
| GET | /api/student/practice/difficulty-mix | 难度配比展示 |
| POST | /api/student/practice/submit | 提交判分（choice/blank/solution 三题型，SymPy 校验） |

## 2. 模拟考试

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/student/exam/generate | 组卷 {type: full_mock/topic, kp_module?, title?}。full_mock 150分/120分钟（8选择×5 + 3填空×5 + 5解答×19），bank+AI 混排，返回 bank_count/ai_count/items |
| GET | /api/student/exam/history | 试卷历史 + 成绩聚合 |

## 3. 错题本 + FSRS

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/student/error-records | 收录错题 {question_text, answer_text, source_channel(manual_photo/auto_judge/chat_command), error_type?, kp_code?}。**迭代19 去重语义**：同用户同题干（去空白后一致）只保留一条活动记录，重复收录 wrong_count+1、补全缺失字段；返回 data.created 表示是否新建（唯一索引兜底并发） |
| GET | /api/student/error-records | 三视图列表（view=time/kp/error_type） |
| GET | /api/student/error-records/{id}/detail | 详情 + FSRS 字段（memory_stability/retrievability/fsrs_level） |
| POST | /api/student/error-records/{id}/review | 复习 {result: remembered/forgotten}。remembered 推进 1/3/7/15 档；forgotten 重置并 wrong_count+1。返回 fsrs_stability/memory_level |
| GET | /api/student/error-records/filter | 多维筛选（error_type/kp_code/date_from/date_to/stability=stable/decaying/critical） |
| GET | /api/student/error-records/memory-heatmap | 4周×7天记忆热力图（格子按稳定度 S 分级：lv4~lv1/new，见 §8） |
| GET | /api/student/error-records/due-queue | 到期队列（R 升序） |

## 4. 学情报告

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/student/growth/overview | 全域聚合（综合分/到期错题/掌握点数） |
| GET | /api/student/growth/panel | 右栏面板（黄金窗口/今日行动/周简报/鼓励语/高考倒计时；鼓励语带 24h 缓存） |
| GET | /api/student/report/weak-points | 薄弱点 Top4（含 ai_reason 与 primary_action 跳转） |
| GET | /api/student/report/highlights | 高光（日缓存） |
| GET | /api/student/report/error-distribution | 12 类思维漏洞分布 |
| GET | /api/student/report/trend | 掌握度趋势 + FSRS 7 日预测 |

## 5. AI 管家

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/agent/chat | SSE 对话主入口。body {conversation_id?, message, context{workspace, client_msg_id}}。事件流：meta→status→token/card/action/**figure**→title→done。figure 为 F13 可视化讲解图形事件（见 §10），可多次出现，未知事件旧前端忽略（向后兼容） |
| POST | /api/agent/chat/regenerate | 重生成 {conversation_id, message_id} |
| GET | /api/agent/conversations | 会话列表 |
| GET | /api/butler/dashboard | 管家面板（开场白/今日3件事/到期错题/薄弱点/鼓励语） |
| GET | /api/butler/weekly-report | 周报（小婷的话） |
| GET | /api/butler/error-diagnosis/{record_id} | 错题 AI 错因诊断（根因/记忆口诀/补救建议） |
| GET | /api/butler/error-detail/{record_id} | 错题 AI 正解（generated_answer） |
| POST | /api/butler/error-tutor | 错题苏格拉底答疑 {record_id, student_message, history?} |
| GET | /api/butler/actions | 管家动作轮询 |

## 6. 文件（拍照题）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/files/upload | 元数据 {filename, mime, size_bytes, sha256, multipart} → presigned PUT URL + file_id |
| PUT | {upload_url} | 直传对象存储（MinIO/S3） |
| POST | /api/files/{id}/complete | 完成回调。非分片（upload_id 空）幂等确认；分片传 {upload_id, parts} |
| POST | /api/files/{id}/parse | 触发解析 {engine_hint: auto, purpose: chat_attach/question_photo/kb_ingest}。question_photo 优先 wf_doc_understand 云轨（LaTeX+confidence），RapidOCR 兜底。**迭代19**：云轨图片经 data URI 直传（星辰文件服务已下线，不再依赖 /workflow/v1/files） |
| GET | /api/files/{id} | 轮询状态（status/parse_engine/parse_quality.confidence/assets） |

## 7. 意图路由与页面直达

- 对话内「打开错题本/去学情报告」等：动作词+平台页面名 → precheck page_intent 阶段确定性拦截 → SSE action 事件 {kind: open_page, to, label, params}（零 LLM）。
- 「来一套全真模拟/做套卷/练薄弱」→ practice_intent 拦截 → open_page 练题中心。
- 对话内「出几道/变式」明确除外，仍走 smart_quiz。
- 影子评测：每次路由旁路 wf_intent_router 落 router_eval_logs（utterance/local_decision/xc_decision/agree）。

## 8. FSRS 字段与等级契约（迭代18）

- 稳定度 S（天）= 按复习次数分段（0.4/1/3/7/15）× 答错惩罚（每多错一次 -15%，最低 40%）。
- 可提取性 R(t) = (1 + t/(9S))^-1；到期判定 R < 0.85；衰减红阈值 0.6。
- 记忆等级（稳定度 S 分级）：

| 等级 | 含义 | S 范围 | 筛选档位 |
|------|------|--------|----------|
| new | 刚收录/待复习 | S < 1 | critical |
| lv1 | 偏弱（复习 1 次） | 1 ≤ S < 3 | decaying |
| lv2 | 一般（复习 2 次） | 3 ≤ S < 7 | decaying |
| lv3 | 稳定（复习 3 次） | 7 ≤ S < 15 | stable |
| lv4 | 非常稳（复习 4 次+） | S ≥ 15 | stable |

- 热力图格子等级取该格错题的最小稳定度；record_ids 全收。

## 9. 性能基线（实测 2026-08-15）| 接口 | P50 | P95 |
|------|-----|-----|
| error-records/filter | 60ms | 134ms |
| memory-heatmap | 46ms | 54ms |
| due-queue | 82ms | 98ms |
| practice/start(5题真题) | 121ms | 138ms |
| group-recommend | 74ms | 94ms |
| growth/panel | 76ms（缓存后） | — |
| weak-points | 60ms | 3016ms（缓存过期回填） |
| weekly-report | 103ms | 2307ms（缓存过期回填） |

## 10. figure 事件协议（F13 可视化讲解，迭代19）

引导式解题（socratic_solver）中，AI 图形规划器为相关讲解步骤生成结构化图形参数，
由 figure_renderer 确定性渲染为多帧 SVG 后，经 SSE `figure` 事件随讲解流下发：

```json
event: figure
data: {
  "step_no": 2,
  "caption": "观察抛物线的开口与顶点",
  "frames": [
    {"data_uri": "data:image/svg+xml;base64,…", "label": "坐标系与曲线"},
    {"data_uri": "data:image/svg+xml;base64,…", "label": "标注关键点"}
  ],
  "figure_params": {"type": "function", "params": {...}}
}
```

- 契约校验：`app/kernel/figure_block.py`（frames 1~6、data_uri ≤200KB、step_no 正整数；非法丢弃不 500）；
- 帧语义：累计式渐进揭示，第 1 帧恒不含答案性标注（零点/极值点/顶点字母）；
- 发射策略（防泄题）：开场/引导/提示/纠错只发第 1 帧；学生答对后发完成步全帧（视觉确认）；
  揭示/总结时发全部步骤全帧；
- 历史：envelope 落 `figure` block，幂等重放与 fromHistory 还原；旧前端未知事件忽略（双向兼容）；
- 图形类型优先级：函数图像 > 立体几何 > 解析几何 > 平面几何（figure_renderer 支持
  function/cube/cuboid/棱柱/棱锥/棱台/球(含外接组合)/polyhedron/triangle2d）。