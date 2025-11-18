# Makefile for Bloom Monorepo

# Default target when you just run `make`
.PHONY: help
help:
	@echo "Usage:"
	@echo "  make dev-up           - Run full stack in development mode"
	@echo "  make dev-down         - Stop development stack"
	@echo "  make prod-up          - Run full stack in production mode"
	@echo "  make prod-down        - Stop production stack"
	@echo "  make dev-logs         - Tail development logs"
	@echo "  make reset-storage    - Reset dev DB and storage (destructive)"
	@echo "  make drop-tables      - Drop all tables in public schema"
	@echo "  make apply-migrations - Apply database migrations using Supabase CLI"
	@echo "  make load-test-data   - Load CSV test data into dev database"
	@echo "  make upload-images    - Upload test images to MinIO storage"
	@echo "  make create-bucket    - Create a new S3 bucket (BUCKET=name [PUBLIC=true])"
	@echo "  make list-buckets     - List all S3 buckets"

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
.PHONY: dev-logs
logs:
	docker compose -f docker-compose.dev.yml logs -f

.PHONY: prod-logs
prod-logs:
	docker compose -f docker-compose.prod.yml logs -f


## Dev-only destructive operation: reset storage and DB (clear all data)
.PHONY: reset-storage
reset-storage:
	@echo "WARNING: This will DESTROY development DB volumes and clear the MinIO host storage."
	@printf "Are you sure you want to continue? Type 'yes' to proceed: " ; read ans ; if [ "$$ans" != "yes" ] ; then echo "Aborted." ; exit 1 ; fi
	@echo "Stopping dev stack..."
	@docker compose -f docker-compose.dev.yml down || true

	# Try to detect MinIO host path from docker-compose.dev.yml (first match under supabase-minio service)
	@minio_host_path=$$(awk '/^\s*supabase-minio:/ {p=1} p && /^\s*volumes:/ {getline; if ($$0 ~ /-/) {print $$2} ; exit}' docker-compose.dev.yml | sed -E 's/^(-\s*)?([^:]+):.*$$/\2/') || true ; \
	if [ -z "$$minio_host_path" ] || [ "$$minio_host_path" = "docker-compose.dev.yml" ] ; then \
		echo "Could not detect MinIO host path from docker-compose.dev.yml. Please enter the host path to clear (e.g. /Users/minio):"; read minio_host_path ; \
	fi ; \
	if [ -n "$$minio_host_path" ]; then \
		echo "About to remove all files in $$minio_host_path (this is destructive)."; \
		printf "Final confirmation - type 'delete' to remove files in $$minio_host_path: "; read final ; \
		if [ "$$final" = "delete" ]; then \
			echo "Removing files in $$minio_host_path..." ; \
			rm -rf "$$minio_host_path"/* || true ; \
			echo "MinIO host path cleared: $$minio_host_path" ; \
		else \
			echo "Skipped clearing MinIO host path." ; \
		fi ; \
	else \
		echo "No MinIO host path specified. Skipping MinIO cleanup." ; \
	fi

	@echo "Removing docker volumes prefixed with 'bloom_v2_dev_'..."
	@docker volume ls -q | grep '^bloom_v2_dev_' | xargs -r docker volume rm || true

	@echo "Bringing dev stack back up..."
	@docker compose -f docker-compose.dev.yml --env-file .env.dev up -d --build
	
	@echo "Waiting for database to be ready..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		if docker exec db-dev pg_isready -U supabase_admin -h localhost >/dev/null 2>&1; then \
			echo "Database is ready"; \
			break; \
		fi; \
		echo "Waiting for database... ($$i/10)"; \
		sleep 2; \
	done
	
	@echo "Truncating all tables to remove seed data..."
	@docker exec db-dev psql -U supabase_admin -d postgres -c "\
		DO \$$\$$ \
		DECLARE \
			r RECORD; \
		BEGIN \
			FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP \
				EXECUTE 'TRUNCATE TABLE public.' || quote_ident(r.tablename) || ' CASCADE'; \
			END LOOP; \
		END \$$\$$;" || echo "Note: Some tables may not exist yet"
	
	@echo "reset-storage completed. Database and storage are now empty."

## Drop all tables in public schema
.PHONY: drop-tables
drop-tables:
	@echo "WARNING: This will DROP all tables in the public schema."
	@printf "Are you sure you want to continue? Type 'yes' to proceed: " ; read ans ; if [ "$$ans" != "yes" ] ; then echo "Aborted." ; exit 1 ; fi
	@if ! docker ps | grep -q db-dev; then \
		echo "Error: Development database not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Dropping all tables in public schema..."
	@docker exec db-dev psql -U supabase_admin -d postgres -c "\
		DO \$$\$$ \
		DECLARE \
			r RECORD; \
		BEGIN \
			FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP \
				EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE'; \
			END LOOP; \
		END \$$\$$;"
	@echo "All tables dropped successfully."

## Apply database migrations
.PHONY: apply-migrations
apply-migrations:
	@echo "Applying database migrations..."
	@if ! docker ps | grep -q db-dev; then \
		echo "Error: Development database not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Applying migrations from supabase/migrations/ directory..."
	@for file in $$(ls -1 supabase/migrations/*.sql 2>/dev/null | sort); do \
		if [ -f "$$file" ]; then \
			echo "Applying: $$(basename $$file)"; \
			PGPASSWORD=postgres psql -h localhost -p 5432 -U supabase_admin -d postgres -f "$$file" 2>&1 | grep -v "already exists" | grep -v "does not exist, skipping" || true; \
		fi \
	done
	@echo "All migrations applied successfully."

## Load test data into development database
.PHONY: load-test-data
load-test-data:
	@echo "Loading test data into development database..."
	@if ! docker ps | grep -q db-dev; then \
		echo "Error: Development database not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Installing Python dependencies..."
	@python3 -m pip install --quiet supabase pandas 2>/dev/null || (echo "Error: Failed to install dependencies. Install with: pip install supabase pandas" && exit 1)
	@echo "Running data loader script..."
	@python3 scripts/load_test_data.py

## Upload test images to MinIO storage
.PHONY: upload-images
upload-images:
	@echo "Uploading test images to MinIO storage..."
	@if ! docker ps | grep -q supabase-minio; then \
		echo "Error: MinIO not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Installing Python dependencies..."
	@python3 -m pip install --quiet supabase 2>/dev/null || (echo "Error: Failed to install dependencies. Install with: pip install supabase" && exit 1)
	@echo "Running image uploader script..."
	@python3 scripts/upload_test_images.py

## Create a new MinIO bucket
.PHONY: create-bucket
create-bucket:
	@if [ -z "$(BUCKET)" ]; then \
		echo "Error: BUCKET name required. Usage: make create-bucket BUCKET=my-bucket-name [PUBLIC=true]"; \
		exit 1; \
	fi
	@if ! docker ps | grep -q supabase-minio; then \
		echo "Error: MinIO not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Installing Python dependencies..."
	@python3 -m pip install --quiet supabase 2>/dev/null || (echo "Error: Failed to install dependencies. Install with: pip install supabase" && exit 1)
	@echo "Creating bucket via Supabase Storage API..."
	@if [ "$(PUBLIC)" = "true" ]; then \
		python3 scripts/create_bucket.py create $(BUCKET) public; \
	else \
		python3 scripts/create_bucket.py create $(BUCKET); \
	fi

## List all MinIO buckets
.PHONY: list-buckets
list-buckets:
	@if ! docker ps | grep -q supabase-minio; then \
		echo "Error: MinIO not running. Start with 'make dev-up' first."; \
		exit 1; \
	fi
	@echo "Installing Python dependencies..."
	@python3 -m pip install --quiet supabase 2>/dev/null || (echo "Error: Failed to install dependencies. Install with: pip install supabase" && exit 1)
	@python3 scripts/create_bucket.py list

# Force rebuild (even without changes)
.PHONY: rebuild
rebuild: ensure-lock
	@echo " Rebuilding all Docker images..."
	docker compose -f docker-compose.dev.yml build --no-cache