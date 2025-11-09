---
name: Review & Update Documentation
description: Systematic workflow for reviewing and updating Bloom documentation
category: Documentation
tags: [docs, documentation, review, openspec]
---

# Review & Update Documentation

Systematic workflow for reviewing and updating Bloom documentation to ensure accuracy, completeness, and consistency across the monorepo.

## Quick Commands

### Documentation Discovery

```bash
# Find all markdown documentation
find . -type f -name "*.md" \
  ! -path "*/node_modules/*" ! -path "*/.venv/*" \
  ! -path "*/.next/*" ! -path "*/minio_data/*" | sort

# List OpenSpec proposals
openspec list

# Find Flask API endpoints
grep -n "@app.route" flask/app.py

# Find React pages and components
find web -name "*.tsx" -type f ! -path "*/node_modules/*" | head -20

# Check for TODO/FIXME in docs
grep -r "TODO\|FIXME\|TBD" --include="*.md" . | grep -v node_modules

# View recently modified docs (last 7 days)
find . -name "*.md" ! -path "*/node_modules/*" -mtime -7 -ls
```

### Documentation Validation

```bash
# Validate all OpenSpec proposals
openspec validate --strict

# Check Flask API routes
cd flask && uv run python -c "from app import app; print('\\n'.join(str(rule) for rule in app.url_map.iter_rules()))"

# Verify environment variables documented
diff <(grep -o "os.environ.get(['\"].*['\"])" flask/config.py | sort -u) \
     <(grep "^[A-Z_]*=" .env.dev | cut -d= -f1 | sort -u)

# Check Docker services match documentation
docker-compose -f docker-compose.dev.yml config --services
```

## Documentation Review Workflows

### Workflow 1: Quick Health Check

Identify which documentation exists and find gaps:

```bash
# Check core documentation
ls -lh README.md CLAUDE.md ARCHITECTURE.md DEVELOPMENT.md API.md TESTING.md DEPLOYMENT.md 2>/dev/null || echo "Some files missing"

# Check package documentation
ls -lh flask/README.md web/README.md packages/*/README.md 2>/dev/null || echo "Package docs missing"

# Check OpenSpec
ls -lh openspec/project.md openspec/AGENTS.md
openspec list

# Show last modified dates
find . -maxdepth 2 -name "*.md" -exec stat -f "%Sm %N" -t "%Y-%m-%d" {} \; | sort
```

**Checklist:**

- [ ] README.md exists and is up to date (<30 days)
- [ ] CLAUDE.md has Bloom-specific guidelines
- [ ] ARCHITECTURE.md exists (if not, create it)
- [ ] API.md or flask/README.md exists with endpoint documentation
- [ ] web/README.md has component and routing documentation
- [ ] DEVELOPMENT.md has complete setup instructions
- [ ] TESTING.md exists (after Phase 2 CI/CD)
- [ ] openspec/project.md is current
- [ ] All active proposals are valid (`openspec validate --strict`)

### Workflow 2: OpenSpec Proposal Review

Review active change proposals for completeness:

```bash
# List all active proposals
openspec list

# Validate all proposals
for proposal in openspec/changes/*/; do
  echo "Validating $(basename $proposal)..."
  openspec validate "$(basename $proposal)" --strict
done

# Check for proposals without design.md
find openspec/changes -mindepth 1 -maxdepth 1 -type d ! -exec test -e "{}/design.md" \; -print

# Check proposal task completion
grep -r "\- \[x\]" openspec/changes/*/tasks.md
grep -r "\- \[ \]" openspec/changes/*/tasks.md
```

**For each proposal in `openspec/changes/`:**

- [ ] **proposal.md complete**
  - [ ] Why section (1-2 sentences on problem/opportunity)
  - [ ] What Changes section (bullet list with **BREAKING** markers)
  - [ ] Impact section (affected specs, code, files)
- [ ] **tasks.md exists**
  - [ ] Implementation tasks with checkboxes
  - [ ] Tasks marked as complete when implemented
  - [ ] Clear, actionable task descriptions
- [ ] **design.md exists if needed**
  - [ ] Required for cross-cutting changes
  - [ ] Required for new dependencies
  - [ ] Required for security/performance/migration complexity
  - [ ] Technical decisions documented
- [ ] **Spec deltas valid**
  - [ ] At least one delta file in specs/
  - [ ] Proper delta operations (ADDED/MODIFIED/REMOVED)
  - [ ] Each requirement has ≥1 scenario
  - [ ] Scenarios use `####` header format
  - [ ] Passes `openspec validate --strict`
