# Design: Supabase Dashboard Subpath Serving

## Context

The Bloom project self-hosts Supabase on a local server with nginx as a reverse proxy in front of Kong Gateway and Supabase Studio. The architecture requires serving multiple services under different subpaths:

- Frontend: `/` (currently commented out)
- Supabase API/Dashboard: `/supabase_kong/`
- MinIO Console: `/minio/`

**Constraints:**
- Nginx must route traffic to containerized services
- Production deployment at `https://api.bloom.salk.edu/supabase_kong`
- Cannot modify Kong Gateway or Supabase Studio source code
- Must preserve existing MinIO subpath functionality
- Docker compose orchestration for both dev and prod environments

**Stakeholders:**
- Development team needing local dashboard access
- Production deployment requiring HTTPS subpath serving
- End users accessing the Bloom web application

## Goals / Non-Goals

**Goals:**
- Serve Supabase Studio dashboard correctly at `/supabase_kong/` subpath
- Load all static assets (CSS, JS, images) with correct paths
- Preserve Kong Gateway API functionality
- Maintain authentication flow through nginx proxy
- Support both development (localhost) and production (https://api.bloom.salk.edu) environments

**Non-Goals:**
- Serving dashboard at root path (requirement is subpath)
- Modifying Supabase Studio or Kong source code
- Custom build of Supabase components
- Supporting multiple Supabase instances simultaneously

## Decisions

### Decision 1: Use Studio's Built-in Base Path Support

**What:** Configure Supabase Studio container with `STUDIO_BASE_PATH` environment variable instead of relying on nginx URL rewriting.

**Why:**
- Modern Next.js applications (which Studio is built on) support base path configuration natively
- Nginx `sub_filter` cannot rewrite JavaScript-generated URLs or API responses
- Path rewriting at the proxy level breaks SPA routing and asset loading
- Supabase Studio v2.x includes base path support via environment variables

**Alternatives considered:**
1. **Path rewriting with sub_filter** (current approach): Fails because it only rewrites static HTML, not dynamic JavaScript or API responses
2. **Custom nginx module for deep content inspection**: Too complex, maintenance burden, performance overhead
3. **Separate subdomain for dashboard**: Requires additional DNS/SSL configuration, cross-origin complications
4. **Serving Studio separately on different port**: Requires firewall rules, complicates deployment

### Decision 2: Minimal nginx Proxy Configuration

**What:** Use simple `proxy_pass` to Kong without path manipulation, letting the application handle its own routing.

**Why:**
- Simpler configuration is easier to debug and maintain
- Reduces points of failure in the request path
- Follows reverse proxy best practices (let the app handle its own URLs)
- Kong Gateway can handle routing based on the full request path

**Configuration pattern:**
```nginx
location /supabase_kong/ {
  proxy_pass http://kong:8000/;
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header X-Forwarded-Prefix /supabase_kong;
}
```

### Decision 3: Pass Subpath Context via Headers

**What:** Add `X-Forwarded-Prefix` header to inform Kong/Studio of the subpath.

**Why:**
- Standard header used by reverse proxies to communicate subpath information
- Kong Gateway and modern web frameworks respect this header
- Allows the application to construct correct URLs in responses
- Non-invasive approach that doesn't require application code changes

## Risks / Trade-offs

### Risk: Studio Version Compatibility
- **Risk:** Supabase Studio might not support `STUDIO_BASE_PATH` in the current version (2025.06.30-sha-6f5982d)
- **Mitigation:**
  - Verify environment variable support in Supabase Studio documentation
  - Test in development environment before production deployment
  - Fallback: Use `NEXT_PUBLIC_BASE_PATH` (Next.js standard variable) if `STUDIO_BASE_PATH` doesn't work
  - Ultimate fallback: Upgrade to newer Studio version that supports base path

### Risk: Kong Dashboard Authentication
- **Risk:** Kong's built-in dashboard (if used) might have different subpath requirements than Studio
- **Mitigation:**
  - Kong Gateway dashboard is served by Kong itself, not Studio
  - If Kong dashboard is needed, configure separate location block (e.g., `/kong_admin/`)
  - Current setup uses Studio as primary dashboard interface

### Trade-off: Path Handling Complexity
- **Trade-off:** Must ensure consistent trailing slash handling between nginx and application
- **Impact:** Missing or extra trailing slashes can cause 404s or redirect loops
- **Resolution:** Use trailing slash in both `location` directive and `proxy_pass` target OR neither, but be consistent

## Migration Plan

### Pre-deployment Steps
1. Back up current nginx configuration
2. Test configuration in development environment
3. Verify Kong routing doesn't require additional changes
4. Document rollback procedure

### Deployment Steps
1. Update nginx.conf.template with new configuration
2. Update docker-compose.dev.yml with Studio environment variable
3. Update docker-compose.prod.yml with Studio environment variable
4. Restart nginx container (or run envsubst + reload)
5. Restart Studio container with new environment variable
6. Verify dashboard access and asset loading

### Verification
- Dashboard accessible at `/supabase_kong/`
- All CSS styles load correctly
- JavaScript executes without errors
- Images and icons display
- Authentication flow works
- API requests route correctly through Kong

### Rollback Plan
If issues occur:
1. Restore previous nginx.conf.template
2. Remove `STUDIO_BASE_PATH` environment variable from docker-compose files
3. Restart affected containers
4. Original behavior restored (broken static assets, but no worse than before)

### Post-deployment
- Monitor nginx error logs for 404s or proxy errors
- Monitor browser console for failed asset requests
- Document the working configuration for future reference

## Open Questions

1. **Kong routing configuration**: Does Kong's declarative config (kong.yml) need updates to route `/supabase_kong/*` requests to Studio container?
   - *Resolution needed*: Check Kong routes and ensure Studio endpoint is accessible
   - *If kong.yml exists*: May need to add route for `/supabase_kong` prefix

2. **Studio container direct access**: Should Studio be exposed directly (current: port 55323:3000) or only through nginx?
   - *Current state*: Both dev and prod expose Studio directly on port 55323
   - *Recommendation*: Keep direct access for development/debugging, block in production firewall

3. **Base path environment variable**: Which environment variable does Supabase Studio actually use?
   - Possibilities: `STUDIO_BASE_PATH`, `NEXT_PUBLIC_BASE_PATH`, `BASE_PATH`
   - *Action required*: Check Supabase Studio documentation or source code
   - *Fallback*: Try multiple variables in order of likelihood

4. **Static frontend path**: The commented-out frontend location (`/`) - will this conflict once enabled?
   - *Current*: Frontend serving is commented out in nginx.conf.template
   - *Future consideration*: Ensure frontend doesn't try to claim `/supabase_kong` path when uncommented