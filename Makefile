.PHONY: dev test build clean lint ci format help install install-backend install-frontend docker-up docker-down db-init

help:
	@echo "OpenScrcpy Development Commands:"
	@echo ""
	@echo "  make dev            - Start development servers (backend + frontend)"
	@echo "  make test           - Run unit/integration tests (pytest + vitest, 排除 e2e)"
	@echo "  make lint           - Run linters (ruff + mypy + eslint)"
	@echo "  make ci             - 本地 CI 门禁 (ruff+mypy+pytest / eslint+vitest+build)"
	@echo "  make format         - Format code (ruff + eslint)"
	@echo "  make build          - Build for production"
	@echo "  make clean          - Clean build artifacts"
	@echo "  make install        - Install all dependencies"
	@echo "  make docker-up      - [可选/未验证] Start Docker containers"
	@echo "  make docker-down    - [可选/未验证] Stop Docker containers"
	@echo "  make db-init        - Initialize database"
	@echo ""

dev:
	@bash scripts/dev.sh

test:
	@echo "Running backend tests..."
	PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -v
	@echo "Running frontend tests..."
	cd frontend && npx vitest run

lint:
	@echo "Running backend linters..."
	python -m ruff check backend/app
	python -m mypy backend/app
	@echo "Running frontend linters..."
	cd frontend && npm run lint

# 本地等价 CI 门禁：与 .github/workflows/ci.yml 步骤一致
ci:
	python -m ruff check backend/app
	python -m mypy backend/app
	PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q
	cd frontend && npm run lint && npx vitest run && npm run build

format:
	@echo "Formatting backend..."
	ruff format backend/app
	@echo "Formatting frontend..."
	cd frontend && npm run format

build:
	@echo "Building frontend..."
	cd frontend && npm run build

clean:
	@echo "Cleaning..."
	rm -rf backend/dist backend/build backend/*.egg-info
	rm -rf frontend/dist frontend/node_modules/.vite
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true

docker-up:
	docker compose up -d

docker-down:
	docker compose down

db-init:
	@echo "Initializing database..."
	python -c "import asyncio; from backend.app.infrastructure.persistence.sqlite import init_db; asyncio.run(init_db())"

install-backend:
	pip install -e ".[dev]"

install-frontend:
	cd frontend && npm ci

install: install-backend install-frontend
	@echo "All dependencies installed"