- [ ] **Implementation status tracked**
  - [ ] tasks.md updated as work progresses
  - [ ] Blockers documented in proposal.md
  - [ ] Ready for `openspec archive` when complete

### Workflow 3: Flask API Documentation Review

Ensure Flask API endpoints are documented:

```bash
# Extract all Flask routes
cd flask && uv run python -c "
from app import app
for rule in app.url_map.iter_rules():
    methods = ','.join(sorted(rule.methods - {'HEAD', 'OPTIONS'}))
    print(f'{methods:6} {rule.rule}')
"

# Count undocumented endpoints
# (compare routes in app.py with documented endpoints in API.md)
grep "@app.route" flask/app.py | wc -l
```

**Flask API Documentation Checklist:**

- [ ] **API.md or flask/README.md exists** with comprehensive endpoint documentation
- [ ] Each endpoint documented with:
  - [ ] HTTP method and path
  - [ ] Request parameters (query, body, headers)
  - [ ] Request body schema (JSON examples)
  - [ ] Response schema (success and error cases)
  - [ ] Authentication requirements (JWT or public)
  - [ ] Example curl commands
  - [ ] Error codes and meanings (400, 401, 404, 500)
- [ ] **VideoWriter class documented**
  - [ ] Constructor parameters (`filename`, `fps`)
  - [ ] `add()` method usage with numpy arrays
  - [ ] `_open()` internal workflow
  - [ ] `close()` finalization and error handling
  - [ ] ffmpeg dependencies and installation
  - [ ] Decimation factor explanation
- [ ] **S3/MinIO integration documented**
  - [ ] boto3 configuration (credentials, endpoint)
  - [ ] Bucket names and structure
  - [ ] Presigned URL generation
  - [ ] Error handling and retries
- [ ] **Authentication flow documented**
  - [ ] JWT token validation process
  - [ ] Supabase Auth integration
  - [ ] Protected endpoint examples
  - [ ] Token expiration handling

### Workflow 4: Infrastructure Documentation Review

Verify Docker, nginx, and database documentation:

```bash
# List all Docker services
docker-compose -f docker-compose.dev.yml config --services
docker-compose -f docker-compose.prod.yml config --services

# Check nginx configuration
cat nginx/nginx.conf.template

# List environment variables
grep "^[A-Z_]*=" .env.dev | cut -d= -f1 | sort

# Check Supabase migrations
ls -lh supabase/migrations/
```

**Infrastructure Documentation Checklist:**

- [ ] **docker-compose.dev.yml documented**
  - [ ] Service descriptions (Flask, Web, Supabase, MinIO, nginx, Kong)
  - [ ] Port mappings explained
  - [ ] Volume mounts documented (minio_data/, volumes/)
  - [ ] Environment variables listed
  - [ ] Health checks explained
- [ ] **docker-compose.prod.yml documented**
  - [ ] Differences from dev explained
  - [ ] Multi-stage build process
  - [ ] Production optimizations noted
  - [ ] Security considerations
- [ ] **nginx configuration documented**
  - [ ] Reverse proxy setup
  - [ ] Subpath routing (Supabase at `/supabase_kong/`)
  - [ ] Frontend and API routing
  - [ ] CORS settings
- [ ] **MinIO setup documented**
  - [ ] Directory creation (`minio_data/`)
  - [ ] Permissions (gitignored)
  - [ ] Bucket initialization
  - [ ] Policy configuration (public vs private)
- [ ] **Supabase configuration documented**
  - [ ] Self-hosted setup
  - [ ] Migration workflow (`supabase db push`)
  - [ ] RLS policy guidelines
  - [ ] Studio UI access (port 55323)
  - [ ] Subpath deployment configuration

### Workflow 5: Next.js Frontend Documentation Review

Ensure web app structure and components are documented:

```bash
# List pages
find web/app -name "page.tsx" -o -name "layout.tsx"

# List components
find web/components -name "*.tsx" | head -20

# Check environment variables
grep "process.env" web -r --include="*.ts" --include="*.tsx" | cut -d: -f2 | sort -u

# List Material-UI usage
grep "@mui/material" web -r --include="*.tsx" | cut -d: -f1 | sort -u | head -10
```

**Next.js Frontend Documentation Checklist:**

