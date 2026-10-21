# ZXSY Math Arena

数学垂类大模型 —— 面向高中数学的三端 AI 教学平台

- **学生端**：AI 引导式解题、知识库文件引用问答、举一反三变式链、可视化讲解、错题本 FSRS 复习
- **教师端**：备课中心（教学设计 → PPT → 教案）、班级管理、作业发布与 AI 批改、双师课堂
- **科研端**：论文阅读（PDF + 选中问 Agent）、LaTeX 写作（编译 + 预览）、公式校验

## 快速启动

### 1. 克隆 + 配置

```bash
git clone https://github.com/xu123hu/zxsy-math-arena.git
cd zxsy-math-arena

# 配置环境变量（填入你的 LLM API Key）
cp .env.example .env
# 编辑 .env，至少填 SPARK_API_PASSWORD 和 EMBEDDING_API_KEY
```

### 2. 启动基础设施（Docker）

```bash
# 启动 PostgreSQL(×2) / Redis(×2) / MinIO，自动创建 4 个数据库
docker compose -f deploy/docker-compose.yml up -d

# 等待就绪（约 5 秒）
docker compose -f deploy/docker-compose.yml ps
```

| 服务 | 端口 | 数据库 |
|------|------|--------|
| PostgreSQL (pgvector pg15) | 54329 | math_arena, research_vnext |
| PostgreSQL (pgvector pg16) | 54330 | teacher_platform, ppt_studio_v2 |
| Redis | 6379 | 主缓存 |
| Valkey | 6380 | 教师平台 |
| MinIO | 9000/9001 | 文件存储 |

### 3. 后端 API（端口 8000）

```bash
cd services/api
python -m venv .venv
.venv/Scripts/activate       # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -e ".[dev]"

# 数据库迁移（创建所有表）
alembic upgrade head

# 启动
python run_server.py
# → http://localhost:8000/api/health
```

### 4. 前端（端口 5176）

```bash
cd frontend
cp .env.example .env
pnpm install
pnpm dev
# → http://localhost:5176
```

### 5. 灌入演示数据（可选）

```bash
python tools/seed_demo_accounts.py --profile local
```

创建演示账号：学生 13800001001 / 教师 13800001002 / 科研 13800001003，密码 `MathArenaDemo2026!`

### 用 Makefile 一键操作

```bash
make infra       # 启动 Docker
make migrate     # 数据库迁移
make api         # 启动后端
make frontend    # 启动前端
make seed        # 灌入演示数据
```

## 仓库结构

```
zxsy-math-arena/
├── frontend/                        # Vue 3 前端
│   ├── src/                         # 源代码
│   ├── public/                      # 静态资源
│   ├── test/                        # 单元测试
│   └── e2e/                         # E2E 验收脚本
├── services/
│   ├── api/                         # FastAPI 统一网关 (8000)
│   ├── teacher-platform/            # 教师平台 (8100)
│   ├── research-vnext/              # 科研工作台 (8300)
│   ├── ppt-studio-v2/               # PPT 生成 (8200)
│   ├── ppt-presenton/               # PPT 渲染适配
│   ├── sandbox/                     # SymPy 沙箱
│   └── teacher-harness/             # 教师端工具
├── packages/protocol/               # 共享类型
├── deploy/                          # Docker Compose + Nginx
├── docs/                            # 架构文档
├── data/                            # 知识库种子 / 论文种子
├── tools/                           # 播种 / 运维
├── scripts/                         # 开发脚本
└── .github/workflows/               # CI
```

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Pinia |
| 后端 | FastAPI + SQLAlchemy 2.x (async) + Pydantic v2 |
| 数据库 | PostgreSQL 15/16 + pgvector |
| 缓存 | Redis 7 / Valkey |
| AI | DeepSeek-V4 + 豆包 Seed + Qwen VL |
| 部署 | Docker Compose |

## License

Private
