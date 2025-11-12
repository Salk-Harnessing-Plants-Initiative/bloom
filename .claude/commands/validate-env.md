---
name: Validate Environment
description: Check development environment is correctly set up for Bloom
category: Setup
tags: [environment, setup, validation, troubleshooting]
---

# Validate Development Environment

Check that your development environment is correctly set up and ready for Bloom development (Next.js + Flask + Docker + Supabase).

## Quick Start

Run the validation checks manually:

```bash
# Check Node.js and pnpm
node -v
pnpm -v

# Check Python and uv
python --version
uv --version

# Check Docker
docker --version
docker-compose --version

# Check Supabase CLI
supabase --version

# Check services are running
docker-compose -f docker-compose.dev.yml ps
```

This checks:

1. Node.js version (≥ 18)
2. pnpm installation and version
3. Python version (≥ 3.11)
4. uv installation
5. Docker and Docker Compose
6. Supabase CLI
7. Required ports availability
8. Environment variables
9. Service connectivity
10. Package installations

## What Gets Checked

### 1. Node.js Version

- ✅ Node.js ≥ 18 (recommended: 20 LTS)
- ❌ Node.js < 18

### 2. pnpm Package Manager

- ✅ pnpm installed (recommended: ≥ 8.0)
- ✅ pnpm workspaces configured
- ✅ `node_modules` installed

### 3. Python Version

- ✅ Python ≥ 3.11
- ✅ uv package manager installed
- ✅ Flask dependencies installed

### 4. Docker and Docker Compose

- ✅ Docker installed and running
- ✅ Docker Compose installed
- ✅ Docker daemon accessible

### 5. Supabase CLI

- ✅ Supabase CLI installed (≥ 1.0)
- ✅ Supabase running locally
- ✅ Database migrations applied

### 6. Port Availability

- ✅ Port 3000 available (Next.js dev server)
- ✅ Port 5002 available (Flask API)
- ✅ Port 54321 available (Supabase Kong)
- ✅ Port 54322 available (PostgreSQL)
- ✅ Port 54323 available (Supabase Studio)
- ✅ Port 9000 available (MinIO)
- ✅ Port 9001 available (MinIO Console)

### 7. Environment Variables

- ✅ `.env.dev` file exists
- ✅ Required variables defined
- ✅ Supabase keys set
- ✅ MinIO credentials set

### 8. Service Connectivity

- ✅ Supabase database accessible
- ✅ MinIO accessible
- ✅ Flask API responds to health check
- ✅ Next.js dev server can connect to API

### 9. Database Schema

- ✅ Supabase migrations applied
- ✅ Required tables exist
- ✅ RLS policies configured

### 10. MinIO Storage

- ✅ MinIO server running
- ✅ Bucket created (`bloom-videos`)
- ✅ Presigned URLs work

## Expected Output

### ✅ Fully Configured Environment

```
================================
Bloom Environment Validation
================================

[1/10] Node.js Version
✅ Node.js v20.10.0 (meets requirement: >=18)
✅ npm v10.2.3

[2/10] pnpm Package Manager
✅ pnpm 8.15.0 installed
✅ pnpm workspaces configured (4 packages)
✅ node_modules installed (1,234 packages)

[3/10] Python Version
✅ Python 3.11.7 (meets requirement: >=3.11)
✅ uv 0.1.0 installed
✅ Flask dependencies installed (45 packages)

[4/10] Docker and Docker Compose
✅ Docker 24.0.6 installed
✅ Docker Compose v2.23.0 installed
✅ Docker daemon running

[5/10] Supabase CLI
✅ Supabase CLI 1.120.0 installed
✅ Supabase running (5 services)
✅ Database migrations up to date (12 applied)

[6/10] Port Availability
✅ Port 3000 available (Next.js)
✅ Port 5002 available (Flask)
✅ Port 54321 available (Supabase Kong)
✅ Port 54322 available (PostgreSQL)
✅ Port 54323 available (Supabase Studio)
✅ Port 9000 available (MinIO)
✅ Port 9001 available (MinIO Console)

[7/10] Environment Variables
✅ .env.dev exists
✅ DATABASE_URL set
✅ SUPABASE_URL set
✅ SUPABASE_ANON_KEY set
✅ AWS_ACCESS_KEY_ID set (MinIO)
✅ AWS_SECRET_ACCESS_KEY set (MinIO)

[8/10] Service Connectivity
✅ Supabase database: Connected (PostgreSQL 15.1)
✅ MinIO: Connected (RELEASE.2024-01-01T00-00-00Z)
✅ Flask API: Health check passed (200 OK)

[9/10] Database Schema
✅ Migrations applied: 12/12
✅ Tables exist: experiments, videos, trials, frames
✅ RLS policies configured: 8 policies

[10/10] MinIO Storage
✅ MinIO server running
✅ Bucket 'bloom-videos' exists
✅ Presigned URL generation works

================================
✅ ENVIRONMENT VALID
================================

Your environment is ready for development! 🚀

Next steps:
  - Start dev servers: make dev-up
  - Run linting: pnpm lint
  - Run tests: pnpm test
  - Start coding!
```

