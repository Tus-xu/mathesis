.PHONY: help setup infra migrate api frontend seed test lint clean

help: ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ==================== 一键启动 ====================

setup: infra migrate ## 一键：启动基础设施 + 数据库迁移

infra: ## 启动全部 Docker 基础设施（PG×2 / Redis×2 / MinIO）
	docker compose -f deploy/docker-compose.yml up -d
	@echo "等待数据库就绪..."
	@sleep 5

api: ## 启动后端 API（端口 8000）
	cd services/api && python run_server.py

frontend: ## 启动前端 dev server（端口 5176）
	cd frontend && pnpm dev

seed: ## 灌入演示数据（账号/班级/知识库/引导会话）
	python tools/seed_demo_accounts.py --profile local

# ==================== 数据库 ====================

migrate: ## 运行数据库迁移（alembic upgrade head）
	cd services/api && alembic upgrade head
	@echo "注意：teacher-platform / research-vnext / ppt-studio-v2 需单独迁移（见各目录 README）"

migrate-teacher: ## 教师平台迁移
	cd services/teacher-platform && python -m alembic upgrade head

migrate-research: ## 科研平台迁移
	cd services/research-vnext && python -m alembic upgrade head

# ==================== Docker ====================

docker-up: ## 启动全部 Docker 基础设施
	docker compose -f deploy/docker-compose.yml up -d

docker-down: ## 停止全部 Docker 服务
	docker compose -f deploy/docker-compose.yml down

docker-clean: ## 停止并删除全部容器和数据卷（危险！）
	docker compose -f deploy/docker-compose.yml down -v

# ==================== 代码质量 ====================

lint: ## 运行 lint 检查
	cd services/api && ruff check .

lint-fix: ## 自动修复
	cd services/api && ruff check --fix .

# ==================== 测试 ====================

test: ## 运行后端测试
	cd services/api && pytest tests/ -q

test-frontend: ## 运行前端测试
	cd frontend && pnpm test:run

# ==================== 清理 ====================

clean: ## 清理构建产物和缓存
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null
