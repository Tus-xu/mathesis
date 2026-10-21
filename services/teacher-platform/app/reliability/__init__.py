"""R11.5-C 异步可靠性 / 安全 / 追踪基础闸门（P0）。

命名空间 `app/reliability/` 为跨域平台能力（与 app/db、app/core 同级地位），
供 8100 教师端与未来 8200 PPT Studio V2（DECISIONS §2/§5）共同使用：

- `models`：Job / JobEvent / Outbox / Inbox / Idempotency 正式表（reliability_* 前缀）
- `trace`：W3C traceparent/tracestate + correlation_id 贯穿（API→DB→outbox→worker→provider）
- `outbox`：TransactionalOutbox（业务状态/job/outbox 同事务）+ dispatcher/reconciler + 背压
- `jobs`：Job 状态机（PENDING/CLAIMED/COMPLETED/FAILED/CANCELLED/DLQ）+ 原子 claim/fencing
- `inbox`：消费侧去重（重复投递 / ACK 前崩溃 / 重放兜底）
- `idempotency`：owner_service/operation/principal/idempotency_key/request_hash + 同 key 409
- `security`：secret redaction + 直/间接/RAG/多模态 prompt injection negative suite
- `queue_policy`：8100/8200 queue/group/key/pool/quota/object prefix 隔离模板

设计红线（DECISIONS §4/§5 投影）：
- 不引入第二套队列框架：保留既有 TaskIQ/Valkey，本命名空间只在其上做事务化收口；
- PPT 长任务禁止进 FastAPI BackgroundTasks——统一经 outbox→stream→worker 路径；
- 生产禁止隐式 DDL；表所有权全部落正式迁移（R11.5-C e26a6b7c8d9e）。
"""
