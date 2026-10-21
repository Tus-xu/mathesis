# 教师端 V3 平台 · 一键开发命令（02-ARCHITECTURE §1.3 / B3 §3.10）
# make up = 拉起全栈 + 迁移 + 种子（一条命令能起全栈是硬标准）

.PHONY: up down logs api worker migrate seed test gate health ps

up:
	docker compose up -d --build
	@echo "==> 等待基础设施健康…"
	python - <<'PY'
import time, urllib.request, sys
for i in range(60):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8100/healthz", timeout=2) as r:
            if r.status == 200:
                print("==> /healthz 全绿 ✔"); sys.exit(0)
    except Exception:
        pass
    time.sleep(3)
print("==> /healthz 未全绿（查看 docker compose ps）"); sys.exit(1)
PY

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f --tail=100

api:
	uv run uvicorn main:app --host 0.0.0.0 --port 8100 --reload

worker:
	uv run taskiq worker app.tasks.broker:broker

migrate:
	uv run alembic upgrade head

seed:
	uv run python -m seeds.seed_dev

test:
	uv run pytest tests/unit -q

gate:
	uv run pytest tests/integration/valkey_gate -q -m gate

health:
	python - <<'PY'
import urllib.request, json
print(json.dumps(json.load(urllib.request.urlopen("http://127.0.0.1:8100/healthz")), ensure_ascii=False, indent=2))
PY