### ❌ Issues Found

```
================================
Bloom Environment Validation
================================

[1/10] Node.js Version
✅ Node.js v20.10.0

[2/10] pnpm Package Manager
❌ pnpm not found

FIX: Install pnpm with:
     npm install -g pnpm

[3/10] Python Version
✅ Python 3.11.7
❌ uv not found

FIX: Install uv with:
     curl -LsSf https://astral.sh/uv/install.sh | sh

[4/10] Docker and Docker Compose
✅ Docker 24.0.6 installed
❌ Docker daemon not running

FIX: Start Docker Desktop or run:
     sudo systemctl start docker  # Linux
     open -a Docker  # macOS

[5/10] Supabase CLI
❌ Supabase CLI not installed

FIX: Install Supabase CLI with:
     brew install supabase/tap/supabase  # macOS
     # or see: https://supabase.com/docs/guides/cli

[6/10] Port Availability
⚠️  Port 3000 already in use (PID: 12345)

FIX: Stop process using port:
     kill 12345
     # or: lsof -ti :3000 | xargs kill -9

[7/10] Environment Variables
❌ .env.dev not found

FIX: Create .env.dev from template:
     cp .env.example .env.dev
     # Then edit .env.dev with your values

[8/10] Service Connectivity
⏭  Skipped (services not running)

[9/10] Database Schema
⏭  Skipped (Supabase not running)

[10/10] MinIO Storage
⏭  Skipped (services not running)

================================
❌ ENVIRONMENT HAS ISSUES
================================

Found 5 issues. Fix them using the commands above.
```

## Common Issues & Fixes

### Issue: "pnpm not found"

**Cause:** pnpm not installed globally

**Fix:**

```bash
# Install pnpm globally
npm install -g pnpm

# Verify installation
pnpm -v
```

### Issue: "uv not found"

**Cause:** uv (Python package manager) not installed

**Fix:**

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with Homebrew (macOS)
brew install uv

# Or with pip
pip install uv

# Verify installation
uv --version
```

### Issue: "Docker daemon not running"

**Cause:** Docker Desktop not started or Docker service stopped

**Fix:**

```bash
# macOS
open -a Docker

# Linux
sudo systemctl start docker

# Verify Docker is running
docker ps
```

### Issue: "Supabase CLI not installed"

**Cause:** Supabase CLI not installed

**Fix:**

```bash
# macOS
brew install supabase/tap/supabase

# Linux
brew install supabase/tap/supabase
# or download binary from GitHub releases

# Windows
scoop install supabase

# Verify installation
supabase --version
```

### Issue: "Port already in use"

**Cause:** Another process is using required port

**Fix:**

```bash
# Find process using port (e.g., 3000)
lsof -ti :3000  # macOS/Linux
netstat -ano | findstr :3000  # Windows

# Kill process
kill -9 <PID>  # macOS/Linux
taskkill /F /PID <PID>  # Windows

