.PHONY: dev test build clean lint format help install install-backend install-frontend docker-up docker-down db-init

help:
	@echo "OpenScrcpy Development Commands:"
	@echo ""
	@echo "  make dev            - Start development servers (backend + frontend)"
	@echo "  make test           - Run all tests (pytest + vitest)"
	@echo "  make lint           - Run linters (ruff + mypy + eslint)"
	@echo "  make format         - Format code (ruff + eslint)"
	@echo "  make build          - Build for production"
	@echo "  make clean          - Clean build artifacts"
	@echo "  make install        - Install all dependencies"
	@echo "  make docker-up      - Start Docker containers"
	@echo "  make docker-down    - Stop Docker containers"
	@echo "  make db-init        - Initialize database"
	@echo ""

dev:
	@bash scripts/dev.sh

test:
	@echo "Running backend tests..."
	python -m pytest backend/tests -v
	@echo "Running frontend tests..."
	cd frontend && npm run test

lint:
	@echo "Running backend linters..."
	ruff check backend/app
	@echo "Running frontend linters..."
	cd frontend && npm run lint

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
