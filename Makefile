# Makefile for Bloom Monorepo

# Default target when you just run `make`
.PHONY: help
help:
	@echo "Usage:"
	@echo "  make dev    - Run full stack in development mode"
	@echo "  make prod   - Run full stack in production mode"
	@echo "  make down   - Stop all containers"
	@echo "  make logs   - Tail logs"

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
	docker compose -f docker-compose.dev.yml --env-file .env.dev up --build
	@echo " Bloom is running at http://localhost:3000"

.PHONY: rebuild-dev-fresh
rebuild-dev-fresh:
	@echo "Removing existing node_modules for a fresh install..."
	rm -rf web/node_modules packages/*/node_modules
	@echo "Rebuilding Dev Stack without cache..."
	docker compose -f docker-compose.dev.yml build --no-cache
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
	docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
	@echo " Bloom Production running in background"

.PHONY: rebuild-prod-fresh
rebuild-prod-fresh:
	@echo "Removing existing node_modules for a fresh install..."
	rm -rf web/node_modules packages/*/node_modules
	@echo "Rebuilding Prod Stack without cache..."
	docker compose -f docker-compose.prod.yml build --no-cache
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

# Force rebuild (even without changes)
.PHONY: rebuild
rebuild: ensure-lock
	@echo " Rebuilding all Docker images..."
	docker compose -f docker-compose.dev.yml build --no-cache