# Or stop all Docker containers
docker-compose down
```

### Issue: ".env.dev not found"

**Cause:** Environment file hasn't been created

**Fix:**

```bash
# Copy example file
cp .env.example .env.dev

# Edit with your values
nano .env.dev
# or
code .env.dev

# Required variables:
# - DATABASE_URL
# - SUPABASE_URL
# - SUPABASE_ANON_KEY
# - AWS_ACCESS_KEY_ID=minioadmin
# - AWS_SECRET_ACCESS_KEY=minioadmin
```

### Issue: "Supabase not running"

**Cause:** Supabase services haven't been started

**Fix:**

```bash
# Start Supabase
supabase start

# Check status
supabase status

# If migrations haven't been applied
supabase db reset
```

### Issue: "MinIO bucket not found"

**Cause:** MinIO bucket hasn't been initialized

**Fix:**

```bash
# Start services
docker-compose -f docker-compose.dev.yml up -d

# Create bucket using MinIO Console
open http://localhost:9001
# Login: minioadmin / minioadmin
# Create bucket: bloom-videos

# Or use mc CLI
docker exec -it bloom-minio mc mb local/bloom-videos --ignore-existing
```

### Issue: "Database migrations not applied"

**Cause:** Supabase migrations haven't run

**Fix:**

```bash
# Apply all migrations
supabase db push

# Or reset database (WARNING: deletes all data)
supabase db reset

# Check migration status
supabase migration list
```

### Issue: "node_modules not installed"

**Cause:** Dependencies haven't been installed

**Fix:**

```bash
# Install all workspace dependencies
pnpm install

# If pnpm-lock.yaml is out of sync
pnpm install --no-frozen-lockfile
```

### Issue: "Flask dependencies not installed"

**Cause:** Python dependencies haven't been installed

**Fix:**

```bash
# Navigate to Flask directory
cd flask

# Install dependencies with uv
uv sync

# Or with specific extras
uv sync --all-extras
```

## When to Run This

### Initial Setup

Run after cloning the repository for the first time.

### After Environment Changes

- After updating dependencies (`package.json`, `pyproject.toml`)
- After `pnpm install` or `uv sync`
- After updating Docker images
- After changing environment variables

### Troubleshooting

- When services fail to start
- When tests fail unexpectedly
- When getting "connection refused" errors
- After switching machines or branches
- After long breaks from development

### Onboarding

- Help new contributors verify setup
- Include in onboarding documentation
- Share validation output to debug setup issues

## Detailed Checks Explained

### Node.js Version Check

```bash
node --version
# Should output: v18+ (v20 LTS recommended)
```

Bloom requires Node.js 18+ for Next.js 14 support.

### pnpm Workspace Check

```bash
pnpm list --depth 0
# Should show all workspace packages:
# - web (Next.js app)
# - packages/bloom-fs
# - packages/bloom-js
# - packages/bloom-nextjs-auth
```

### Python Version Check

```bash
python --version
# Should output: Python 3.11+
```

Bloom Flask backend requires Python 3.11+ for type hints and performance.

### uv Installation Check

```bash
uv --version
# Should output: uv 0.1.0+
```

uv is 10-100x faster than pip for dependency management.

### Docker Check

```bash
docker ps
# Should show running containers or empty list (ok if not started yet)

docker-compose version
# Should output: Docker Compose version v2.x
```

### Supabase Status Check

```bash
supabase status
# Should show:
# API URL: http://localhost:54321
# DB URL: postgresql://postgres:postgres@localhost:54322/postgres
# Studio URL: http://localhost:54323
```

### Port Availability Check

```bash
# Check if port is in use
lsof -i :3000  # macOS/Linux
netstat -ano | findstr :3000  # Windows

# Empty output = port available
# Output with PID = port in use
```

### Environment Variable Check

```bash
# Check .env.dev exists
ls -la .env.dev

# Verify required variables (don't print values)
grep -q "DATABASE_URL" .env.dev && echo "✅ DATABASE_URL set"
grep -q "SUPABASE_URL" .env.dev && echo "✅ SUPABASE_URL set"
```

### Database Connectivity Check

```bash
# Test PostgreSQL connection
psql postgresql://postgres:postgres@localhost:54322/postgres -c "SELECT version();"

