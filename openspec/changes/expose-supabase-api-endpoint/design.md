# Design: Expose Supabase API via nginx at /api/ Subpath

## Context

The Bloom project uses nginx as a reverse proxy for multiple services:
- Frontend web application (planned for `/`)
- Supabase Studio dashboard (at `/supabase_kong/`)
- MinIO console (at `/minio/`)
- Kong Gateway API (currently port 8000 only)

Kong Gateway routes requests to Supabase backend services:
- Auth (GoTrue): `/auth/v1/*`
- REST API (PostgREST): `/rest/v1/*`
- Storage: `/storage/v1/*`
- Realtime: `/realtime/v1/*`
- Edge Functions: `/functions/v1/*`

**Current state:**
- Kong is accessible on port 8000 directly (bypassing nginx)
- CLI tools use `http://localhost:8000`
- Frontend apps reference `http://localhost:8000` in env vars
- No HTTPS path for API access in production
- Inconsistent access pattern with dashboard at `/supabase_kong/`

**Constraints:**
- Must maintain dashboard functionality at `/supabase_kong/`
- Must support CLI operations (upload, download, migrations)
- Must prepare for frontend at `/` (currently commented out)
- Must work in both dev (HTTP) and prod (HTTPS) environments
- Kong expects root-level paths (e.g., `/auth/v1`, not `/api/auth/v1`)
- Cannot modify Kong or Supabase service source code

## Goals / Non-Goals

**Goals:**
- Expose Kong Gateway API through nginx at `/api/` subpath
- Support Supabase CLI operations through nginx proxy
- Enable HTTPS access to API in production
- Maintain clear separation between dashboard UI and API endpoints
- Prepare URL structure for when frontend is enabled at `/`
- Keep container-to-container communication direct (no nginx overhead)

**Non-Goals:**
- Modifying Kong Gateway configuration or routes
- Changing how Kong routes to backend services
- Supporting the old `:8000` direct access pattern (breaking change is acceptable)
- Custom authentication or rate limiting at nginx level (Kong handles this)

## Decisions

### Decision 1: Use /api/ Subpath with Path Rewriting

**What:** Proxy `/api/*` requests to Kong at `kong:8000/*`, stripping the `/api` prefix.

**Why:**
- Kong's declarative configuration expects root-level paths (`/auth/v1`, `/rest/v1`, etc.)
- Clients can use clean URLs: `http://localhost/api/auth/v1/signup`
- Consistent with dashboard at `/supabase_kong/` (both use subpaths)
- Leaves `/` available for the Bloom frontend application
- Standard pattern for API separation in nginx

**Configuration approach:**
```nginx
location /api/ {
  rewrite ^/api/(.*)$ /$1 break;
  proxy_pass http://kong:8000;
  # ... headers ...
}
```

**Alternatives considered:**
1. **No path rewriting** (`/api/api/auth/v1`): Requires reconfiguring Kong routes - too complex
2. **Root path** (`/`): Conflicts with planned frontend at `/`, not viable
3. **Keep port 8000**: No HTTPS in production, inconsistent with dashboard approach

### Decision 2: Dual URL Configuration (Internal vs External)

**What:** Use two different Supabase URL patterns:
- **Internal (container-to-container):** `http://kong:8000` - Direct, no nginx overhead
- **External (browser/CLI):** `http://localhost/api` or `https://api.bloom.salk.edu/api` - Through nginx

**Why:**
- Container-to-container communication doesn't need nginx (lower latency)
- Studio container can talk directly to Kong without HTTP overhead
- External clients (browsers, CLI) benefit from nginx (HTTPS, logging, single entry point)
- Environment variables allow different URLs for different contexts

**Implementation:**
- `SUPABASE_URL`: Internal URL (`http://kong:8000`) - Used by Studio server-side
- `SUPABASE_PUBLIC_URL`: External URL (`http://localhost/api`) - Used by browsers/CLI
- `NEXT_PUBLIC_SUPABASE_URL`: External URL - Used by Next.js frontend client-side

