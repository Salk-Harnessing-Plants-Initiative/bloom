# Development Environment Setup & Configuration

This guide covers setting up Bloom for local development

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

- Docker and Docker Compose installed
- Python 3.8 or higher
- Node.js 18+ and npm
---

## ⚙️ Environment Configuration

### Step 1: Get Environment Template

Get the development environment template from:
**[Notion: Environment Configuration](https://www.notion.so/Plan-2734a67a766780e89373c1e1ec687a4d)**

Copy the `.env.dev` template content and create the file:

```bash
# Create .env.dev file
nano .env.dev
# Or use your preferred editor
```

### Step 2: Configure Environment Variables

Edit `.env.dev` and update the following:

**MinIO Storage Path** (REQUIRED):
```bash
MINIO_DATA_PATH=/Users/yourusername/minio
```
Replace `yourusername` with your actual username or preferred path.

**Other Values** (can remain as defaults for local dev):
```bash
# Database (defaults are fine for development)
POSTGRES_PASSWORD=postgres
POSTGRES_USER=supabase_admin

# MinIO (defaults are fine for development)
MINIO_ROOT_USER=supabase
MINIO_ROOT_PASSWORD=supabase123

# JWT Keys (defaults provided in template work for dev)
JWT_SECRET=super-secret-jwt-token-with-at-least-32-characters-long
ANON_KEY=eyJhbGci...
SERVICE_ROLE_KEY=eyJhbGci...
```

### Step 3: Set File Permissions

```bash
chmod 600 .env.dev
```

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
- flask-app

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
make apply-migrations
```

This command will:
- Apply all SQL files from `supabase/migrations/` in order
- Skip tables/policies that already exist
- Show progress for each migration file

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

To drop all tables and reapply migrations:

```bash
# Drop all tables
make drop-tables

# Reapply migrations
make apply-migrations
```

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
| Flask API | http://localhost:5002 | 5002 | Video generation API |
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

**Flask API** (http://localhost:5002):

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | / | API documentation |
| GET | /test | Health check |
| GET | /list_buckets | List storage buckets |
| POST | /generate_video | Generate video from scan |
| POST | /get_presigned_urls | Get presigned URLs |

---

## Video Generation

### Step 1: Verify Data is Loaded

Ensure you have loaded test data:

```bash
make load-test-data
make upload-images
```

### Step 2: Generate Video

```bash
curl -X POST http://localhost:5002/generate_video \
  -H "Content-Type: application/json" \
  -d '{"scan_id": 1}'
```

Response should include:
```json
{
  "message": "Video generated successfully",
  "scan_id": 1,
  "total_frames": 72,
  "download_url": "http://..."
}
```

### Step 3: Verify Video in Storage

Using MinIO Console:
1. Open http://localhost:9101
2. Login with credentials from `.env.dev`
3. Navigate to `video` bucket
4. Find `1.mp4` file

Or via command line:
```bash
make list-buckets
```

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

### Flask API Errors

```bash
# Check Flask logs
docker logs flask-app

# Verify Python dependencies
docker exec flask-app pip list

# Restart Flask
docker restart flask-app

# Test connection
curl http://localhost:5002/test
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
MINIO_ROOT_USER=supabase
MINIO_ROOT_PASSWORD=supabase123
```

Use these to login at http://localhost:9101

---

## Next Steps

After successful setup:

1. Explore the frontend at http://localhost:3000
2. View database in Studio at http://localhost:55323
3. Test API endpoints with curl or Postman
4. Generate a test video with Flask API
5. Start developing your features
6. See [PROD_SETUP.md](./PROD_SETUP.md)/[PROD_SETUP.html](./PROD_SETUP.html) for production deployment

---

# API Gateway
KONG_HTTP_PORT=8000
SITE_URL=http://localhost:3000

# MinIO
MINIO_ROOT_USER=supabase
MINIO_ROOT_PASSWORD=supabase123
MINIO_DEFAULT_BUCKET=bloom-storage

# Flask
FLASK_SUPABASE_URL=http://kong:8000
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


**Last Updated:** November 17, 2025