- [ ] **web/README.md exists** with comprehensive guide
- [ ] **Architecture documented**
  - [ ] App Router structure (Next.js 13+ app/ directory)
  - [ ] Component organization (components/, utils/, lib/)
  - [ ] State management (hooks, context, or state library)
  - [ ] API integration (fetch, axios, SWR, or similar)
- [ ] **Authentication flow documented**
  - [ ] Supabase Auth integration
  - [ ] Protected routes and middleware
  - [ ] Session management
  - [ ] JWT handling with Flask API
- [ ] **Material-UI usage documented**
  - [ ] Theme configuration
  - [ ] Custom components
  - [ ] Styling patterns (sx prop vs styled vs CSS modules)
- [ ] **Flask API client documented**
  - [ ] API base URL configuration
  - [ ] Type definitions for requests/responses
  - [ ] Error handling patterns
  - [ ] Authentication header injection
- [ ] **Environment variables documented**
  - [ ] Required variables (`.env.dev`, `.env.prod`)
  - [ ] `NEXT_PUBLIC_` variables explained
  - [ ] API endpoints and URLs
  - [ ] Supabase connection strings

## Documentation Templates

### API.md Template (Flask)

Use this template to document Flask API endpoints:

````markdown
# Bloom Flask API Documentation

Base URL: `http://localhost:5002` (dev) | `https://api.bloom.example.com` (prod)

## Authentication

All protected endpoints require a JWT token in the Authorization header:

\```
Authorization: Bearer <jwt_token>
\```

Get a token from Supabase Auth after user login.

## Endpoints

### GET /

**Health Check**

Returns the status of the Flask application.

**Authentication:** Not required

**Response:**
\```json
{
"message": "Flask app is running!"
}
\```

**Status Codes:**

- 200: Success

---

### POST /generate_video

**Generate Video from Scan Images**

Generates an MP4 video from a sequence of cylindrical scan images.

**Authentication:** Required (JWT)

**Request Body:**
\```json
{
"scan_id": 123
}
\```

**Parameters:**

- `scan_id` (integer, required): ID of the scan in database

**Response (Success):**
\```json
{
"message": "Video generated successfully",
"scan_id": 123,
"total_frames": 72,
"download_url": "https://minio.example.com/videos/scan_123.mp4?signature=..."
}
\```

**Response (Error):**
\```json
{
"error": "Scan not found"
}
\```

**Status Codes:**

- 200: Success
- 400: Bad request (invalid parameters)
- 401: Unauthorized (missing or invalid JWT)
- 404: Scan not found
- 500: Server error (video generation failed)

**Example:**
\```bash
curl -X POST http://localhost:5002/generate_video \\
-H "Authorization: Bearer <jwt_token>" \\
-H "Content-Type: application/json" \\
-d '{"scan_id": 123}'
\```

---

### POST /get_presigned_urls

**Get Presigned URLs for S3 Objects**

Generates presigned URLs for accessing S3/MinIO objects.

**Authentication:** Required (JWT)

**Request Body:**
\```json
{
"object_paths": [
"images/scan_123/frame_001.png",
"images/scan_123/frame_002.png"
]
}
\```

**Parameters:**

- `object_paths` (array of strings, required): S3 object paths

**Response:**
\```json
{
"presigned_urls": [
"https://minio.example.com/bloom-storage/images/scan_123/frame_001.png?signature=...",
"https://minio.example.com/bloom-storage/images/scan_123/frame_002.png?signature=..."
],
"invalid_urls": [false, false],
"skipped_paths": []
}
\```

**Status Codes:**

- 200: Success
- 401: Unauthorized

## Video Generation

### VideoWriter Class

The `VideoWriter` class handles video generation from image sequences using ffmpeg.

**Usage:**
\```python
from videoWriter import VideoWriter
import numpy as np

writer = VideoWriter(filename="output.mp4", fps=30.0)

# Add frames (numpy arrays)

for image_array in images:
writer.add(image_array) # numpy.ndarray, uint8 or float32/64

writer.close()
\```

**Parameters:**

- `filename` (str): Output video file path
- `fps` (float): Frames per second (default: 30.0)

**Methods:**

- `add(img)`: Add a frame (numpy array, uint8 or float32/64)
- `close()`: Finalize video and close ffmpeg subprocess

**Dependencies:**

- ffmpeg must be installed on the system
- Uses H.264 codec with MP4 container
- CRF 20 for high quality

**Error Handling:**

- Raises `RuntimeError` if ffmpeg fails (exit code != 0)
- Raises `RuntimeError` if ffmpeg times out (>30 seconds)
- Validates dimensions and fps before starting

## Environment Variables

Required environment variables (`.env.dev` or `.env.prod`):

- `SUPABASE_URL`: Supabase instance URL
- `SUPABASE_KEY`: Supabase anon or service key
- `JWT_SECRET`: Secret for JWT validation (must match Supabase)
- `AWS_REGION`: AWS region (use `us-east-1` for MinIO)
- `S3_BUCKET_NAME`: Default S3 bucket name
- `S3_ENDPOINT`: MinIO endpoint URL (e.g., `http://minio:9100`)
- `AWS_ACCESS_KEY_ID`: S3/MinIO access key
- `AWS_SECRET_ACCESS_KEY`: S3/MinIO secret key