**Trade-off:** Slightly more complex configuration, but better performance and security

### Decision 3: Breaking Change for CLI and External Tools

**What:** Require all CLI tools and external scripts to update from `:8000` to `/api`.

**Why:**
- Clean architecture is worth the migration cost
- Only affects development team (not end users)
- Can be communicated clearly and migrated systematically
- Port 8000 can remain exposed temporarily during migration if needed

**Migration strategy:**
1. Document the change in PR and deployment notes
2. Update all internal scripts and env files
3. Keep port 8000 exposed during transition period
4. Remove port 8000 exposure after full migration

### Decision 4: Path Rewriting Strategy

**What:** Use nginx `rewrite` directive to strip `/api` prefix before proxying to Kong.

**Why:**
- Kong's routes are configured for root-level paths
- Simpler than reconfiguring all Kong routes to expect `/api` prefix
- Standard nginx pattern for path manipulation
- Transparent to Kong - no application changes needed

**Pattern:**
```nginx
location /api/ {
  rewrite ^/api/(.*)$ /$1 break;
  proxy_pass http://kong:8000;
}
```

This transforms:
- `http://localhost/api/auth/v1/signup` → `http://kong:8000/auth/v1/signup`
- `http://localhost/api/rest/v1/todos` → `http://kong:8000/rest/v1/todos`

## Risks / Trade-offs

### Risk: Breaking Existing CLI/Script Usage

- **Risk:** Developers' local scripts and CLI configurations use `http://localhost:8000`
- **Mitigation:**
  - Clear documentation in PR and deployment notes
  - Migration checklist for updating local environments
  - Keep port 8000 exposed temporarily during transition
  - Update all example commands in documentation

### Risk: WebSocket/Realtime Connection Issues

- **Risk:** Supabase Realtime uses WebSockets; path rewriting might break upgrade headers
- **Mitigation:**
  - nginx automatically handles WebSocket upgrade headers with `proxy_pass`
  - Add `proxy_http_version 1.1;` and WebSocket headers explicitly
  - Test realtime subscriptions thoroughly
  - Fallback: Add separate location block for `/api/realtime/` if needed

### Risk: Path Rewriting Edge Cases

- **Risk:** Some Kong routes might behave unexpectedly with stripped prefix
- **Mitigation:**
  - Test all major Supabase service endpoints (auth, rest, storage, realtime)
  - Use `break` flag to prevent further rewriting
  - Monitor nginx error logs during initial deployment
  - Kong's routes are well-defined and standard, low risk

### Trade-off: Dual URL Configuration Complexity

- **Trade-off:** Developers must understand internal vs external URLs
- **Impact:** More environment variables to configure, potential confusion
- **Resolution:**
  - Document clearly with inline comments in docker-compose files
  - Provide examples in README
  - Benefits (performance, security) outweigh complexity cost

## URL Structure

### Development Environment

| Service | URL | Purpose |
|---------|-----|---------|
| Frontend (future) | `http://localhost/` | Bloom web application |
| Studio Dashboard | `http://localhost/supabase_kong/` | Database management UI |
| Supabase API | `http://localhost/api/` | REST, Auth, Storage, Realtime |
| MinIO Console | `http://localhost/minio/` | Object storage management |
| Kong (internal) | `http://kong:8000/` | Container-to-container only |

### Production Environment

| Service | URL | Purpose |
|---------|-----|---------|
| Frontend (future) | `https://api.bloom.salk.edu/` | Bloom web application |
| Studio Dashboard | `https://api.bloom.salk.edu/supabase_kong/` | Database management UI |
| Supabase API | `https://api.bloom.salk.edu/api/` | REST, Auth, Storage, Realtime |
| MinIO Console | `https://api.bloom.salk.edu/minio/` | Object storage management |
| Kong (internal) | `http://kong:8000/` | Container-to-container only |

### Example API Endpoints

