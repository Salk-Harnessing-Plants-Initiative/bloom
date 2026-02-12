# Issue: Staging Environment Setup

**Epic**: [CI/CD Pipeline with Staging & Production Environments](./EPIC-cicd-deployment-testing.md)
**Priority**: P0
**Dependencies**: #cicd-1 (Runner)
**Labels**: `cicd`, `infrastructure`, `staging`, `deployment`

## Summary

Create a staging environment that mirrors production but runs on different ports, with automatic deployment triggered by pushes to the `dev` branch.

## Background

Currently there's only a dev environment (for local development) and production. A staging environment provides:
- Pre-production testing with production-like configuration
- Safe place to verify changes before production deployment
- Environment for QA and stakeholder review
- Automated deployment target for the `dev` branch

## Requirements

### Environment Isolation
- [ ] Separate database (`bloom_staging`)
- [ ] Separate MinIO buckets (prefixed with `staging-`)
- [ ] Different ports from production
- [ ] Own environment variables (`.env.staging`)
- [ ] Can run alongside production on same server

### Deployment
- [ ] Automatic deployment on push to `dev` (after CI passes)
- [ ] Zero-downtime deployments
- [ ] Rollback capability
- [ ] Health check verification after deploy

## Port Allocation

| Service | Production | Staging |
|---------|------------|---------|
| Nginx (HTTP) | 80 | 3080 |
| Nginx (HTTPS) | 443 | 3443 |
| Kong API | 8000 | 8100 |
| PostgreSQL | 5432 | 5433 |
| MinIO API | 9000 | 9200 |
| MinIO Console | 9001 | 9201 |
| Supabase Studio | 55323 | 55324 |

## Implementation

### 1. Create Staging Docker Compose

```yaml
# docker-compose.staging.yml
name: bloom-staging

services:
  nginx:
    image: nginx:1.28.0-alpine
    ports:
      - "3080:80"
      - "3443:443"
    volumes:
      - ./nginx/nginx.staging.conf:/etc/nginx/nginx.conf:ro
      - ./certs/staging:/etc/nginx/certs:ro
    depends_on:
      bloom-web:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - bloom-staging

  bloom-web:
    build:
      context: ./web
      dockerfile: Dockerfile.bloom-web.prod
    environment:
      - NEXT_PUBLIC_SUPABASE_URL=http://localhost:8100
      - NEXT_PUBLIC_SUPABASE_ANON_KEY=${STAGING_ANON_KEY}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/api/health"]
      interval: 10s
      timeout: 5s
      retries: 3
    restart: unless-stopped
    networks:
      - bloom-staging

  db:
    image: supabase/postgres:15.8.1.060
    environment:
      POSTGRES_PASSWORD: ${STAGING_DB_PASSWORD}
      POSTGRES_DB: bloom_staging
    volumes:
      - ./volumes/staging/db:/var/lib/postgresql/data
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    networks:
      - bloom-staging

  # ... (rest of services with staging configuration)
  # See full file in implementation

networks:
  bloom-staging:
    driver: bridge
```

### 2. Create Staging Environment File Template

```bash
# .env.staging.template

# Database
STAGING_DB_PASSWORD=<generate-secure-password>
POSTGRES_DB=bloom_staging

# JWT (should be different from prod)
STAGING_JWT_SECRET=<generate-secure-secret>
STAGING_ANON_KEY=<generate-anon-key>
STAGING_SERVICE_ROLE_KEY=<generate-service-key>

# MinIO
STAGING_MINIO_ROOT_USER=staging_admin
STAGING_MINIO_ROOT_PASSWORD=<generate-secure-password>
STAGING_MINIO_DATA_PATH=/var/lib/bloom/staging/minio

# Domains (optional, for reverse proxy)
STAGING_DOMAIN=staging.bloom.local
```

### 3. Create Deployment Workflow