## Development

**Local Testing:**
\```bash

# Start Flask dev server

cd flask && uv run flask run --host=0.0.0.0 --port=5002

# Test health check

curl http://localhost:5002/

# Get JWT token from Supabase Auth (via web UI or API)

# Then test protected endpoint:

curl -X POST http://localhost:5002/generate_video \\
-H "Authorization: Bearer <token>" \\
-H "Content-Type: application/json" \\
-d '{"scan_id": 1}'
\```

**Error Codes:**

- 400: Bad Request (invalid input)
- 401: Unauthorized (missing/invalid JWT)
- 403: Forbidden (valid JWT but insufficient permissions)
- 404: Not Found (resource doesn't exist)
- 500: Internal Server Error (server-side failure)
````

### ARCHITECTURE.md Template

````markdown
# Bloom Architecture

## System Overview

Bloom is a full-stack application for biological/scientific data visualization, specifically designed for cylindrical scan image management and video generation for plant research.

## Technology Stack

- **Frontend**: Next.js 16, React 19, TypeScript, Material-UI
- **Backend API**: Flask (Python 3.11) with uv package manager
- **Database**: PostgreSQL (via self-hosted Supabase)
- **Object Storage**: MinIO (S3-compatible)
- **Authentication**: Supabase Auth (JWT tokens)
- **Reverse Proxy**: nginx (production), Kong (Supabase API Gateway)
- **Containerization**: Docker Compose
- **Monorepo**: Turborepo with pnpm workspaces

## Service Architecture

\```
[Browser]
↓
[nginx:80] (production) or direct (development)
├─→ [Next.js:3000]
│ ↓ (API calls)
├─→ [Flask:5002] ──→ [MinIO:9100] (S3 operations)
│ ↓ (database queries)
└─→ [Kong:8000] (Supabase API Gateway)
├─→ [PostgreSQL:5432]
├─→ [PostgREST] (REST API)
├─→ [GoTrue] (Auth)
├─→ [Storage]
└─→ [Realtime]
\```

### Service Details

#### Next.js Web App (port 3000)

- React-based UI for scan visualization
- Server-side rendering (SSR) with App Router
- Material-UI components
- Supabase Auth integration
- API client for Flask endpoints

#### Flask API (port 5002)

- RESTful API for video generation
- S3/MinIO integration for image storage
- JWT authentication (validates Supabase tokens)
- VideoWriter class (ffmpeg wrapper for video encoding)
- Logging with Python logging module

#### Supabase Stack (Kong on port 8000)

- **PostgreSQL** (port 5432): Main database
- **PostgREST**: Auto-generated REST API
- **GoTrue**: Authentication service
- **Storage**: File upload and management
- **Realtime**: WebSocket subscriptions
- **Studio UI** (port 55323): Admin dashboard

#### MinIO (ports 9100-9101)

- S3-compatible object storage
- Stores scan images and generated videos
- Bucket: `bloom-storage`
- Console UI on port 9101 (admin interface)

#### nginx (production only, port 80)

- Reverse proxy for all services
- Subpath routing:
  - `/` → Next.js frontend
  - `/api/` → Flask backend (future)
  - `/supabase_kong/` → Supabase services
- Static file serving

## Data Flow

### 1. Scan Data Upload

\```
[Researcher] → [Web UI] → [Supabase Storage/API] → [PostgreSQL: cyl_images table]
↓
[MinIO: S3 storage]
\```

### 2. Video Generation Workflow

\```
[Researcher clicks "Generate Video"]
↓
[Web UI] → [Flask API /generate_video]
↓
[Fetch scan + images from PostgreSQL]
↓
[Download images from MinIO/S3]
↓
[VideoWriter: decimate frames (every 4th), encode with ffmpeg]
↓
[Upload MP4 to MinIO]
↓
[Generate presigned URL for download]
↓
[Return URL to Web UI]
\```

### 3. Authentication Flow

\```
[User Login] → [Web UI] → [Supabase Auth (GoTrue)]
↓
[JWT Token returned to client]
↓
[Client includes token in Flask API requests]
↓
[Flask validates JWT signature with JWT_SECRET]
\```

## Database Schema

### Key Tables

**cyl_scanners**

- Scanner metadata (name, location, settings)
- One scanner → many scans

**cyl_scans**

- Scan session data (timestamp, scanner_id, experiment_id)
- Links scans to images and experiments

**cyl_images**

- Individual scan frame images
- Fields: scan_id, frame_number, object_path (S3 key), metadata
- S3 key pattern: `images/{scan_id}/frame_{number}.png`

**cyl_experiments**

- Experiment organization
- Groups multiple scans

**Row Level Security (RLS):**

- All tables have RLS policies
- Users can only access their own data
- Service role key bypasses RLS for admin operations

## Environment Configuration

### Development (.env.dev)

- Local Docker Compose setup
- MinIO at `http://localhost:9100`
- Supabase at `http://localhost:8000`
- No nginx (direct service access)
- Hot reload enabled for Flask and Next.js

