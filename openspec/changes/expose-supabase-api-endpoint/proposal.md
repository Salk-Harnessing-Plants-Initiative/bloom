# Expose Supabase API (Kong Gateway) at /api/ Subpath

## Why

Currently, the Supabase API (Kong Gateway) is only accessible directly via port 8000 (`http://localhost:8000` in dev, `http://kong:8000` container-to-container). This creates several problems:

- **CLI operations** (upload/download via Supabase CLI) cannot work through nginx's reverse proxy
- **Production HTTPS**: Direct port access bypasses nginx, requiring separate SSL/TLS configuration for Kong
- **Inconsistent access patterns**: Dashboard at `/supabase_kong/`, API at `:8000` - different protocols/hosts
- **Frontend application**: When the Bloom frontend is enabled, it needs a consistent way to call the Supabase API through nginx
- **Security**: Exposing Kong directly on port 8000 creates an additional attack surface

The recent fix for the dashboard (PR #2) routes `/supabase_kong/` to Studio but doesn't expose the Kong Gateway API through nginx, leaving CLI and programmatic access broken for production HTTPS scenarios.

## What Changes

- Add nginx location block to proxy `/api/` subpath to Kong Gateway at `kong:8000`
- Configure nginx to strip the `/api` prefix before forwarding to Kong (Kong expects root-level paths)
- Update environment variables in docker-compose files to use the new `/api/` endpoint:
  - `NEXT_PUBLIC_SUPABASE_URL`: From `http://localhost:8000` to `http://localhost/api`
  - `SUPABASE_PUBLIC_URL`: From `http://localhost:8000` to `http://localhost/api` (dev) or `https://api.bloom.salk.edu/api` (prod)
  - Flask app `SUPABASE_URL`: Update to use nginx-proxied endpoint
- Document the URL structure in configuration comments
- Maintain backward compatibility by keeping port 8000 exposed for container-to-container communication

**Architecture:**
- Frontend (Bloom web): `/` (when enabled)
- Studio Dashboard: `/supabase_kong/`
- Supabase API: `/api/`
- MinIO Console: `/minio/`

## Impact

- Affected specs: `infrastructure` (modify existing capability spec from PR #2)
- Affected code:
  - [nginx/nginx.conf.template](nginx/nginx.conf.template) - Add new `/api/` location block
  - [docker-compose.dev.yml](docker-compose.dev.yml) - Update `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_PUBLIC_URL`, and Flask `SUPABASE_URL`
  - [docker-compose.prod.yml](docker-compose.prod.yml) - Update `SUPABASE_PUBLIC_URL`
- **Breaking changes**:
  - CLI tools must update endpoint from `http://localhost:8000` to `http://localhost/api` (dev) or `https://api.bloom.salk.edu/api` (prod)
  - Any external scripts or tools accessing the Supabase API must update their URLs
  - Environment variables change (migration required)
- Dependencies: None (uses existing Kong Gateway)
- Testing required:
  - Supabase CLI operations (upload, download, migrations)
  - Frontend API calls (when frontend is enabled)
  - Studio dashboard API integration
  - Authentication flows
  - Storage operations