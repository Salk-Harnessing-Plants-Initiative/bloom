# Makefile for Bloom Monorepo
# Default target when you just run `make`
.PHONY: help
help:
	@echo "Usage:"
	@echo "  make dev-up    - Run full stack in development mode"
	@echo "  make rebuild-dev-fresh - Rebuild dev stack with fresh dependencies"
	@echo "  make prod-up   - Run full stack in production mode"
	@echo "  make rebuild-prod-fresh - Rebuild prod stack with fresh dependencies"
	@echo "  make down   - Stop all containers"
	@echo "  make logs   - Tail logs"
	@echo "  make rebuild    - Rebuild all Docker images without cache"

# Run development stack
.PHONY: dev-up
dev-up:
	@echo " Checking frontend dependencies..."
	@if [ ! -f "./web/package-lock.json" ]; then \
		echo " package-lock.json not found. Installing dependencies..."; \
		cd web && npm install; \
	else \
		echo " package-lock.json found."; \
	fi
	@echo " Starting Bloom Dev Stack..."
	docker compose -f docker-compose.dev.yml --env-file .env.dev up --build -d
	@echo " Bloom is running at http://localhost:3000"

.PHONY: rebuild-dev-fresh
rebuild-dev-fresh:
	@echo "Removing existing node_modules for a fresh install..."
	rm -rf web/node_modules packages/*/node_modules
	@echo "Rebuilding Dev Stack without cache..."
	docker compose -f docker-compose.dev.yml build --no-cache -d
	@echo "Dev Stack images rebuilt with fresh dependencies."


# Run production stack
.PHONY: prod-up
prod-up:
	@echo " Checking frontend dependencies..."
	@if [ ! -f "./web/package-lock.json" ]; then \
		echo " package-lock.json not found. Installing dependencies..."; \
		cd web && npm install; \
	else \
		echo " package-lock.json found. Installing .. "; \
	fi
	@echo " Starting Bloom Production Stack..."
	docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build -d
	@echo " Bloom Production running in background"

.PHONY: rebuild-prod-fresh
rebuild-prod-fresh:
	@echo "Removing existing node_modules for a fresh install..."
	rm -rf web/node_modules packages/*/node_modules
	@echo "Rebuilding Prod Stack without cache..."
	docker compose -f docker-compose.prod.yml build --no-cache -d
	@echo "Prod Stack images rebuilt with fresh dependencies."

# Stop dev
.PHONY: dev-down
dev-down:
	docker compose -f docker-compose.dev.yml down
	@echo "All containers stopped."

# Stop prod
.PHONY: prod-down
prod-down:
	docker compose -f docker-compose.prod.yml down
	@echo "All containers stopped."

# View logs for all services
.PHONY: logs
logs:
	docker compose -f docker-compose.dev.yml logs -f

# Force rebuild (even on no changes)
.PHONY: rebuild-dev
rebuild: ensure-lock
	@echo " Rebuilding all Docker images..."
	docker compose -f docker-compose.dev.yml build --no-cache

.PHONY: rebuild-prod
rebuild-prod: ensure-lock
	@echo " Rebuilding all Docker images..."
	docker compose -f docker-compose.prod.yml build --no-cache