### Production (.env.prod)

- Multi-stage Docker builds (smaller images)
- nginx reverse proxy with subpath routing
- External domain names
- Environment-specific secrets
- No debug mode

## Video Processing

### VideoWriter Class (`flask/videoWriter.py`)

**Process:**

1. Open ffmpeg subprocess with pipe stdin
2. Accept numpy arrays (RGB images)
3. Convert to bytes and pipe to ffmpeg
4. Encode as H.264, MP4 container, CRF 20
5. Close subprocess and validate output

**Decimation:**

- Default factor: 4 (every 4th frame)
- Reduces file size and processing time
- Configurable per request (future)

**Performance:**

- CPU-intensive (ffmpeg encoding)
- ~2-5 seconds for 100 frames
- Depends on image resolution and frame count

## Security

### Authentication

- Supabase Auth (email/password, OAuth providers)
- JWT tokens (HS256 algorithm)
- Token validation on Flask protected endpoints
- Generic error messages (no information leakage)

### Access Control

- Row Level Security (RLS) on all tables
- User-scoped queries in PostgreSQL
- S3 bucket policies (private by default)
- Presigned URLs for temporary access (1 hour TTL)

### Secrets Management

- Environment variables in `.env` files
- `.env` files gitignored
- Never hardcode secrets
- Use `get_required_env()` for validation

## Development Workflow

### Local Setup

\```bash

# 1. Clone repository

git clone <repo-url>
cd bloom

# 2. Create MinIO data directory

mkdir -p minio_data
chmod 777 minio_data

# 3. Copy and configure environment

cp .env.dev.example .env.dev

# Edit .env.dev with your values

# 4. Start all services

make dev-up

# 5. Initialize database

cd supabase && supabase db push

# 6. Load test data

cd web && pnpm db:init:dev
\```

### Daily Development

\```bash

# Start services

make dev-up

# View logs

make dev-logs

# Stop services

make dev-down

# Rebuild after dependency changes

make rebuild-dev-fresh
\```

## Monitoring & Debugging

### Logs

\```bash

# All services

docker-compose -f docker-compose.dev.yml logs -f

# Flask only

docker-compose -f docker-compose.dev.yml logs -f flask-app

# Next.js only

docker-compose -f docker-compose.dev.yml logs -f web
\```

### Health Checks

- Flask: `curl http://localhost:5002/`
- Supabase: `curl http://localhost:8000/rest/v1/`
- MinIO: Browse to `http://localhost:9101`
- Next.js: Browse to `http://localhost:3000`

### Database

- Supabase Studio: `http://localhost:55323`
- Direct PostgreSQL: `psql -h localhost -p 5432 -U postgres -d postgres`

## Performance Considerations

- **Video generation**: CPU-intensive, consider queue (Celery) for production
- **Large scans**: Pagination for >1000 images
- **S3 presigned URLs**: 1-hour expiration (configurable)
- **Database**: Connection pooling via Supabase
- **Frontend**: Server-side rendering for initial page load

## Deployment

### Production Checklist