With the `/api/` prefix, Supabase services are accessed as:

```
POST   http://localhost/api/auth/v1/signup
POST   http://localhost/api/auth/v1/token?grant_type=password
GET    http://localhost/api/rest/v1/todos
POST   http://localhost/api/storage/v1/object/bucket-name/file.jpg
WS     ws://localhost/api/realtime/v1/websocket
```

## Migration Plan

### Pre-deployment

1. Review and approve OpenSpec proposal
2. Identify all scripts/tools using `http://localhost:8000`
3. Prepare updated CLI commands and examples
4. Test in isolated dev environment first

### Deployment Steps

1. **Update nginx configuration:**
   - Add `/api/` location block with path rewriting
   - Verify configuration syntax: `nginx -t`

2. **Update docker-compose files:**
   - Modify `NEXT_PUBLIC_SUPABASE_URL` environment variables
   - Modify `SUPABASE_PUBLIC_URL` environment variables
   - Add documentation comments

3. **Restart services:**
   ```bash
   docker compose -f docker-compose.dev.yml down
   docker compose -f docker-compose.dev.yml up -d
   ```

4. **Update developer environments:**
   - Notify team of URL changes
   - Update CLI configuration
   - Update local scripts

5. **Verify functionality:**
   - Test all endpoints in checklist
   - Monitor nginx access logs
   - Check for errors in application logs

### Verification Checklist

- [ ] CLI auth: `supabase login` with new URL
- [ ] CLI storage: Upload/download files
- [ ] PostgREST: Query database via REST API
- [ ] Auth: Signup/login flows
- [ ] Storage: File upload/download
- [ ] Realtime: WebSocket connections (if used)
- [ ] Studio dashboard: Still loads and functions
- [ ] MinIO console: Still accessible

### Rollback Plan

If issues occur:

1. **Immediate rollback:**
   ```bash
   git revert <commit-hash>
   docker compose -f docker-compose.dev.yml down
   docker compose -f docker-compose.dev.yml up -d
   ```

2. **Environment variables:**
   - Revert to `http://localhost:8000` in env vars
   - Restart affected containers

3. **Temporary dual support:**
   - Keep port 8000 exposed
   - Allow both `:8000` and `/api/` during transition
   - Gradually migrate and then remove port 8000

### Post-deployment

1. Update all project documentation with new URLs
2. Create wiki page or doc explaining URL structure
3. Archive old CLI examples, replace with new ones
4. Monitor for issues over 1-2 weeks
5. Consider removing port 8000 exposure after full migration confirmed

## Open Questions

1. **Frontend Supabase client configuration:** When the Bloom frontend is enabled at `/`, will it use `http://localhost/api` or a different pattern?
   - *Recommendation*: Use `NEXT_PUBLIC_SUPABASE_URL=http://localhost/api` for consistency

2. **Flask app Supabase URL:** Should Flask use internal `http://kong:8000` or external `/api/`?
   - *Current*: Uses `FLASK_SUPABASE_URL` from env
   - *Recommendation*: Use internal `http://kong:8000` if Flask runs in Docker network, external if separate

3. **WebSocket proxy configuration:** Do we need special nginx config for Realtime WebSockets?
   - *Action*: Test realtime connections after implementation
   - *Fallback*: Add explicit WebSocket headers if needed:
     ```nginx
     proxy_http_version 1.1;
     proxy_set_header Upgrade $http_upgrade;
     proxy_set_header Connection "upgrade";
     ```

4. **Rate limiting and security:** Should nginx implement rate limiting or leave it to Kong?
   - *Current approach*: Kong handles auth and rate limiting
   - *Recommendation*: Keep security in Kong, nginx is just proxy
   - *Future*: Could add nginx-level rate limiting for additional protection

5. **Monitoring and logging:** Should we add specific logging for API requests?
   - *Current*: nginx logs all requests in access.log
   - *Recommendation*: Sufficient for now, can add custom log formats later
