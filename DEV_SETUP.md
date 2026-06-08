# Development Environment Setup & Configuration

This guide covers setting up Bloom for local development.

> Once the stack is up, see [`_WIKI/SUPABASE/README.md`](_WIKI/SUPABASE/README.md)
> for how the Supabase roles, RLS, storage buckets, and JWT flow are wired.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Environment Configuration](#environment-configuration)
3. [MinIO Storage Setup](#minio-storage-setup)
4. [Starting the Stack](#starting-the-stack)
5. [Database Migrations](#database-migrations)
6. [Loading Test Data](#loading-test-data)
7. [Service URLs and Ports](#service-urls-and-ports)

---

## Prerequisites

Before starting, ensure you have:

- **Docker and Docker Compose** (Docker Desktop on macOS/Windows)
- **`make`** — on Windows it is provided inside WSL2 (see below)
- **`uv`** — https://docs.astral.sh/uv/getting-started/installation/ (runs the
  Python helpers; no separate Python install needed)
- **Node.js 18+ and npm**
- **Supabase CLI**, pinned to the version CI uses (currently **2.92.1**) so local
  `migrate-local` behaves identically to CI:
  - macOS: `brew install supabase/tap/supabase`
  - Linux/WSL2: download the matching release from
    https://github.com/supabase/cli/releases
  - check: `supabase --version`

### Windows: use WSL2

Bloom's stack is Linux containers, and the Postgres init scripts under
`volumes/db/` must be checked out with LF line endings. **Develop inside WSL2**
(Ubuntu) with Docker Desktop's WSL integration enabled, and **clone the repo into
the WSL2 Linux filesystem** (e.g. `~/repos/bloom`) — *not* under `/mnt/c/...`.
Cloning onto the Windows drive reintroduces filesystem/line-ending problems
(issue #124). Inside WSL2 every command below is identical to macOS/Linux.

---

## ⚙️ Environment Configuration

### Step 1: Generate `.env.dev`

The repo ships a committed, secret-free template at **`.env.dev.example`**.
Generate a working `.env.dev` from it with fresh local secrets:

```bash
make init        # writes .env.dev with generated secrets (FORCE=1 to overwrite)
```

`make init` (via `scripts/init_dev.py`) generates the database password and
encryption keys at the sizes each service needs, and mints `ANON_KEY`,
`SERVICE_ROLE_KEY`, and `BLOOM_AGENT_KEY` as JWTs **signed by the generated
`JWT_SECRET`** — these are not interchangeable random strings; the stack rejects
mismatched keys. `.env.dev` is git-ignored and never leaves your machine.

### Step 2: Optional — user-supplied keys and overrides

Only needed for specific features / conflicts:

- `OPENAI_API_KEY`, `LANGCHAIN_API_KEY` — set these if you need the LLM agent.
- **Port conflict:** if host port `5432` is already in use (commonly a
  WSL-relayed Postgres from another project), set a free port in `.env.dev`:
  ```bash
  POSTGRES_HOST_PORT=5433
  ```
  The stack, `make migrate-local`, and the integration tests all honour it.

> **Re-running `make init FORCE=1`:** moves your existing `.env.dev` to
> `.env.dev.backup` before writing a fresh one. Anything you added manually
> (the keys above) won't appear in the new file — `grep` the backup if you
> need to copy values back.

---

## MinIO Storage Setup

### Step 1: Create Storage Directory

```bash
# Create directory (adjust path to match MINIO_DATA_PATH)
mkdir -p ~/minio

# Set permissions
chmod 755 ~/minio
```

### Step 2: Verify Path Configuration

Ensure `MINIO_DATA_PATH` in `.env.dev` matches your created directory:

```bash
# Example for macOS/Linux
MINIO_DATA_PATH=/Users/yourusername/minio

# Or use full path
MINIO_DATA_PATH=$(pwd)/minio
```

---

## Starting the Stack

### Step 1: Start Development Stack

```bash
make dev-up
```

This command will:
- Install frontend dependencies (if needed)
- Build Docker images
- Start all services with hot reload
- Initialize MinIO buckets automatically
- Apply database migrations

First startup may take 5-10 minutes to download images and build.

### Step 2: Verify Services are Running

```bash
docker ps
```

You should see containers for:
- bloom-web-dev (Next.js with hot reload)
- db-dev (PostgreSQL)
- supabase-minio (MinIO storage)
- supabase-storage
- supabase-kong
- supabase-auth
- supabase-rest
- langchain-agent (LangChain AI agent)
- bloommcp (FastMCP Bloom analysis tools)

### Step 3: Access the Application

- Frontend: http://localhost:3000
- Supabase Studio: http://localhost:55323
- MinIO Console: http://localhost:9101

---

## Database Migrations

### Step 1: Verify Database is Ready

```bash
docker exec db-dev pg_isready -U supabase_admin
```

Output should be: `accepting connections`

### Step 2: Apply Migrations

Apply all database migrations:

```bash
make migrate-local
```

This command (Supabase CLI `db push --debug`, reading credentials and
`POSTGRES_HOST_PORT` from `.env.dev`) will:
- Apply every SQL file from `supabase/migrations/` in order
- Record applied migrations in `supabase_migrations.schema_migrations`
- Skip migrations that are already recorded (idempotent)

> Requires the `supabase` CLI on PATH (see Prerequisites). Pin the same version
> CI uses to avoid behaviour drift.

### Step 3: Verify Migrations Applied

Check that all tables were created:

```bash
docker exec db-dev psql -U supabase_admin -d postgres -c "\dt public.*"
```

You should see tables like:
- species
- phenotypers
- cyl_experiments
- cyl_scans
- cyl_images
- etc.

### Reset Database (if needed)

To reset the local DB/storage and reapply migrations from scratch:

```bash
# Clean reset -> up -> migrate -> health check, in one shot (destructive)
make verify-dev
```

Or step by step: `make reset-storage` (reset dev DB + storage) then
`make migrate-local`.

---

## Loading Test Data

### Quick Method

Load all test data in one go:

```bash
# Step 1: Load CSV data into database (14 tables)
make load-test-data

# Step 2: Upload sample images to MinIO (72 images)
make upload-images
```
---

## Service URLs and Ports

### Frontend & External Services

| Service | URL | Port | Description |
|---------|-----|------|-------------|
| Bloom Web | http://localhost:3000 | 3000 | Next.js frontend with hot reload |
| Supabase Studio | http://localhost:55323 | 55323 | Database management UI |
| LangChain Agent | http://localhost:5002 | 5002 | AI agent chat API |
| Bloom MCP Server | http://localhost:8811 | 8811 | FastMCP Bloom analysis tools |
| Swagger UI | http://localhost:8085 | 8085 | PostgREST API browser |
| MinIO Console | http://localhost:9101 | 9101 | Storage management console |
| Kong Gateway | http://localhost:8000 | 8000 | API Gateway |

### Internal Services (Docker Network)

| Service | Internal URL | Port | Description |
|---------|-------------|------|-------------|
| PostgreSQL | db-dev:5432 | 5432 | Database |
| MinIO API | supabase-minio:9000 | 9000 | S3 API |
| Storage API | storage:5000 | 5000 | Supabase Storage |
| Auth API | auth:9999 | 9999 | Authentication |
| REST API | rest:3000 | 3000 | PostgREST |
| Realtime | realtime:4000 | 4000 | Realtime subscriptions |

### API Endpoints

**Kong Gateway** (http://localhost:8000) routes to:

| Path | Routes To | Description |
|------|-----------|-------------|
| /auth/v1/* | auth:9999 | Authentication |
| /rest/v1/* | rest:3000 | Database API |
| /realtime/v1/* | realtime:4000 | Subscriptions |
| /storage/v1/* | storage:5000 | File storage |

**LangChain Agent** (http://localhost:5002):

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /chat | Send message to AI agent |
| GET | /health | Health check |

---

## AI Agent

### Verify Agent is Running

```bash
curl http://localhost:5002/health
```

The LangChain agent connects to the Bloom MCP server (port 8811) which provides 39 Bloom analysis tools for plant phenotyping data.

---

### Storage Management

**Create new bucket:**
```bash
# Private bucket
make create-bucket BUCKET=my-new-bucket

# Public bucket
make create-bucket BUCKET=public-data PUBLIC=true
```

**List all buckets:**
```bash
make list-buckets
```

**View via console:**
http://localhost:9101

### Logs and Debugging

---

### Database Connection Errors

```bash
# Check database is running
docker ps | grep db-dev

# Verify connection
docker exec db-dev pg_isready -U supabase_admin

# Check credentials in .env.dev
cat .env.dev | grep POSTGRES

# Restart database
docker restart db-dev
```

### MinIO/Storage Issues

```bash
# Check MinIO is running
docker ps | grep minio

# Verify path exists and is writable
ls -la ~/minio

# Check environment variable
cat .env.dev | grep MINIO_DATA_PATH

# Restart MinIO
docker restart supabase-minio

# Check logs
docker logs supabase-minio
```

### Frontend Won't Start

```bash
# Check for dependency issues
cd web
npm install

# Clear cache
rm -rf web/.next
rm -rf web/node_modules
cd web && npm install

# Rebuild
make rebuild-dev-fresh
```

### LangChain Agent / MCP Server errors

```bash
# Check agent logs
docker logs bloom_v2_dev-langchain-agent-1

# Check MCP server logs
docker logs bloom_v2_dev-bloommcp-1

# Restart services
docker restart bloom_v2_dev-langchain-agent-1
docker restart bloom_v2_dev-bloommcp-1

# Test agent health
curl http://localhost:5002/health
```

### Data Loading Fails

```bash
# Verify database is ready
docker exec db-dev pg_isready

# Check for table conflicts
docker exec db-dev psql -U supabase_admin -d postgres -c "\dt"

# Reset and try again
make reset-storage
make load-test-data
```

### Complete Reset

If nothing works, perform a complete reset:

```bash
# Stop all containers
make dev-down

# Remove all containers and volumes
docker compose -f docker-compose.dev.yml down -v

# Remove MinIO data
rm -rf ~/minio/*

# Start fresh
make dev-up
make load-test-data
make upload-images
```

---
### Automated Bucket Creation

On stack startup, these buckets are created automatically:

**Private Buckets:**
- images
- species_illustrations
- tus-files
- video
- scrna

**Public Buckets:**
- experiment-log-images
- plates-images
- plate-blob-storage

### MinIO Credentials

```bash
# From .env.dev
MINIO_ROOT_USER=<your-minio-user>
MINIO_ROOT_PASSWORD=<your-minio-password>
```

Use these to login at http://localhost:9101

---

## Next Steps

After successful setup:

1. Explore the frontend at http://localhost:3000
2. View database in Studio at http://localhost:55323
3. Test API endpoints with curl or Postman
4. Test AI agent at http://localhost:5002
5. Start developing your features
6. See [PROD_SETUP.md](./PROD_SETUP.md) for production deployment

---

# API Gateway
KONG_HTTP_PORT=8000
SITE_URL=http://localhost:3000

# MinIO
MINIO_ROOT_USER=<your-minio-user>
MINIO_ROOT_PASSWORD=<your-minio-password>
MINIO_DEFAULT_BUCKET=bloom-storage

# LangChain Agent
SUPABASE_URL=http://kong:8000
S3_ENDPOINT=http://supabase-minio:9000

---

### Resetting Dev Storage and Database (Destructive)

If you want to fully reset the development database and MinIO storage and re-run the initialization scripts, use the new Makefile helper. WARNING: This is destructive and will permanently delete dev data.

```bash
# Confirmed, destructive reset for DEVELOPMENT ONLY
make reset-storage
```

What the target does:
- Stops the dev compose stack.
- Attempts to detect the MinIO host path mounted in `docker-compose.dev.yml` and will prompt for confirmation before deleting all files in that directory.
- Removes docker volumes prefixed with `bloom_v2_dev_` (named dev volumes).
- Restarts the dev stack so you can re-run initialization.

Safety notes:
- The Make target prompts twice (first to proceed, then to type `delete` to actually remove files) to avoid accidents.
- This target is intended for development only. Don't run it against production.
- If the MinIO host path cannot be detected automatically, you'll be prompted to enter it manually (e.g. `/Users/benficaa/minio`).


**Last Updated:** March 2026