```yaml
# .github/workflows/deploy-staging.yml
name: Deploy to Staging

on:
  push:
    branches: [dev]
  workflow_dispatch:

concurrency:
  group: staging-deploy
  cancel-in-progress: false

jobs:
  ci:
    uses: ./.github/workflows/ci.yml

  deploy:
    name: Deploy to Staging
    runs-on: [self-hosted, bloom]
    needs: [ci]
    environment: staging
    steps:
      - uses: actions/checkout@v4

      - name: Create .env.staging from secrets
        run: |
          cat > .env.staging << EOF
          STAGING_DB_PASSWORD=${{ secrets.STAGING_DB_PASSWORD }}
          STAGING_JWT_SECRET=${{ secrets.STAGING_JWT_SECRET }}
          STAGING_ANON_KEY=${{ secrets.STAGING_ANON_KEY }}
          STAGING_SERVICE_ROLE_KEY=${{ secrets.STAGING_SERVICE_ROLE_KEY }}
          STAGING_MINIO_ROOT_USER=${{ secrets.STAGING_MINIO_ROOT_USER }}
          STAGING_MINIO_ROOT_PASSWORD=${{ secrets.STAGING_MINIO_ROOT_PASSWORD }}
          STAGING_MINIO_DATA_PATH=/var/lib/bloom/staging/minio
          EOF

      - name: Build images
        run: |
          docker-compose -f docker-compose.staging.yml build

      - name: Run database migrations
        run: |
          docker-compose -f docker-compose.staging.yml run --rm \
            -e DATABASE_URL=postgres://postgres:${{ secrets.STAGING_DB_PASSWORD }}@db:5432/bloom_staging \
            bloom-web pnpm db:migrate

      - name: Deploy with zero downtime
        run: |
          # Pull new images and recreate containers one by one
          docker-compose -f docker-compose.staging.yml up -d --remove-orphans

      - name: Wait for health checks
        run: |
          timeout 120 bash -c '
            until curl -sf http://localhost:3080/api/health; do
              echo "Waiting for staging to be healthy..."
              sleep 5
            done
          '
          echo "Staging deployment successful!"

      - name: Notify on failure
        if: failure()
        run: |
          echo "::error::Staging deployment failed!"
          # Add Slack/email notification here
```

### 4. Update Makefile

```makefile
# Staging commands
staging-up:
	docker-compose -f docker-compose.staging.yml up -d

staging-down:
	docker-compose -f docker-compose.staging.yml down

staging-logs:
	docker-compose -f docker-compose.staging.yml logs -f

staging-rebuild:
	docker-compose -f docker-compose.staging.yml build --no-cache
	docker-compose -f docker-compose.staging.yml up -d

staging-status:
	docker-compose -f docker-compose.staging.yml ps
```

## Directory Structure

```
/var/lib/bloom/
├── staging/
│   ├── minio/           # Staging MinIO data
│   └── db/              # Staging PostgreSQL data (if not using volumes/)
├── production/
│   ├── minio/           # Production MinIO data
│   └── db/              # Production PostgreSQL data
└── backups/
    ├── staging/
    └── production/

/home/github-runner/actions-runner/_work/bloom/bloom/
├── docker-compose.staging.yml
├── docker-compose.prod.yml
├── .env.staging          # Created by workflow from secrets
└── .env.prod             # Created by workflow from secrets
```

## GitHub Secrets Required

Configure in: Repository → Settings → Secrets and variables → Actions

| Secret | Description |
|--------|-------------|
| `STAGING_DB_PASSWORD` | PostgreSQL password for staging |
| `STAGING_JWT_SECRET` | JWT signing secret |
| `STAGING_ANON_KEY` | Supabase anonymous key |
| `STAGING_SERVICE_ROLE_KEY` | Supabase service role key |
| `STAGING_MINIO_ROOT_USER` | MinIO admin username |
| `STAGING_MINIO_ROOT_PASSWORD` | MinIO admin password |

## Verification Checklist

- [ ] Staging stack starts successfully
- [ ] Database is isolated from production
- [ ] MinIO buckets are separate
- [ ] Web app accessible on staging ports
- [ ] Automatic deployment triggers on push to `dev`
- [ ] Health checks pass after deployment
- [ ] Can rollback by redeploying previous commit

## Rollback Procedure

```bash
# Manual rollback to previous version
git checkout <previous-commit>
docker-compose -f docker-compose.staging.yml build bloom-web
docker-compose -f docker-compose.staging.yml up -d bloom-web
```

## References

- [Docker Compose Best Practices](https://docs.docker.com/compose/production/)
- [GitHub Environments](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)