- [ ] Update environment variables in `.env.prod`
- [ ] Build production images: `make rebuild-prod-fresh`
- [ ] Configure nginx SSL/TLS certificates
- [ ] Set up domain DNS records
- [ ] Configure Supabase production instance
- [ ] Initialize MinIO buckets and policies
- [ ] Run database migrations: `supabase db push`
- [ ] Test all endpoints with production URLs
- [ ] Set up backup strategy (PostgreSQL, MinIO)
- [ ] Configure monitoring and alerting

## Future Enhancements

- [ ] Redis caching for frequently accessed data
- [ ] Celery for async video generation (background jobs)
- [ ] CDN for static assets and videos
- [ ] Horizontal scaling for Flask API (load balancer)
- [ ] WebSocket updates for video generation progress
- [ ] Advanced video options (resolution, FPS, codec)
- [ ] Bulk video generation
- [ ] Video thumbnails and previews
````

### DEVELOPMENT.md Template

````markdown
# Bloom Development Guide

Complete guide for setting up and developing the Bloom application locally.

## Prerequisites

### Required Software

- **Docker Desktop** (v24+)

  - [Download for Mac](https://www.docker.com/products/docker-desktop)
  - [Download for Windows](https://www.docker.com/products/docker-desktop)
  - [Install for Linux](https://docs.docker.com/engine/install/)

- **Node.js** (v20+) and **pnpm** (v9+)
  \```bash

  # Install pnpm

  npm install -g pnpm
  \```

- **Python** (3.11+) and **uv**
  \```bash

  # Install uv

  curl -LsSf https://astral.sh/uv/install.sh | sh
  \```

- **make** (usually pre-installed on Mac/Linux)
  \```bash
  # Check if installed
  make --version
  \```

### Optional Tools

- **Supabase CLI** (for migrations)
  \```bash
  brew install supabase/tap/supabase
  \```

- **PostgreSQL client** (for direct database access)
  \```bash
  brew install postgresql
  \```

## Initial Setup

### 1. Clone Repository

\```bash
git clone <repo-url>
cd bloom
\```

### 2. Create MinIO Data Directory

MinIO requires a local directory for object storage:

\```bash
mkdir -p minio_data
chmod 777 minio_data # Allow Docker to write
\```

**Note**: `minio_data/` is gitignored. Do not commit it.

### 3. Configure Environment Variables

Copy the development environment template:

\```bash
cp .env.dev.example .env.dev
\```

Edit `.env.dev` and configure:

\```bash

# Supabase

SUPABASE_URL=http://localhost:8000
SUPABASE_KEY=<anon-key-from-supabase>
JWT_SECRET=<jwt-secret-from-supabase>

# MinIO (S3)

AWS_REGION=us-east-1
S3_BUCKET_NAME=bloom-storage
S3_ENDPOINT=http://minio:9100
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin

# Next.js

NEXT_PUBLIC_SUPABASE_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_ANON_KEY=<same-as-SUPABASE_KEY>
\```

**Where to find Supabase keys:**

1. Start Supabase: `make dev-up`
2. Check logs: `docker-compose -f docker-compose.dev.yml logs supabase-kong`
3. Look for `anon key:` and `service_role key:`

### 4. Install Dependencies

\```bash

# Install all monorepo dependencies

pnpm install

# Install Flask dependencies

cd flask && uv sync --all-extras
\```

### 5. Start Services

\```bash

# Start all Docker services

make dev-up

# Wait for services to be healthy (~30 seconds)

docker-compose -f docker-compose.dev.yml ps
\```

### 6. Initialize Database

\```bash

# Run Supabase migrations

cd supabase
supabase db push

# Load test data (optional)

cd ../web
pnpm db:init:dev
\```

### 7. Verify Setup

\```bash

# Test Flask

curl http://localhost:5002/

# Test Supabase

curl http://localhost:8000/rest/v1/

# Test MinIO

open http://localhost:9101 # Login: minioadmin / minioadmin

# Test Next.js

open http://localhost:3000
\```

## Development Workflow

### Starting Development

\```bash

# Start all services

make dev-up

# View logs (all services)

make dev-logs

# View logs (specific service)

docker-compose -f docker-compose.dev.yml logs -f flask-app
docker-compose -f docker-compose.dev.yml logs -f web
\```

### Making Code Changes

**Flask (Python):**

- Edit files in `flask/`
- Flask auto-reloads on file changes (debug mode)
- Install new dependencies: `cd flask && uv add <package>`
- Run tests: `cd flask && uv run pytest`

**Next.js (TypeScript):**

- Edit files in `web/`
- Next.js auto-reloads via Fast Refresh
- Install new dependencies: `pnpm add <package>`
- Run linting: `pnpm lint`

**Database:**

- Edit schemas in `supabase/migrations/`
- Apply migrations: `cd supabase && supabase db push`
- Create new migration: `supabase migration new <name>`

### Stopping Services

\```bash

# Stop all services (keep data)

make dev-down

# Stop and remove all data

docker-compose -f docker-compose.dev.yml down -v
\```

## Common Tasks

### Reset Database

\```bash

# Stop services

make dev-down

# Remove database volume

docker volume rm bloom_db-data

# Restart and re-initialize

make dev-up
cd supabase && supabase db push
cd ../web && pnpm db:init:dev
\```

### Clear MinIO Storage

\```bash

# Stop services

make dev-down

# Remove MinIO data

rm -rf minio_data/\*

# Restart (bucket will be recreated)

make dev-up
\```

### Rebuild Services

\```bash

# Rebuild all Docker images

make rebuild-dev-fresh

# Rebuild specific service

docker-compose -f docker-compose.dev.yml build flask-app
docker-compose -f docker-compose.dev.yml up -d flask-app
\```

### Run Tests

\```bash

# Python tests (Phase 2)

cd flask && uv run pytest --cov

# TypeScript tests (future)

pnpm test

# E2E tests (future)

pnpm test:e2e
\```

### Format and Lint Code

\```bash

# Python

cd flask
uv run black .
uv run ruff check --fix .
uv run mypy .

# TypeScript/JavaScript

pnpm format
pnpm lint:fix

# Run all pre-commit hooks

cd flask && uv run pre-commit run --all-files
\```

## Troubleshooting

### Issue: Port Already in Use

**Symptom**: `Error: port is already allocated`

**Solution**:
\```bash

# Find process using port (e.g., 5002)

lsof -ti:5002

# Kill process

kill -9 <PID>

# Or change port in docker-compose.dev.yml

\```

### Issue: MinIO Permission Denied

**Symptom**: `mkdir: cannot create directory '/data': Permission denied`

**Solution**:
\```bash

# Ensure minio_data/ has correct permissions

chmod 777 minio_data

# Or recreate directory

rm -rf minio_data
mkdir minio_data
chmod 777 minio_data
\```

### Issue: Supabase Auth Not Working

**Symptom**: JWT validation fails in Flask

**Solution**:

1. Check JWT_SECRET matches Supabase secret
2. Get correct secret from Supabase logs:
   \```bash
   docker-compose -f docker-compose.dev.yml logs supabase-kong | grep "JWT secret"
   \```
3. Update `.env.dev` with correct secret
4. Restart Flask: `docker-compose -f docker-compose.dev.yml restart flask-app`

### Issue: Database Connection Error

**Symptom**: `could not connect to server: Connection refused`

**Solution**:
\```bash

# Check if PostgreSQL is running

docker-compose -f docker-compose.dev.yml ps supabase-db

# Check logs

docker-compose -f docker-compose.dev.yml logs supabase-db

# Restart database

docker-compose -f docker-compose.dev.yml restart supabase-db
\```

### Issue: Flask Not Reloading

**Symptom**: Code changes not reflected

**Solution**:

1. Check if Flask debug mode is enabled
2. Restart Flask container:
   \```bash
   docker-compose -f docker-compose.dev.yml restart flask-app
   \```
3. Check logs for errors:
   \```bash
   docker-compose -f docker-compose.dev.yml logs -f flask-app
   \```

### Issue: Next.js Build Errors

**Symptom**: TypeScript errors or missing modules

**Solution**:
\```bash

# Clear Next.js cache

rm -rf web/.next

# Reinstall dependencies

pnpm install

# Restart service

docker-compose -f docker-compose.dev.yml restart web
\```

## Git Workflow

### Branch Strategy

- `main`: Production-ready code
- `feature/*`: New features
- `fix/*`: Bug fixes
- `docs/*`: Documentation updates

### Commit Messages

Follow Conventional Commits:

\```bash

# Format

<type>(<scope>): <subject>

# Examples

feat(flask): add video generation endpoint
fix(web): correct authentication flow
docs(readme): update setup instructions
chore(deps): upgrade Next.js to v16
\```

### Pre-commit Hooks

Install pre-commit hooks:

\```bash
cd flask && uv run pre-commit install
\```

Hooks run automatically on `git commit`:

- Trim trailing whitespace
- Fix end of files
- Check YAML/TOML
- Black formatting
- Ruff linting
- mypy type checking
- Prettier formatting

## Editor Setup

### VS Code

Recommended extensions:

- Python (Microsoft)
- Pylance
- ESLint
- Prettier
- Docker
- PostgreSQL

Settings (`.vscode/settings.json`):
\```json
{
"python.linting.enabled": true,
"python.linting.mypyEnabled": true,
"python.formatting.provider": "black",
"editor.formatOnSave": true,
"editor.codeActionsOnSave": {
"source.fixAll.eslint": true
}
}
\```

## Resources

- [Flask Documentation](https://flask.palletsprojects.com/)
- [Next.js Documentation](https://nextjs.org/docs)
- [Supabase Documentation](https://supabase.com/docs)
- [MinIO Documentation](https://min.io/docs/)
- [Turborepo Documentation](https://turbo.build/repo)
- [uv Documentation](https://docs.astral.sh/uv/)
````

## Documentation Review Checklist

### Core Documentation

- [ ] **README.md** - Project overview, quick start, contributing
- [ ] **CLAUDE.md** - AI assistant instructions and project context
- [ ] **ARCHITECTURE.md** - System design, service architecture
- [ ] **DEVELOPMENT.md** - Local setup, development workflow
- [ ] **API.md** or **flask/README.md** - Flask API endpoints
- [ ] **TESTING.md** - Test strategy, running tests (Phase 2+)
- [ ] **DEPLOYMENT.md** - Production deployment guide

### Package Documentation

- [ ] **flask/README.md** - Flask app, API endpoints, VideoWriter
- [ ] **web/README.md** - Next.js app, components, routing
- [ ] **packages/\*/README.md** - Any shared packages

### OpenSpec Documentation

- [ ] **openspec/project.md** - Project context and conventions
- [ ] **openspec/AGENTS.md** - OpenSpec workflow guide
- [ ] **openspec/changes/\*/proposal.md** - All active proposals
- [ ] **openspec/changes/\*/tasks.md** - Task tracking
- [ ] **openspec/changes/\*/design.md** - Design docs (if complex)

### Infrastructure Documentation

- [ ] **docker-compose.dev.yml** - Development services
- [ ] **docker-compose.prod.yml** - Production configuration
- [ ] **nginx/nginx.conf.template** - Reverse proxy config
- [ ] **.env.dev.example** - Environment variable template
- [ ] **Makefile** - Common commands documented

## Common Documentation Issues

### Issue 1: Outdated Setup Instructions

**Symptom**: New developers can't get started following README

**Fix**:

1. Test setup on clean environment (or ask colleague)
2. Update step-by-step instructions
3. Add troubleshooting section for common errors
4. Update prerequisites and versions

### Issue 2: Missing New Features

**Symptom**: New endpoints or components not documented

**Fix**:

1. Add feature to API.md or relevant README
2. Update OpenSpec proposal tasks.md
3. Add usage examples
4. Update ARCHITECTURE.md if needed

### Issue 3: Broken Code Examples

**Symptom**: curl commands or code snippets don't work

**Fix**:

1. Test each code example
2. Update to current API format
3. Add comments explaining key parts
4. Verify environment variables are correct

### Issue 4: Outdated Environment Variables

**Symptom**: Missing or wrong env vars in .env.dev.example

**Fix**:
\```bash

# Extract all env vars from code

grep -r "os.environ.get\|process.env" flask/ web/ --include="_.py" --include="_.ts" --include="\*.tsx"

# Compare with .env.dev.example

# Add missing variables

# Remove unused variables

\```

## When Documentation is Complete

Documentation is complete when:

- [ ] New developer can set up environment without asking questions
- [ ] All code examples work when copy-pasted
- [ ] Common tasks are documented (setup, dev, deploy)
- [ ] Breaking changes are noted in relevant docs
- [ ] All links work (no 404s)
- [ ] No TODO/TBD/FIXME in documentation
- [ ] Spelling and grammar are correct
- [ ] Structure is clear and logical
- [ ] OpenSpec proposals all pass `openspec validate --strict`

## Related Commands

- `/lint` - Check code and documentation style
- `/review-pr` - PR review includes documentation check
- `/coverage` - Test coverage analysis (Phase 2+)
- `/openspec/proposal` - Create documentation for new features
- `/openspec/apply` - Implement approved changes
- `/openspec/archive` - Archive completed changes
