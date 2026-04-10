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

# Find FastAPI routes in langchain service
grep -rn "router\." langchain/

# Find FastAPI routes in bloommcp service
grep -rn "router\." bloommcp/

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

# Check FastAPI routes in langchain service
cd langchain && uv run python -c "from app import app; import uvicorn; print('routes ok')"

# Verify environment variables documented
diff <(grep -o "os.environ.get(['\"].*['\"])" langchain/config.py | sort -u) \
     <(grep "^[A-Z_]*=" .env.dev | cut -d= -f1 | sort -u)

# Check Docker services match documentation
docker compose -f docker-compose.dev.yml config --services
```

## Documentation Review Workflows

### Workflow 1: Quick Health Check

Identify which documentation exists and find gaps:

```bash
# Check core documentation
ls -lh README.md CLAUDE.md ARCHITECTURE.md DEVELOPMENT.md API.md TESTING.md DEPLOYMENT.md 2>/dev/null || echo "Some files missing"

# Check package documentation
ls -lh langchain/README.md bloommcp/README.md web/README.md packages/*/README.md 2>/dev/null || echo "Package docs missing"

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
- [ ] API.md or langchain/README.md exists with endpoint documentation
- [ ] web/README.md has component and routing documentation
- [ ] DEVELOPMENT.md has complete setup instructions
- [ ] TESTING.md exists with integration test guidance
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

### Workflow 3: Python Services Documentation Review

Ensure the LangGraph agent (`langchain/`) and FastMCP server (`bloommcp/`) endpoints are documented:

```bash
# List FastAPI routes in langchain service
cd langchain && uv run python -c "
from app import app
for route in app.routes:
    if hasattr(route, 'methods'):
        methods = ','.join(sorted(route.methods))
        print(f'{methods:10} {route.path}')
"

# List FastAPI routes in bloommcp service
cd bloommcp && uv run python -c "
from app import app
for route in app.routes:
    if hasattr(route, 'methods'):
        methods = ','.join(sorted(route.methods))
        print(f'{methods:10} {route.path}')
"
```

**Python Services Documentation Checklist:**

- [ ] **API.md or langchain/README.md exists** with comprehensive endpoint documentation
- [ ] Each endpoint documented with:
  - [ ] HTTP method and path
  - [ ] Request parameters (query, body, headers)
  - [ ] Request body schema (JSON examples)
  - [ ] Response schema (success and error cases)
  - [ ] Authentication requirements (JWT or public)
  - [ ] Example curl commands
  - [ ] Error codes and meanings (400, 401, 404, 500)
- [ ] **LangGraph agent documented**
  - [ ] Agent graph structure and tools
  - [ ] Streaming response support
  - [ ] Tool-calling integration with FastMCP server
  - [ ] Error handling and retry logic
- [ ] **FastMCP server documented**
  - [ ] Available MCP tools and resources
  - [ ] Input/output schemas
  - [ ] Integration with LangGraph agent
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

Verify Docker, Caddy, and database documentation:

```bash
# List all Docker services
docker compose -f docker-compose.dev.yml config --services
docker compose -f docker-compose.prod.yml config --services

# Check Caddy configuration
cat caddy/Caddyfile

# List environment variables
grep "^[A-Z_]*=" .env.dev | cut -d= -f1 | sort

# Check Supabase migrations
ls -lh supabase/migrations/
```

**Infrastructure Documentation Checklist:**

- [ ] **docker-compose.dev.yml documented**
  - [ ] Service descriptions (LangGraph agent, FastMCP server, web app, Supabase, MinIO, Caddy, Kong)
  - [ ] Port mappings explained
  - [ ] Volume mounts documented (minio_data/, volumes/)
  - [ ] Environment variables listed
  - [ ] Health checks explained
- [ ] **docker-compose.prod.yml documented**
  - [ ] Differences from dev explained
  - [ ] Multi-stage build process
  - [ ] Production optimizations noted
  - [ ] Security considerations
- [ ] **Caddy configuration documented**
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
  - [ ] Migration workflow (`make apply-migrations-local`)
  - [ ] RLS policy guidelines
  - [ ] Studio UI access
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
  - [ ] JWT handling with Python services
- [ ] **Material-UI usage documented**
  - [ ] Theme configuration
  - [ ] Custom components
  - [ ] Styling patterns (sx prop vs styled vs CSS modules)
- [ ] **Python service API client documented**
  - [ ] API base URL configuration (routed through Caddy)
  - [ ] Type definitions for requests/responses
  - [ ] Error handling patterns
  - [ ] Authentication header injection
- [ ] **Environment variables documented**
  - [ ] Required variables (`.env.dev`, `.env.prod`)
  - [ ] `NEXT_PUBLIC_` variables explained
  - [ ] API endpoints and URLs
  - [ ] Supabase connection strings

## Documentation Templates

### API.md Template (Python Services)

Use this template to document the LangGraph agent and FastMCP server endpoints:

````markdown
# Bloom API Documentation

All API routing is handled by Caddy. In development, services are accessed directly.

## Authentication

All protected endpoints require a JWT token in the Authorization header:

\```
Authorization: Bearer <jwt_token>
\```

Get a token from Supabase Auth after user login.

## LangGraph Agent Endpoints (`langchain/`)

### GET /health

**Health Check**

Returns the status of the LangGraph agent service.

**Authentication:** Not required

**Response:**
\```json
{
  "status": "ok",
  "service": "langchain-agent"
}
\```

**Status Codes:**

- 200: Success

---

### POST /agent/invoke

**Invoke LangGraph Agent**

Runs the LangGraph agent with the provided input and returns the result.

**Authentication:** Required (JWT)

**Request Body:**
\```json
{
  "input": "Analyze scan 123",
  "scanner_id": 123
}
\```

**Parameters:**

- `input` (string, required): Natural language instruction for the agent
- `scanner_id` (integer, optional): ID of the scanner in database

**Response (Success):**
\```json
{
  "output": "Analysis complete",
  "tool_calls": [],
  "scanner_id": 123
}
\```

**Status Codes:**

- 200: Success
- 400: Bad request (invalid parameters)
- 401: Unauthorized (missing or invalid JWT)
- 500: Server error (agent invocation failed)

**Example:**
\```bash
curl -X POST http://localhost:8000/agent/invoke \
  -H "Authorization: Bearer <jwt_token>" \
  -H "Content-Type: application/json" \
  -d '{"input": "Analyze scan 123", "scanner_id": 123}'
\```

---

## FastMCP Server Endpoints (`bloommcp/`)

### GET /health

**Health Check**

Returns the status of the FastMCP server.

**Authentication:** Not required

**Response:**
\```json
{
  "status": "ok",
  "service": "bloommcp"
}
\```

---

### POST /mcp/tools/call

**Call an MCP Tool**

Invokes an MCP tool registered with the FastMCP server.

**Authentication:** Required (JWT)

**Request Body:**
\```json
{
  "name": "get_scan_data",
  "arguments": {
    "scanner_id": 123
  }
}
\```

**Parameters:**

- `name` (string, required): Name of the MCP tool to invoke
- `arguments` (object, required): Tool-specific arguments

**Response:**
\```json
{
  "content": [
    {
      "type": "text",
      "text": "Scanner 123 data..."
    }
  ]
}
\```

**Status Codes:**

- 200: Success
- 401: Unauthorized

## Environment Variables

Required environment variables (`.env.dev` or `.env.prod`):

- `SUPABASE_URL`: Supabase instance URL
- `SUPABASE_KEY`: Supabase anon or service key
- `JWT_SECRET`: Secret for JWT validation (must match Supabase)
- `AWS_REGION`: AWS region (use `us-east-1` for MinIO)
- `S3_BUCKET_NAME`: Default S3 bucket name
- `S3_ENDPOINT`: MinIO endpoint URL (e.g., `http://supabase-minio:9100`)
- `AWS_ACCESS_KEY_ID`: S3/MinIO access key
- `AWS_SECRET_ACCESS_KEY`: S3/MinIO secret key
````

### ARCHITECTURE.md Template

````markdown
# Bloom Architecture

## System Overview

Bloom is a full-stack application for biological/scientific data visualization, specifically designed for cylindrical scan image management and AI-powered analysis for plant research.

## Technology Stack

- **Frontend**: Next.js 16, React 19, TypeScript, Material-UI
- **LangGraph Agent**: FastAPI + LangGraph (Python 3.11) with uv package manager
- **FastMCP Server**: FastMCP (Python 3.11) with uv package manager
- **Database**: PostgreSQL (via self-hosted Supabase)
- **Object Storage**: MinIO (S3-compatible)
- **Authentication**: Supabase Auth (JWT tokens)
- **Reverse Proxy**: Caddy (production), Kong (Supabase API Gateway)
- **Containerization**: Docker Compose
- **Monorepo**: Turborepo with npm workspaces

## Service Architecture

\```
[Browser]
↓
[Caddy:80/443] (production) or direct (development)
├─→ [Next.js:3000]            (bloom-web)
│    ↓ (API calls)
├─→ [LangGraph Agent:8001]    (langchain-agent)
│    ↓ (tool calls)
├─→ [FastMCP Server:8002]     (bloommcp)
│    ↓ (database queries)
└─→ [Kong:8000]               (Supabase API Gateway)
     ├─→ [PostgreSQL:5432]    (db-dev / db-prod)
     ├─→ [PostgREST]          (REST API)
     ├─→ [GoTrue]             (Auth)
     ├─→ [Storage]
     └─→ [Realtime]
\```

MinIO (supabase-minio): ports 9100-9101

### Service Details

#### Next.js Web App (`web/`, container `bloom-web`)

- React-based UI for scan visualization
- Server-side rendering (SSR) with App Router
- Material-UI components
- Supabase Auth integration
- API client for LangGraph agent and FastMCP endpoints

#### LangGraph Agent (`langchain/`, container `langchain-agent`)

- FastAPI + LangGraph service for AI-powered data analysis
- S3/MinIO integration for image storage
- JWT authentication (validates Supabase tokens)
- Tool-calling integration with FastMCP server
- Logging with Python logging module

#### FastMCP Server (`bloommcp/`, container `bloommcp`)

- FastMCP server exposing plant data tools
- MCP tools for querying scan data, images, and experiments
- Used by the LangGraph agent for structured data access

#### Supabase Stack (Kong on port 8000)

- **PostgreSQL** (port 5432): Main database (container `db-dev` / `db-prod`)
- **PostgREST**: Auto-generated REST API
- **GoTrue**: Authentication service
- **Storage**: File upload and management
- **Realtime**: WebSocket subscriptions
- **Studio UI**: Admin dashboard

#### MinIO (container `supabase-minio`, ports 9100-9101)

- S3-compatible object storage
- Stores scan images and analysis artifacts
- Bucket: `bloom-storage`
- Console UI on port 9101 (admin interface)

#### Caddy (production only, container `caddy`, port 80/443)

- Reverse proxy for all services
- Subpath routing:
  - `/` → Next.js frontend
  - `/api/agent/` → LangGraph agent
  - `/api/mcp/` → FastMCP server
  - `/supabase_kong/` → Supabase services
- Automatic HTTPS with Let's Encrypt

## Data Flow

### 1. Scan Data Upload

\```
[Researcher] → [Web UI] → [Supabase Storage/API] → [PostgreSQL: cyl_images table]
                                                   ↓
                                            [MinIO: S3 storage]
\```

### 2. AI Analysis Workflow

\```
[Researcher sends analysis request]
↓
[Web UI] → [LangGraph Agent /agent/invoke]
↓
[Agent builds graph with tool nodes]
↓
[Tool call: FastMCP Server → fetch scan data from PostgreSQL]
↓
[Tool call: fetch images from MinIO/S3]
↓
[Agent processes and returns analysis]
↓
[Return result to Web UI]
\```

### 3. Authentication Flow

\```
[User Login] → [Web UI] → [Supabase Auth (GoTrue)]
↓
[JWT Token returned to client]
↓
[Client includes token in LangGraph agent / FastMCP requests]
↓
[Services validate JWT signature with JWT_SECRET]
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
- No Caddy (direct service access)
- Hot reload enabled for Python services and Next.js

### Production (.env.prod)

- Multi-stage Docker builds (smaller images)
- Caddy reverse proxy with subpath routing
- External domain names
- Environment-specific secrets
- No debug mode

## Security

### Authentication

- Supabase Auth (email/password, OAuth providers)
- JWT tokens (HS256 algorithm)
- Token validation on protected endpoints in Python services
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
make apply-migrations-local

# 6. Load test data
cd web && npm run init-env
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
docker compose -f docker-compose.dev.yml logs -f

# LangGraph agent only
docker compose -f docker-compose.dev.yml logs -f langchain-agent

# FastMCP server only
docker compose -f docker-compose.dev.yml logs -f bloommcp

# Next.js only
docker compose -f docker-compose.dev.yml logs -f web
\```

### Health Checks

- LangGraph agent: `curl http://localhost:8001/health`
- FastMCP server: `curl http://localhost:8002/health`
- Supabase: `curl http://localhost:8000/rest/v1/`
- MinIO: Browse to `http://localhost:9101`
- Next.js: Browse to `http://localhost:3000`

### Database

- Supabase Studio: `http://localhost:55323`
- Direct PostgreSQL: `psql -h localhost -p 5432 -U postgres -d postgres`

## Performance Considerations

- **AI analysis**: LLM calls are latency-bound; consider streaming responses
- **Large scans**: Pagination for >1000 images
- **S3 presigned URLs**: 1-hour expiration (configurable)
- **Database**: Connection pooling via Supabase
- **Frontend**: Server-side rendering for initial page load

## Deployment

### Production Checklist

- [ ] Update environment variables in `.env.prod`
- [ ] Build production images: `make rebuild-prod-fresh`
- [ ] Configure Caddy SSL/TLS certificates
- [ ] Set up domain DNS records
- [ ] Configure Supabase production instance
- [ ] Initialize MinIO buckets and policies
- [ ] Run database migrations: `make apply-migrations-local`
- [ ] Test all endpoints with production URLs
- [ ] Set up backup strategy (PostgreSQL, MinIO)
- [ ] Configure monitoring and alerting

## Future Enhancements

- [ ] Redis caching for frequently accessed data
- [ ] Async background jobs for long-running agent tasks
- [ ] CDN for static assets
- [ ] Horizontal scaling for Python services (load balancer)
- [ ] WebSocket updates for agent progress
- [ ] Advanced analysis options (custom agent tools)
- [ ] Bulk analysis jobs
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

- **Node.js** (v20+) and **npm** (v10+)
  \```bash
  # Verify versions
  node -v && npm -v
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
S3_ENDPOINT=http://supabase-minio:9100
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin

# Next.js
NEXT_PUBLIC_SUPABASE_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_ANON_KEY=<same-as-SUPABASE_KEY>
\```

**Where to find Supabase keys:**

1. Start Supabase: `make dev-up`
2. Check logs: `docker compose -f docker-compose.dev.yml logs supabase-kong`
3. Look for `anon key:` and `service_role key:`

### 4. Install Dependencies

\```bash

# Install all monorepo dependencies
npm install

# Install Python service dependencies
cd langchain && uv sync --all-extras
cd ../bloommcp && uv sync --all-extras
\```

### 5. Start Services

\```bash

# Start all Docker services
make dev-up

# Wait for services to be healthy (~30 seconds)
docker compose ps
\```

### 6. Initialize Database

\```bash

# Run Supabase migrations
make apply-migrations-local

# Load test data (optional)
cd web && npm run init-env
\```

### 7. Verify Setup

\```bash

# Test LangGraph agent
curl http://localhost:8001/health

# Test FastMCP server
curl http://localhost:8002/health

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
docker compose -f docker-compose.dev.yml logs -f langchain-agent
docker compose -f docker-compose.dev.yml logs -f web
\```

### Making Code Changes

**LangGraph agent (Python, `langchain/`):**

- Edit files in `langchain/`
- FastAPI auto-reloads on file changes (debug mode)
- Install new dependencies: `cd langchain && uv add <package>`
- Run tests: `uv run pytest tests/integration/ -v --tb=short`

**FastMCP server (Python, `bloommcp/`):**

- Edit files in `bloommcp/`
- FastAPI auto-reloads on file changes
- Install new dependencies: `cd bloommcp && uv add <package>`

**Next.js (TypeScript, `web/`):**

- Edit files in `web/`
- Next.js auto-reloads via Fast Refresh
- Install new dependencies: `npm install <package>`
- Run linting: `npm run lint`

**Database:**

- Edit schemas in `supabase/migrations/`
- Apply migrations: `make apply-migrations-local`
- Create new migration: `supabase migration new <name>`

### Stopping Services

\```bash

# Stop all services (keep data)
make dev-down

# Stop and remove all data
docker compose -f docker-compose.dev.yml down -v
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
make apply-migrations-local
cd web && npm run init-env
\```

### Clear MinIO Storage

\```bash

# Stop services
make dev-down

# Remove MinIO data
rm -rf minio_data/*

# Restart (bucket will be recreated)
make dev-up
\```

### Rebuild Services

\```bash

# Rebuild all Docker images
make rebuild-dev-fresh

# Rebuild specific service
docker compose -f docker-compose.dev.yml build langchain-agent
docker compose -f docker-compose.dev.yml up -d langchain-agent
\```

### Run Tests

\```bash

# Python integration tests
uv run pytest tests/integration/ -v --tb=short

# TypeScript linting and type-check
npm run lint
cd web && npx tsc --noEmit
\```

### Format and Lint Code

\```bash

# Python (recommended locally but NOT currently enforced in CI)
cd langchain
uv run black .
uv run ruff check --fix .
uv run mypy .

cd ../bloommcp
uv run black .
uv run ruff check --fix .

# TypeScript/JavaScript
npm run format
npm run lint

# Run all pre-commit hooks
# Run all pre-commit hooks (from repo root)
uv run pre-commit run --all-files
\```

## Troubleshooting

### Issue: Port Already in Use

**Symptom**: `Error: port is already allocated`

**Solution**:
\```bash

# Find process using port (e.g., 8001)
lsof -ti:8001

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

**Symptom**: JWT validation fails in Python services

**Solution**:

1. Check JWT_SECRET matches Supabase secret
2. Get correct secret from Supabase logs:
   \```bash
   docker compose -f docker-compose.dev.yml logs supabase-kong | grep "JWT secret"
   \```
3. Update `.env.dev` with correct secret
4. Restart services: `docker compose -f docker-compose.dev.yml restart langchain-agent bloommcp`

### Issue: Database Connection Error

**Symptom**: `could not connect to server: Connection refused`

**Solution**:
\```bash

# Check if PostgreSQL is running
docker compose ps db-dev

# Check logs
docker compose -f docker-compose.dev.yml logs db-dev

# Restart database
docker compose -f docker-compose.dev.yml restart db-dev
\```

### Issue: Next.js Build Errors

**Symptom**: TypeScript errors or missing modules

**Solution**:
\```bash

# Clear Next.js cache
rm -rf web/.next

# Reinstall dependencies
npm ci

# Restart service
docker compose -f docker-compose.dev.yml restart web
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
feat(langchain): add LangGraph agent endpoint for scan analysis
fix(web): correct authentication flow
docs(readme): update setup instructions
chore(deps): upgrade Next.js to v16
\```

### Pre-commit Hooks

Install pre-commit hooks:

\```bash
# Install from repo root (config is at .pre-commit-config.yaml)
uv run pre-commit install
\```

Hooks run automatically on `git commit`:

- Trim trailing whitespace
- Fix end of files
- Check YAML/TOML
- Black formatting
- Ruff linting
- mypy type checking (recommended locally but NOT currently enforced in CI)
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

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [FastMCP Documentation](https://gofastmcp.com/)
- [Next.js Documentation](https://nextjs.org/docs)
- [Supabase Documentation](https://supabase.com/docs)
- [MinIO Documentation](https://min.io/docs/)
- [Turborepo Documentation](https://turbo.build/repo)
- [uv Documentation](https://docs.astral.sh/uv/)
- [Caddy Documentation](https://caddyserver.com/docs/)
````

## Documentation Review Checklist

### Core Documentation

- [ ] **README.md** - Project overview, quick start, contributing
- [ ] **CLAUDE.md** - AI assistant instructions and project context
- [ ] **ARCHITECTURE.md** - System design, service architecture
- [ ] **DEVELOPMENT.md** - Local setup, development workflow
- [ ] **API.md** or **langchain/README.md** - LangGraph agent and FastMCP API endpoints
- [ ] **TESTING.md** - Test strategy, running integration tests
- [ ] **DEPLOYMENT.md** - Production deployment guide

### Package Documentation

- [ ] **langchain/README.md** - LangGraph agent, API endpoints, tools
- [ ] **bloommcp/README.md** - FastMCP server, available tools
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
- [ ] **caddy/Caddyfile** - Reverse proxy config
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
grep -r "os.environ.get\|process.env" langchain/ bloommcp/ web/ --include="*.py" --include="*.ts" --include="*.tsx"

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
- `/openspec/proposal` - Create documentation for new features
- `/validate-env` - Ensure environment is correct for docs testing
- `/openspec/apply` - Implement approved changes
- `/openspec/archive` - Archive completed changes