---
name: Validate Environment
description: Check development environment is correctly set up for Bloom
category: Setup
tags: [environment, setup, validation, troubleshooting]
---

# Validate Development Environment

Checks that your local environment is ready for Bloom development.

## Prerequisites

| Tool | Minimum Version | Check Command |
|---|---|---|
| Node.js | 18+ | `node -v` |
| npm | 9+ | `npm -v` |
| Python | 3.11+ | `python --version` |
| uv | latest | `uv --version` |
| Docker | 20+ | `docker --version` |
| Docker Compose | v2+ | `docker compose version` |

## Check 1: Node.js and npm

```bash
node -v    # Should be 18+
npm -v     # Should be 9+
npm ci     # Install dependencies from lockfile
```

## Check 2: Python and uv

```bash
python --version    # Should be 3.11+
uv --version

# Install dependencies for both Python services
cd langchain && uv sync && cd ..
cd bloommcp && uv sync && cd ..
```

## Check 3: Docker Services

```bash
make dev-up
docker compose -f docker-compose.dev.yml ps
```

Key services: `bloom-web`, `langchain-agent`, `bloommcp`, `db-dev`, `supabase-minio`, `kong`, `auth`, `rest`, `realtime`, `storage`, `studio`, `supavisor`, `imgproxy`, `meta`, `swagger-ui`

## Check 4: Database

```bash
docker exec db-dev pg_isready -U supabase_admin -h localhost
make apply-migrations-local
make gen-types
```

## Check 5: MinIO/Storage

```bash
curl http://localhost:9000/minio/health/live
make create-bucket
```

## Check 6: Service Connectivity

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000    # Web app
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000    # Supabase API (Kong)
curl -s -o /dev/null -w "%{http_code}" http://localhost:9000/minio/health/live  # MinIO
```

## Troubleshooting

### Port conflicts
```bash
# Linux/macOS
lsof -i :3000 -i :5432 -i :8000 -i :9000
# Windows
netstat -ano | findstr ":3000 :5432 :8000 :9000"
```

### Docker out of disk space
```bash
docker system prune -af --volumes
```

### Stale dependencies
```bash
rm -rf node_modules web/node_modules packages/*/node_modules
npm ci
cd langchain && uv sync && cd ../bloommcp && uv sync && cd ..
```

### Rebuild from scratch
```bash
make rebuild-dev-fresh
```

## Related Commands

- `/run-ci-locally` — run CI checks locally
- `/lint` — run linting checks
- `/database-migration` — manage database migrations