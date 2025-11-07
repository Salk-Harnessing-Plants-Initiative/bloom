# Fix Supabase Dashboard Subpath Serving

## Why

The Supabase dashboard (Studio) is currently failing to load static assets when served through nginx at `/supabase_kong` (localhost development) or `https://api.bloom.salk.edu/supabase_kong` (production). The issue manifests as:

- Static files (CSS, JS, images) fail to load with 404 errors
- Browser requests assets at root paths (e.g., `/assets/main.js`) instead of subpath-aware paths (e.g., `/supabase_kong/assets/main.js`)
- Dashboard UI appears broken or non-functional

Root cause: The nginx reverse proxy configuration strips the `/supabase_kong` prefix before forwarding to Kong, but the Supabase Studio application generates URLs assuming it's served from the root path. The current `sub_filter` approach is insufficient because it only rewrites static HTML content, not dynamically generated JavaScript URLs or API responses.

## What Changes

- **BREAKING**: Remove the path rewrite rule that strips `/supabase_kong` from nginx configuration
- Add proper nginx proxy configuration to preserve the subpath context
- Configure Supabase Studio container with `STUDIO_BASE_PATH` environment variable to make it aware of the subpath
- Update nginx proxy headers to correctly pass the request URI and path information
- Remove ineffective `sub_filter` directives that don't work for SPA applications
- Ensure Kong gateway dashboard authentication works correctly with the subpath

## Impact

- Affected specs: `infrastructure` (new capability spec)
- Affected code:
  - [nginx/nginx.conf.template](nginx/nginx.conf.template) - lines 42-56 (supabase_kong location block)
  - [docker-compose.dev.yml](docker-compose.dev.yml) - lines 58-95 (studio service configuration)
  - [docker-compose.prod.yml](docker-compose.prod.yml) - lines 265-291 (studio service configuration)
- Dependencies: Supabase Studio 2025.06.30-sha-6f5982d (already in use)
- Testing required: Manual verification that dashboard loads correctly at `/supabase_kong` with all static assets