# Migration Issues Analysis

## Critical Security Issues

1. **Secrets in Repository**: `.env.dev` contains production secrets (JWT, passwords, keys)
2. **Hardcoded Credentials in Dockerfiles**: ENV variables with API keys in `web/Dockerfile.bloom-web.dev`
3. **Hardcoded Absolute Paths**: `/Users/benficaa/` paths in docker-compose files won't work on other systems

## Configuration Issues

1. **Environment Variable Typo**: `MINO_S3_STORAGE_REGION` should be `MINIO_S3_STORAGE_REGION`
2. **Database Service Name Mismatch**: Dev uses `db-dev`, prod uses `db-prod` - ensure POSTGRES_HOST matches
3. **Workspace Path Mismatch**: package.json references `bloom-v2/*` which doesn't exist

## Dependency Issues

1. **React Version Conflict**: Root overrides to 18.2.0, web depends on 19.2.0
2. **Package Manager Inconsistency**: packageManager specifies pnpm but scripts use npm
3. **Missing Flask Dependencies**: Dockerfile only copies app.py, missing config.py and videoWriter.py

## Production Completeness

1. **Missing Services in Prod**: No flask-app, meta, or analytics services in prod compose
2. **Missing Health Checks**: Production lacks health checks present in dev
3. **README Errors**: Production command listed as "make dev" instead of "make prod"

## Code Quality

1. **Debug Code**: Multiple print() statements in Flask app
2. **No Environment Validation**: config.py doesn't check for missing required vars
3. **Duplicate Exception Handling**: flask/app.py line 38-42
4. **Empty Experimental Config**: serverActions: {} in next.config.js should be removed if unused