# Or use Supabase CLI
supabase db diff
```

### MinIO Connectivity Check

```bash
# Test MinIO health
curl http://localhost:9000/minio/health/live

# List buckets
docker exec bloom-minio mc ls local/
```

## Platform-Specific Notes

### macOS

**Recommended setup:**

```bash
# Install Homebrew (if not installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Node.js
brew install node@20

# Install pnpm
npm install -g pnpm

# Install Python
brew install python@3.11

# Install uv
brew install uv

# Install Docker Desktop
brew install --cask docker

# Install Supabase CLI
brew install supabase/tap/supabase

# Start Docker Desktop
open -a Docker
```

### Ubuntu/Debian Linux

**Recommended setup:**

```bash
# Update package list
sudo apt-get update

# Install Node.js 20
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install pnpm
npm install -g pnpm

# Install Python 3.11
sudo apt-get install -y python3.11 python3.11-venv

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo apt-get install -y docker-compose-plugin

# Install Supabase CLI
brew install supabase/tap/supabase
# or download from GitHub releases
```

### Windows

**Recommended setup:**

```powershell
# Install Scoop (package manager)
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
irm get.scoop.sh | iex

# Install Node.js
scoop install nodejs-lts

# Install pnpm
npm install -g pnpm

# Install Python
scoop install python

# Install uv
scoop install uv

# Install Docker Desktop
# Download from: https://www.docker.com/products/docker-desktop

# Install Supabase CLI
scoop install supabase

# Or use WSL2 (recommended for better Docker performance)
wsl --install
```

## Integration with Other Commands

```bash
# 1. First time setup
git clone <repo-url>
cd bloom

# 2. Validate environment
/validate-env
# Fix any issues it identifies

# 3. Install dependencies
pnpm install
cd flask && uv sync

# 4. Start services
make dev-up

# 5. Run tests to verify
pnpm test
cd flask && uv run pytest

# 6. Start development!
```

## Automated Environment Check Script

Create a script to run all validation checks:

```bash
#!/bin/bash
# scripts/validate-env.sh

echo "================================"
echo "Bloom Environment Validation"
echo "================================"
echo ""

# Function to check command exists
command_exists() {
  command -v "$1" >/dev/null 2>&1
}

# Function to check port availability
port_available() {
  ! lsof -i :"$1" >/dev/null 2>&1
}

# 1. Node.js Version
echo "[1/10] Node.js Version"
if command_exists node; then
  NODE_VERSION=$(node -v)
  echo "✅ Node.js $NODE_VERSION"
else
  echo "❌ Node.js not found"
fi

# 2. pnpm
echo ""
echo "[2/10] pnpm Package Manager"
if command_exists pnpm; then
  PNPM_VERSION=$(pnpm -v)
  echo "✅ pnpm $PNPM_VERSION installed"
else
  echo "❌ pnpm not found"
fi

# 3. Python & uv
echo ""
echo "[3/10] Python Version"
if command_exists python; then
  PYTHON_VERSION=$(python --version)
  echo "✅ $PYTHON_VERSION"
else
  echo "❌ Python not found"
fi

if command_exists uv; then
  UV_VERSION=$(uv --version)
  echo "✅ uv $UV_VERSION installed"
else
  echo "❌ uv not found"
fi

# 4. Docker
echo ""
echo "[4/10] Docker and Docker Compose"
if command_exists docker; then
  DOCKER_VERSION=$(docker --version)
  echo "✅ $DOCKER_VERSION"

  if docker ps >/dev/null 2>&1; then
    echo "✅ Docker daemon running"
  else
    echo "❌ Docker daemon not running"
  fi
else
  echo "❌ Docker not found"
fi

# 5. Supabase CLI
echo ""
echo "[5/10] Supabase CLI"
if command_exists supabase; then
  SUPABASE_VERSION=$(supabase --version 2>&1 | head -1)
  echo "✅ $SUPABASE_VERSION"
