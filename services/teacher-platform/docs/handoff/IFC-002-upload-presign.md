# IFC-002 · 新增 POST /teacher-v3/upload/presign（B3 → A0 通知）

> 状态：**已实现，待 A0 接入**（B3 M1 交付）· 2026-09-07
> 类型：additive（新端点，不改既有契约断言）

## 动机

前端直传对象存储需要预签名 URL（02-ARCHITECTURE §5：DB 只存 key 不存 URL，presign PUT 直传），
mock 中「上传直接成功」的债务由真实 MinIO presign 注销。

## 新端点（B3 §3.8）

```
POST /api/teacher-v3/upload/presign
Authorization: Bearer <JWT>
Body: { "bucket": "teacher-photos|teacher-assets|...", "object_name": "可选(默认 uploads/{user_id}/{uuid}.bin)", "content_type": "可选" }
Resp: { code:0, message:"ok", data: { url: "<presigned PUT url, 15min>", object_name: "<实际对象key>" } }
```

- bucket 白名单：`teacher-photos / teacher-assets / teacher-exports / teacher-corpus / teacher-thumbs`。
- 前端拿 url 直接 `PUT` 上传，成功后把 `object_name` 存入业务提交体。

## 预签名 URL 主机（关键配置，A0 注意）

presign URL 的 host 来自 `MINIO_ENDPOINT`（签名含 host 头，**不可事后改写**）：
- 本地前端联调（API 跑宿主机）：`MINIO_ENDPOINT=127.0.0.1:9002` → 浏览器直传 `127.0.0.1:9002` ✔。
- 全栈容器模式：`MINIO_ENDPOINT=minio:9000`（内部可达）；浏览器无法解析该 host，
  **前端联调时请让 A0 用本地 API 模式**（`.env` 已配 127.0.0.1:9002），容器模式用于后端内部链路验证。
- 生产：`MINIO_ENDPOINT` 配公网 MinIO 域名，presign URL 天然对外可达。

## 影响

- A0：前端上传链路改 presign 直传（`api/teacherV3.ts` 增补该方法）。
- 其余后端智能体：业务提交体里的图片字段一律用 `object_name`（MinIO key），不传 URL。
- 契约测试：无既有断言受影响（additive）。
