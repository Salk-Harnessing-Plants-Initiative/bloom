# Implementation Tasks

## 1. Update nginx Configuration
- [x] 1.1 Remove the aggressive path rewrite rule (`rewrite ^/supabase_kong(/.*)$ $1 break;`)
- [x] 1.2 Update `proxy_pass` to point to Studio container directly (http://studio:3000/)
- [x] 1.3 Remove ineffective `sub_filter` directives
- [x] 1.4 Remove `proxy_redirect` directive that causes incorrect redirects
- [x] 1.5 Update proxy headers to preserve original request path information
- [x] 1.6 Add `X-Forwarded-Prefix` header to communicate subpath to application

## 2. Configure Studio Container for Subpath
- [x] 2.1 Add `NEXT_PUBLIC_BASE_PATH=/supabase_kong` environment variable to dev studio service
- [x] 2.2 Add `NEXT_PUBLIC_BASE_PATH=/supabase_kong` environment variable to prod studio service
- [x] 2.3 Used `NEXT_PUBLIC_BASE_PATH` (Next.js standard) instead of `STUDIO_BASE_PATH`

## 3. Update Kong Routing (if needed)
- [x] 3.1 Determined that nginx now routes directly to Studio, bypassing Kong for dashboard access
- [x] 3.2 Kong remains accessible for API calls on port 8000 (container-to-container)
- [x] 3.3 Added comment in nginx config about optional Kong API exposure

## 4. Testing
- [ ] 4.1 Start development environment and verify dashboard loads at `http://localhost/supabase_kong`
- [ ] 4.2 Verify all static assets (CSS, JS, images) load correctly
- [ ] 4.3 Test dashboard authentication flow
- [ ] 4.4 Test navigation within the dashboard
- [ ] 4.5 Verify MinIO console still works at `/minio/` (ensure no regression)
- [ ] 4.6 Document the production URL pattern (`https://api.bloom.salk.edu/supabase_kong`)

## 5. Documentation
- [x] 5.1 Update nginx configuration comments to explain subpath serving approach
- [x] 5.2 Add inline comments explaining the Studio base path configuration in docker-compose files

## 6. Implementation Notes
- Changed approach: nginx now proxies `/supabase_kong/` directly to `studio:3000` instead of through Kong
- This ensures Studio receives all requests and can properly handle base path routing
- Kong Gateway remains available for API calls but is not in the dashboard request path