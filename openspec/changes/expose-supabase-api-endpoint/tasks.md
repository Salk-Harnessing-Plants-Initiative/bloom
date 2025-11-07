# Implementation Tasks

## 1. Update nginx Configuration
- [ ] 1.1 Add new location block for `/api/` that proxies to `kong:8000`
- [ ] 1.2 Configure path rewriting to strip `/api` prefix before forwarding to Kong
- [ ] 1.3 Add standard reverse proxy headers (Host, X-Real-IP, X-Forwarded-For, X-Forwarded-Proto)
- [ ] 1.4 Add comments documenting the API endpoint and its purpose
- [ ] 1.5 Ensure location block ordering is correct (more specific paths first)

## 2. Update Development Environment Configuration
- [ ] 2.1 Update `bloom-web` service `NEXT_PUBLIC_SUPABASE_URL` from `http://localhost:8000` to `http://localhost/api`
- [ ] 2.2 Update `studio` service `SUPABASE_PUBLIC_URL` from `http://localhost:8000` to `http://localhost/api`
- [ ] 2.3 Update `flask-app` service `SUPABASE_URL` to use nginx-proxied endpoint (if applicable)
- [ ] 2.4 Keep `studio` service `SUPABASE_URL: http://kong:8000` for container-to-container communication
- [ ] 2.5 Add comments explaining the dual URL configuration (internal vs external)

## 3. Update Production Environment Configuration
- [ ] 3.1 Update `studio` service `SUPABASE_PUBLIC_URL` from `http://kong:8000` to `https://api.bloom.salk.edu/api`
- [ ] 3.2 Verify `SUPABASE_URL` remains as `http://kong:8000` for internal container communication
- [ ] 3.3 Add comments documenting production URL structure

## 4. Documentation
- [ ] 4.1 Document the URL structure in nginx configuration comments
- [ ] 4.2 Create or update README with endpoint information:
  - Development: `http://localhost/api`
  - Production: `https://api.bloom.salk.edu/api`
- [ ] 4.3 Document CLI usage examples with new endpoints
- [ ] 4.4 Add migration notes for developers using old endpoints

## 5. Testing
- [ ] 5.1 Test Supabase CLI operations (auth, db, storage) using `http://localhost/api`
- [ ] 5.2 Test Studio dashboard can connect to API services
- [ ] 5.3 Verify authentication flows work through `/api/` endpoint
- [ ] 5.4 Test storage upload/download operations
- [ ] 5.5 Verify PostgREST queries work through `/api/rest/v1/`
- [ ] 5.6 Test realtime connections (if applicable)
- [ ] 5.7 Ensure MinIO console at `/minio/` still works (no regression)
- [ ] 5.8 Ensure Studio dashboard at `/supabase_kong/` still works (no regression)

## 6. Migration Support
- [ ] 6.1 Document environment variable changes needed
- [ ] 6.2 Create migration script or checklist for updating existing deployments
- [ ] 6.3 Test rollback procedure in case of issues