else
  echo "❌ Supabase CLI not found"
fi

# 6. Port Availability
echo ""
echo "[6/10] Port Availability"
PORTS=(3000 5002 54321 54322 54323 9000 9001)
for port in "${PORTS[@]}"; do
  if port_available "$port"; then
    echo "✅ Port $port available"
  else
    PID=$(lsof -ti :"$port")
    echo "⚠️  Port $port in use (PID: $PID)"
  fi
done

# 7. Environment Variables
echo ""
echo "[7/10] Environment Variables"
if [ -f ".env.dev" ]; then
  echo "✅ .env.dev exists"
else
  echo "❌ .env.dev not found"
fi

# 8. Service Connectivity
echo ""
echo "[8/10] Service Connectivity"
if curl -s http://localhost:54321 >/dev/null 2>&1; then
  echo "✅ Supabase: Connected"
else
  echo "❌ Supabase: Not accessible"
fi

if curl -s http://localhost:9000/minio/health/live >/dev/null 2>&1; then
  echo "✅ MinIO: Connected"
else
  echo "❌ MinIO: Not accessible"
fi

# 9. Database Schema
echo ""
echo "[9/10] Database Schema"
if command_exists supabase; then
  if supabase status >/dev/null 2>&1; then
    echo "✅ Supabase running"
  else
    echo "❌ Supabase not running"
  fi
else
  echo "⏭  Skipped (Supabase CLI not installed)"
fi

# 10. MinIO Storage
echo ""
echo "[10/10] MinIO Storage"
if docker ps | grep -q bloom-minio; then
  echo "✅ MinIO server running"
else
  echo "❌ MinIO server not running"
fi

echo ""
echo "================================"
echo "Validation complete!"
echo "================================"
```

Make it executable:

```bash
chmod +x scripts/validate-env.sh
./scripts/validate-env.sh
```

## Troubleshooting Guide

### "Services won't start"

**Debug steps:**

```bash
# Check Docker logs
docker-compose -f docker-compose.dev.yml logs

# Check specific service
docker-compose -f docker-compose.dev.yml logs flask-app

# Restart all services
docker-compose -f docker-compose.dev.yml down
docker-compose -f docker-compose.dev.yml up -d

# Check service health
docker-compose -f docker-compose.dev.yml ps
```

### "Database connection fails"

**Debug steps:**

```bash
# Check Supabase status
supabase status

# If not running, start it
supabase start

# Test connection
psql postgresql://postgres:postgres@localhost:54322/postgres

# Reset database if corrupted
supabase db reset
```

### "MinIO not accessible"

**Debug steps:**

```bash
# Check MinIO container
docker ps | grep minio

# View MinIO logs
docker logs bloom-minio

# Restart MinIO
docker-compose -f docker-compose.dev.yml restart minio

# Access MinIO Console
open http://localhost:9001
```

### "Permission denied errors"

**Debug steps:**

```bash
# Fix minio_data permissions
sudo chown -R $(whoami) minio_data/

# Fix Docker socket permissions (Linux)
sudo usermod -aG docker $USER
newgrp docker

# Or run with sudo (not recommended)
sudo docker-compose up
```

## Output Format

The validation outputs:

- ✅ Green checkmark: Validation passed
- ❌ Red X: Validation failed (with fix instructions)
- ⚠️ Yellow warning: Non-critical issue
- ⏭ Skipped: Check skipped due to previous failure

## Related Commands

- `/run-ci-locally` - Run all CI checks (requires valid environment)
- `/ci-debug` - Debug CI failures
- `/docs-review` - Review documentation (includes environment setup docs)

## Tips

1. **Run after long breaks**: Environment can drift over time
2. **Before filing bug reports**: Attach validation output to bugs
3. **Team onboarding**: Send validation output to verify new contributors
4. **CI debugging**: Compare local validation with CI environment
5. **After system updates**: OS/Python/Node updates can break environment
6. **Use Makefile**: `make dev-up` starts all services in correct order
7. **Check .gitignore**: Ensure `.env.dev` is not committed (contains secrets)
