---
feature: rv04-assets-literature
status: delivered
updated: 2026-09-14
branch: master (in-place research-vnext only)
commits: pending (not committed per task)
---

# RV04 Research Asset API + Literature Metadata Ingestion

## Report

**What was built** — research-vnext 增加不可变资产层（MinIO 字节 + PG 元数据、hash 去重、MIME/压缩炸弹/恶意策略、Gateway Range/签名/撤权、对象级授权含 security floor）与 Crossref typed 文献元数据入库（DOI/provider-id/指纹分层去重、冲突不合并、RV03 `literature.ingest` handler）。未做 PDF 结构解析、Evidence 结论、sandbox、写作或前端。

**Verification** — `pytest tests/unit/assets tests/unit/literature tests/integration/test_rv04_*.py -q` → **32 passed**；`alembic upgrade head` exit 0（`0006`）；Crossref 真实 4 条写入 `REAL-METADATA-SAMPLE.json`；MinIO `:9002` health 200。

**Journey log** — ① DomainError 构造器误用导致状态码丢失；② 审查抓出撤权 floor / JobContract 注册 / 并发去重三处 critical 并修复；③ registry MinIO 9000 与实测 9002 不一致，配置走 env。

## [S1] Problem

Research 需要可信的资产层与文献元数据层：上传的不可变字节必须可审计、可授权、可 Range/签名交付；文献检索必须经 typed provider 返回真实 metadata，并按 DOI/稳定 ID/指纹分层去重。当前 research-vnext 只有 Session/Job/EvidenceCard 骨架，无 Asset 表、无 LiteratureProvider、无对象存储接线。

## [S2] Design

### 资产（Asset）

- **字节只进对象存储**（MinIO `teacher-minio:9002`，bucket `rv-assets`，key 前缀 `rvs/`）；PostgreSQL 只存元数据。
- 上传流水线（stream）：size limit → hash(sha256) → MIME magic sniff（不信 Content-Type）→ archive bomb 检查 → malware policy（拒绝已知恶意魔数/脚本伪装）→ 写入对象存储 → 落库。
- 同 tenant 内 `content_hash` 去重：重复上传返回既有 asset（不复制字节）。
- 下载/Range/签名交付统一走 8300 Gateway 路由；**每次**做对象级授权（tenant/owner/ACL/capability/revocation）；响应永不包含 MinIO 内部 URL。
- 签名 URL：HMAC + exp + asset_id + version；过期 410；撤权后 403/410。
- `rights`/`retention`/`scan_status`/`lineage` 显式字段；缺失 rights → `UNKNOWN`。

### 文献（Literature）

- `LiteratureProvider` typed adapter：provider/version/query/request_id + DOI/title/authors/date/venue/abstract/fulltext/rights/source_url/retrieved_at/raw_response_hash。
- Crossref 为真实 provider（匿名 REST）。
- 分层去重：DOI → (provider, provider_record_id) → title-author-year fingerprint；冲突不静默合并（写 `dedup_conflict`）。
- 抽取走 RV03 JobContract（`literature.ingest`），不在 request 内长时等待；限流/超时/重试分类用既有 `PROVIDER_*` 错误码。
- Abstract ≠ full text；metadata ≠ Evidence；availability 缺失 → `UNKNOWN`。

### 边界（Out of Scope）

- PDF 结构解析、EvidenceCard 结论、sandbox、写作编译、前端 Workspace。
- 不改 shared contracts / App Shell / global API client / Teacher / legacy Research。
- 不与 RV08 同文件并行。

## [S3] Out of Scope

见上。不 push/deploy。

## Tasks

- [ ] T1: 失败测试（hash 去重/跨 tenant/Range/签名过期撤权/MIME 伪造/超大/压缩炸弹/恶意文件/provider 限流超时/DOI 去重/rights 缺失）— acceptance: 先红
- [ ] T2: Asset 模型+迁移+存储适配+安全流水线+Gateway 路由 — acceptance: 真实 MinIO 上传/Range/ACL 绿
- [ ] T3: Literature Crossref adapter + 去重 + Job 接入 — acceptance: 3–5 篇真实 metadata 入库
- [ ] T4: 集成验证 + 文档 9 份 — acceptance: 命令与证据写入控制目录
