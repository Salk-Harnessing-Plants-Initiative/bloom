# Troubleshooting Guide: Nginx & Supabase Authentication Issues

This document chronicles all major issues encountered during the setup of the production environment with Nginx reverse proxy and Supabase authentication, along with their root causes and solutions.

---

## Table of Contents

1. [Authentication Issues](#authentication-issues)
   - [Cookie Storage Key Mismatch](#1-cookie-storage-key-mismatch-root-cause)
   - [JWT Signature Validation Errors](#2-jwt-signature-validation-errors)
   - [Middleware Cookie Sync Issues](#3-middleware-cookie-sync-issues)
2. [Server-Side Rendering (SSR) Build Errors](#server-side-rendering-ssr-build-errors)
   - [document is not defined](#1-document-is-not-defined)
   - [window is not defined](#2-window-is-not-defined)
3. [Nginx Routing Issues](#nginx-routing-issues)
   - [Frontend Not Loading After Rebuild](#1-frontend-not-loading-after-rebuild)
   - [Studio Subpath Routing Failure](#2-studio-subpath-routing-failure)
4. [Network Architecture](#network-architecture)
5. [Final Working Configuration](#final-working-configuration)

---

## Authentication Issues

### 1. Cookie Storage Key Mismatch (ROOT CAUSE)

**Symptom:**
- Login worked in direct container access (curl from nginx container)
- Browser login failed - middleware couldn't read authentication session
- Logs showed: `[Middleware] Auth session missing!`
- Cookie was present in browser with correct format: `sb-localhost-auth-token`

**Root Cause:**
The `@supabase/ssr` library derives the cookie storage key from the hostname in the `SUPABASE_URL`:
- **Browser client** used `NEXT_PUBLIC_SUPABASE_URL=http://localhost/api` → created cookie with key `sb-localhost-auth-token`
- **Server-side clients** (middleware, server components) used `SUPABASE_URL=http://kong:8000` → looked for cookie with key `sb-kong-auth-token`

This mismatch meant the server couldn't read the cookies created by the browser client.

**Investigation Steps:**
1. Checked cookie format - confirmed it was correct (`base64-` prefix expected by @supabase/ssr)
2. Verified JWT token was valid via direct curl to Kong - succeeded
3. Added logging to middleware - cookie was present (count: 1) but `getUser()` returned null
4. Discovered the storage key derivation from hostname in @supabase/ssr source

**Solution:**
Explicitly set `cookieOptions.name` in all Supabase client configurations to override hostname-based key derivation:

**File: `web/middleware.ts`**
```typescript
const supabase = createServerClient(
  process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_ANON_KEY!,
  {
    cookies: {
      getAll() { return request.cookies.getAll() },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value, options }) => {
          request.cookies.set(name, value)
        })
        response = NextResponse.next({ request })
        cookiesToSet.forEach(({ name, value, options }) => {
          response.cookies.set(name, value, options)
        })
      },
    },
    cookieOptions: { name: 'sb-localhost-auth-token' } // KEY FIX
  }
)
```

**File: `web/lib/supabase/server.ts`**
```typescript
const supabase = createServerClient(
  process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_ANON_KEY!,
  {
    cookies: cookieStore,
    cookieOptions: { name: 'sb-localhost-auth-token' } // KEY FIX
  }
)
```

**File: `web/lib/supabase/client.ts`**
```typescript
const supabase = createBrowserClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_ANON_KEY!,
  {
    cookieOptions: { name: 'sb-localhost-auth-token' } // KEY FIX
  }
)
```

**Key Lesson:**
When using different URLs for browser and server in a reverse proxy setup, always explicitly set `cookieOptions.name` to ensure all clients use the same cookie storage key.

---

### 2. JWT Signature Validation Errors

**Symptom:**
```
JWT verification failed: signature is invalid
```

**Root Cause:**
The `SUPABASE_ANON_KEY` was signed with a different `JWT_SECRET` than the one configured in GoTrue. This happened because the ANON_KEY was generated before the JWT_SECRET was finalized.

**Solution:**
1. Regenerated the ANON_KEY using the correct JWT_SECRET:
```bash
docker exec -it bloom_v2_prod-kong-1 sh
# Inside container:
# npm install -g jsonwebtoken
# node
const jwt = require('jsonwebtoken');
const secret = 'super-secret-jwt-token-with-at-least-32-characters-long';
const payload = { role: 'anon', iss: 'supabase', iat: Math.floor(Date.now() / 1000) };
const token = jwt.sign(payload, secret, { expiresIn: '10y' });
console.log(token);
```

2. Updated `SUPABASE_ANON_KEY` in `docker-compose.prod.yml` for all services:
   - bloom-web
   - studio
   - kong (via .env file)

3. Restarted all containers to apply the new key.

**Verification:**
```bash
curl -X POST http://kong:8000/auth/v1/token \
  -H "Authorization: Bearer <NEW_ANON_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"email":"testuser1@salk.edu","password":"testuser1"}'
```

Should return `200 OK` with access token.

---

### 3. Middleware Cookie Sync Issues

**Symptom:**
- Cookies set in middleware weren't persisting to browser
- Session state not maintained across requests

**Root Cause:**
Initial middleware implementation only set cookies on the request object, not on the response. The canonical Supabase SSR pattern requires setting cookies on both the request (for same-request reads) and response (for client persistence).

**Solution:**
Implemented the canonical Supabase SSR middleware pattern:

```typescript
export async function middleware(request: NextRequest) {
  let response = NextResponse.next({ request })

  const supabase = createServerClient(
    process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll()
        },
        setAll(cookiesToSet) {
          // Set on request for immediate reads
          cookiesToSet.forEach(({ name, value }) => {
            request.cookies.set(name, value)
          })
          // Create new response with updated request
          response = NextResponse.next({ request })
          // Set on response for client persistence
          cookiesToSet.forEach(({ name, value, options }) => {
            response.cookies.set(name, value, options)
          })
        },
      },
      cookieOptions: { name: 'sb-localhost-auth-token' }
    }
  )

  const { data: { user } } = await supabase.auth.getUser()

  // Redirect logic...
  return response
}
```

**Key Points:**
- `setAll` must write to both request and response
- Create new response with updated request before setting response cookies
- This ensures cookies are available immediately and persist to the browser

---

## Server-Side Rendering (SSR) Build Errors

### 1. `document is not defined`

**Symptom:**
```
ReferenceError: document is not defined
    at createBrowserClient (webpack-internal:///(rsc)/./lib/supabase/client.ts:8:45)
```

**Root Cause:**
Custom cookie handlers in `client.ts` were using `document.cookie` which doesn't exist in SSR context (server-side rendering during build).

**Original Problematic Code:**
```typescript
auth: {
  storage: {
    getItem: (key: string) => {
      const cookies = document.cookie.split(';') // ERROR: document not available
      // ...
    },
    setItem: (key: string, value: string) => {
      document.cookie = `${key}=${value}; path=/; max-age=31536000` // ERROR
    }
  }
}
```

**Solution:**
Removed custom storage configuration entirely - let `@supabase/ssr` handle it automatically:

```typescript
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.SUPABASE_ANON_KEY!,
    {
      cookieOptions: { name: 'sb-localhost-auth-token' }
      // No custom storage needed - @supabase/ssr handles it
    }
  )
}
```

---

### 2. `window is not defined`

**Symptom:**
```
ReferenceError: window is not defined
    at createBrowserClient (webpack-internal:///(rsc)/./lib/supabase/client.ts:12:30)
```

**Root Cause:**
Reference to `window.localStorage` in client.ts during server-side build.

**Original Problematic Code:**
```typescript
auth: {
  storage: window.localStorage // ERROR: window not available in SSR
}
```

**Solution:**
Same as above - removed all custom storage configuration. The `@supabase/ssr` library properly handles client vs server contexts automatically.

---

## Nginx Routing Issues

### 1. Frontend Not Loading After Rebuild

**Symptom:**
- After rebuilding bloom-web container, nginx returned 502 Bad Gateway
- `curl http://nginx/` from inside containers failed

**Root Cause:**
Docker assigned a new IP address to the bloom-web container after rebuild, but nginx had cached the old IP address.

**Solution:**
Restart nginx after any bloom-web rebuild:

```bash
make prod-up  # Rebuilds bloom-web
docker-compose -f docker-compose.prod.yml restart nginx
```

**Alternative Fix:**
Configure nginx to use Docker's internal DNS resolution:

```nginx
location / {
  set $upstream http://bloom-web:3000;
  proxy_pass $upstream;
  # ... other config
}
```

This forces nginx to resolve the hostname on every request instead of caching the IP.

---

### 2. Studio Subpath Routing Failure

**Symptom:**
- Accessing `http://localhost/studio/` showed the main Bloom app instead of Supabase Studio
- Studio expected to run on subpath but returned wrong content

**Root Cause:**
Next.js applications (like Supabase Studio) use client-side routing with absolute paths. When running on a subpath without proper base path configuration:

1. **Hardcoded Absolute URLs**: Studio's code has links like `<a href="/project/...">` which resolve to `http://localhost/project/` instead of `http://localhost/studio/project/`

2. **Asset Loading**: Static assets (JS, CSS) are requested from root: `/static/main.js` → `http://localhost/static/main.js` instead of `http://localhost/studio/static/main.js`

3. **Client-Side Navigation**: React Router/Next.js router navigates to absolute paths without the `/studio/` prefix

4. **Redirects**: Server-side redirects (e.g., auth redirects) go to root path without subpath prefix

**Why URL Rewriting Doesn't Fully Work:**

The nginx configuration attempted:
```nginx
location /studio/ {
  rewrite ^/studio(/.*)$ $1 break;  # Remove /studio prefix
  proxy_pass http://studio:3000;    # Forward to app at root
}
```

This only handles incoming requests but doesn't:
- Rewrite URLs in HTML responses
- Modify JavaScript router navigation
- Fix redirects back from the app
- Handle WebSocket connections with correct paths

**Solution: Subdomain Routing**

Instead of subpath routing, use subdomain routing where the app runs at the root of its own hostname:

**nginx.conf.template:**
```nginx
# Main application (default)
server {
  listen 80 default_server;
  server_name _;
  
  location /api/ {
    rewrite ^/api(/.*)$ $1 break;
    proxy_pass http://kong:8000;
  }
  
  location / {
    proxy_pass http://bloom-web:3000;
  }
}

# Supabase Studio (subdomain)
server {
  listen 80;
  server_name studio.localhost studio.*;
  
  location / {
    proxy_pass http://studio:3000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection 'upgrade';
    proxy_set_header Host $host;
    proxy_cache_bypass $http_upgrade;
  }
}
```

**Why This Works:**
- Studio runs at root of `studio.localhost` - all its absolute paths work correctly
- No URL rewriting needed - app's URLs match the served location
- Client-side routing works naturally
- Redirects work as expected
- Each app has its own "domain" space

**Local Development Setup:**

Add to `/etc/hosts`:
```
127.0.0.1   studio.localhost
```

Access:
- Main app: `http://localhost`
- Supabase API: `http://localhost/api`
- Supabase Studio: `http://studio.localhost`

**Production Deployment:**

Configure DNS records:
```
A    @               <server-ip>      # yoursite.com
A    studio          <server-ip>      # studio.yoursite.com
```

Update nginx:
```nginx
server_name studio.yoursite.com;
```

**Key Lessons:**
1. Next.js apps cannot easily run on subpaths without `basePath` configuration in `next.config.js`
2. Subdomain routing is simpler and more reliable for independent applications
3. Use subpaths for APIs and simple proxies, use subdomains for full applications
4. The `default_server` directive or first server block determines which handles non-matching hostnames

---

## Network Architecture

### Dual URL Strategy

The production setup uses different URLs for browser and server-side requests:

**Browser Requests** (via nginx):
```
NEXT_PUBLIC_SUPABASE_URL=http://localhost/api
```
- Browser → nginx (port 80) → `/api/` → Kong (port 8000)
- Public-facing URL
- Goes through nginx CORS and security headers

**Server-Side Requests** (direct):
```
SUPABASE_URL=http://kong:8000
```
- Server components → Kong directly on Docker network
- Faster (no nginx overhead)
- Internal network only

### Environment Variables

**docker-compose.prod.yml:**
```yaml
bloom-web:
  environment:
    # Server-side direct access
    SUPABASE_URL: http://kong:8000
    # Browser access through nginx
    NEXT_PUBLIC_SUPABASE_URL: http://localhost/api
    SUPABASE_ANON_KEY: ${SUPABASE_ANON_KEY}

studio:
  environment:
    # Studio backend connects directly
    SUPABASE_URL: http://kong:8000
    # Studio frontend connects through nginx
    SUPABASE_PUBLIC_URL: http://localhost/api
    SUPABASE_ANON_KEY: ${SUPABASE_ANON_KEY}
```

### Nginx Routes

```nginx
# Main App
location / {
  proxy_pass http://bloom-web:3000;
}

# Supabase API
location /api/ {
  rewrite ^/api(/.*)$ $1 break;
  proxy_pass http://kong:8000;
}

# Studio (separate server block)
server {
  server_name studio.localhost studio.*;
  location / {
    proxy_pass http://studio:3000;
  }
}
```

---

## Final Working Configuration

### 1. Supabase Client Files

All three client files must use the same `cookieOptions.name`. **This is now configurable via environment variable:**

**Environment Variable:**
```bash
# .env file
SUPABASE_COOKIE_NAME=sb-localhost-auth-token

# For production, change to match your domain:
SUPABASE_COOKIE_NAME=sb-yoursite-auth-token
```

**web/middleware.ts:**
```typescript
const cookieName = process.env.SUPABASE_COOKIE_NAME || 'sb-localhost-auth-token'

const supabase = createServerClient(
  process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_ANON_KEY!,
  {
    cookies: { getAll, setAll },
    cookieOptions: { name: cookieName }
  }
)
```

**web/lib/supabase/server.ts:**
```typescript
const cookieName = process.env.SUPABASE_COOKIE_NAME || 'sb-localhost-auth-token'

const supabase = createServerClient(
  process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_ANON_KEY!,
  {
    cookies: cookieStore,
    cookieOptions: { name: cookieName }
  }
)
```

**web/lib/supabase/client.ts:**
```typescript
const cookieName = process.env.NEXT_PUBLIC_SUPABASE_COOKIE_NAME || 'sb-localhost-auth-token'

const supabase = createBrowserClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_ANON_KEY!,
  {
    cookieOptions: { name: cookieName }
  }
)
```

**Why This Matters for Production:**
- Cookie name should reflect your actual domain for clarity
- Makes debugging easier (cookies named after your site)
- Avoids confusion when managing multiple environments
- Format convention: `sb-{domain}-auth-token`
  - Local: `sb-localhost-auth-token`
  - Production: `sb-yoursite-auth-token`

### 2. JWT Configuration

Ensure `SUPABASE_ANON_KEY` is signed with the same `JWT_SECRET` used by GoTrue:

```bash
# Generate matching ANON_KEY
docker exec -it <kong-container> sh
npm install -g jsonwebtoken
node -e "
const jwt = require('jsonwebtoken');
const secret = 'super-secret-jwt-token-with-at-least-32-characters-long';
const payload = { role: 'anon', iss: 'supabase', iat: Math.floor(Date.now() / 1000) };
console.log(jwt.sign(payload, secret, { expiresIn: '10y' }));
"
```

### 3. Docker Compose Environment

```yaml
bloom-web:
  environment:
    SUPABASE_URL: http://kong:8000
    NEXT_PUBLIC_SUPABASE_URL: http://localhost/api
    SUPABASE_ANON_KEY: ${ANON_KEY}
    JWT_SECRET: ${JWT_SECRET}
    SITE_URL: http://localhost
    # Cookie name - configurable per environment
    SUPABASE_COOKIE_NAME: ${SUPABASE_COOKIE_NAME:-sb-localhost-auth-token}
    NEXT_PUBLIC_SUPABASE_COOKIE_NAME: ${SUPABASE_COOKIE_NAME:-sb-localhost-auth-token}

studio:
  environment:
    SUPABASE_URL: http://kong:8000
    SUPABASE_PUBLIC_URL: http://localhost/api
    SUPABASE_ANON_KEY: ${SUPABASE_ANON_KEY}
    STUDIO_PG_META_URL: http://meta:8080
  ports:
    - "55323:3000"  # Optional: direct access for debugging
```

### 4. Nginx Configuration

```nginx
# Main Application (default_server)
server {
  listen 80 default_server;
  server_name _;
  
  location /api/ {
    rewrite ^/api(/.*)$ $1 break;
    proxy_pass http://kong:8000;
    add_header 'Access-Control-Allow-Credentials' 'true' always;
    proxy_pass_header Set-Cookie;
  }
  
  location / {
    proxy_pass http://bloom-web:3000;
    proxy_set_header Host $host;
  }
}

# Supabase Studio (subdomain)
server {
  listen 80;
  server_name studio.localhost studio.*;
  
  location / {
    proxy_pass http://studio:3000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection 'upgrade';
    proxy_set_header Host $host;
  }
}
```

### 5. Local Hosts File

```bash
# /etc/hosts
127.0.0.1   studio.localhost
```

### 6. Testing Authentication

```bash
# 1. Clear browser cookies and cache

# 2. Access main app
open http://localhost

# 3. Login with test credentials
# Email: testuser1@salk.edu
# Password: testuser1

# 4. Verify redirect to /app

# 5. Check cookies in browser DevTools
# Should see: sb-localhost-auth-token with base64- prefix

# 6. Access Studio
open http://studio.localhost

# 7. Should auto-authenticate with same session
```

---

## Production Deployment Checklist

When deploying to production, follow these steps to configure the cookie name and other environment-specific settings:

### 1. Update Environment Variables

Create a `.env` file (or use your secrets management system) with production values:

```bash
# Copy the example file
cp .env.example .env

# Edit with production values
SUPABASE_COOKIE_NAME=sb-yoursite-auth-token
SITE_URL=https://yoursite.com
JWT_SECRET=your-production-jwt-secret-at-least-32-chars
ANON_KEY=your-production-anon-key-signed-with-jwt-secret
SERVICE_ROLE_KEY=your-production-service-role-key
POSTGRES_PASSWORD=your-production-postgres-password
```

### 2. Update docker-compose.prod.yml

Change the public URLs to match your domain:

```yaml
bloom-web:
  build:
    args:
      NEXT_PUBLIC_SUPABASE_URL: https://yoursite.com/api  # Changed from http://localhost/api
  environment:
    NEXT_PUBLIC_SUPABASE_URL: https://yoursite.com/api
    SUPABASE_COOKIE_NAME: ${SUPABASE_COOKIE_NAME}  # Will read from .env
    NEXT_PUBLIC_SUPABASE_COOKIE_NAME: ${SUPABASE_COOKIE_NAME}
```

### 3. Configure DNS Records

Set up A records for your domain and subdomains:

```
A    @               <your-server-ip>      # yoursite.com
A    studio          <your-server-ip>      # studio.yoursite.com
A    www             <your-server-ip>      # www.yoursite.com (optional)
```

### 4. Update Nginx Configuration

In `nginx/nginx.conf.template`, update server names:

```nginx
# Main application
server {
  listen 80;
  server_name yoursite.com www.yoursite.com;
  # ... rest of config
}

# Studio
server {
  listen 80;
  server_name studio.yoursite.com;
  # ... rest of config
}
```

### 5. Set Up SSL Certificates (Highly Recommended)

Use Let's Encrypt for free SSL certificates:

```bash
# Install certbot
sudo apt-get install certbot python3-certbot-nginx

# Get certificates for your domains
sudo certbot --nginx -d yoursite.com -d www.yoursite.com -d studio.yoursite.com

# Certbot will automatically update nginx config for HTTPS
```

Or update nginx config manually for SSL:

```nginx
server {
  listen 443 ssl http2;
  server_name yoursite.com;
  
  ssl_certificate /etc/letsencrypt/live/yoursite.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/yoursite.com/privkey.pem;
  
  # ... rest of config
}

# Redirect HTTP to HTTPS
server {
  listen 80;
  server_name yoursite.com;
  return 301 https://$host$request_uri;
}
```

### 6. Rebuild and Deploy

```bash
# Rebuild with production settings
make prod-up

# Or manually:
docker-compose -f docker-compose.prod.yml build --no-cache
docker-compose -f docker-compose.prod.yml up -d
```

### 7. Verify Cookie Configuration

After deployment, check that cookies are created with the correct name:

1. Open browser DevTools → Application → Cookies
2. Login to your site
3. Verify cookie name is `sb-yoursite-auth-token` (or whatever you configured)
4. Check that authentication works across page refreshes

### Production Environment Variables Summary

| Variable | Local | Production | Purpose |
|----------|-------|------------|---------|
| `SUPABASE_COOKIE_NAME` | `sb-localhost-auth-token` | `sb-yoursite-auth-token` | Cookie name for auth session |
| `SITE_URL` | `http://localhost` | `https://yoursite.com` | Public URL of your app |
| `NEXT_PUBLIC_SUPABASE_URL` | `http://localhost/api` | `https://yoursite.com/api` | Public Supabase API URL |
| `JWT_SECRET` | Dev secret | Production secret | Must match between deployments |
| `ANON_KEY` | Dev key | Production key | Signed with JWT_SECRET |

---

## Database Access (Production)

When deployed on a server with proper firewall rules (no exposed database ports), you have several options:

### 1. Supabase Studio (Recommended)
- Access via subdomain: `https://studio.yoursite.com`
- Full GUI for database management
- SQL editor, table browser, query history
- Uses authenticated API access through nginx

### 2. SSH Tunnel
```bash
ssh -L 5432:localhost:5432 user@yourserver.com
psql -h localhost -p 5432 -U postgres
```

### 3. VPN Access
Set up VPN (WireGuard/OpenVPN) to access internal Docker network directly.

### 4. Supabase CLI
```bash
# From inside the server
docker exec -it <postgres-container> psql -U postgres
```

---

## Summary

The authentication issues were caused by a combination of:

1. **Cookie storage key mismatch** (PRIMARY): Different hostnames in SUPABASE_URL vs NEXT_PUBLIC_SUPABASE_URL caused @supabase/ssr to use different cookie names for browser vs server clients.

2. **JWT signature mismatch**: ANON_KEY was signed with wrong JWT_SECRET, causing token validation failures.

3. **Improper middleware pattern**: Not following canonical SSR pattern meant cookies weren't syncing correctly between requests.

4. **SSR build errors**: Custom cookie handlers using browser APIs (`document.cookie`, `window.localStorage`) broke server-side rendering.

5. **Nginx routing complexity**: Studio couldn't run on subpath due to Next.js client-side routing limitations - required subdomain approach.

The solution required:
- Explicit `cookieOptions.name` in all Supabase clients
- Regenerating ANON_KEY with correct JWT_SECRET  
- Implementing canonical Supabase SSR middleware pattern
- Removing custom browser API usage from client code
- Switching from subpath to subdomain routing for Studio
- Configuring proper nginx server blocks with default_server directive

**Key Takeaway**: When using nginx reverse proxy with different internal/external URLs, always explicitly configure cookie names to ensure consistency across all Supabase client instances.

---

## Supabase Studio Upload & CORS Issues

### Issue: Studio File Uploads Failing with CORS Errors

**Date:** November 17, 2025

**Symptoms:**
- Supabase Studio dashboard accessible at `http://studio.localhost`
- Multiple 404 errors when uploading files:
  - `GET http://studio.localhost/api/v1/projects/default/api-keys?reveal=false 404 (Not Found)`
  - `GET http://studio.localhost/api/platform/profile 404 (Not Found)`
  - `GET http://studio.localhost/api/cli-release-version 404 (Not Found)`
- CORS errors on file upload:
  ```
  Access to XMLHttpRequest at 'http://localhost/storage/v1/upload/resumable' 
  from origin 'http://studio.localhost' has been blocked by CORS policy: 
  Response to preflight request doesn't pass access control check: 
  Redirect is not allowed for a preflight request.
  ```
- Studio dashboard partially functional but uploads completely broken

**Root Causes:**

1. **Cross-Origin Requests**: Studio was configured with `SUPABASE_PUBLIC_URL: http://localhost/api`, causing it to make requests to `http://localhost` instead of `http://studio.localhost`, triggering CORS violations.

2. **Missing API Routes**: The `studio.localhost` nginx server block only proxied Studio UI requests, but didn't proxy Supabase backend API routes (`/auth/`, `/storage/`, `/rest/`, etc.) needed for Studio to communicate with Supabase services.

3. **Incorrect API Proxying Attempt**: Initial fix tried proxying all `/api/*` requests to Kong, but this broke Studio's internal APIs (`/api/platform/*`, `/api/v1/projects/*`, `/api/cli-release-version`) which should be handled by Studio itself, not Kong.

**Investigation Process:**

1. Checked nginx logs - showed rewrite rules working but Kong returning 404s
2. Realized Studio has two types of APIs:
   - **Studio internal APIs**: `/api/platform/*`, `/api/v1/projects/*`, `/api/cli-release-version` → Should go to Studio container
   - **Supabase backend APIs**: `/auth/*`, `/storage/*`, `/rest/*`, `/realtime/*` → Should go to Kong

**Solution:**

#### 1. Updated Studio Environment Variable

**File: `.env.prod`**
```bash
# Studio Configuration
STUDIO_SUPABASE_PUBLIC_URL=http://studio.localhost
```

**File: `docker-compose.prod.yml`**
```yaml
studio:
  environment:
    SUPABASE_URL: ${SUPABASE_URL}
    SUPABASE_PUBLIC_URL: ${STUDIO_SUPABASE_PUBLIC_URL}  # Changed from ${NEXT_PUBLIC_SUPABASE_URL}
    SUPABASE_ANON_KEY: ${ANON_KEY}
    SUPABASE_SERVICE_KEY: ${SERVICE_ROLE_KEY}
```

This ensures Studio makes same-origin requests to `http://studio.localhost` instead of cross-origin requests to `http://localhost`.

#### 2. Updated Nginx Configuration for Studio

**File: `nginx/nginx.conf.template`**

Instead of proxying all `/api/*` requests (which broke Studio's internal APIs), we now proxy only specific Supabase backend service routes:

```nginx
# -----------------
# Supabase Studio (subdomain) 
# -----------------
server {
  listen 80;
  server_name studio.localhost studio.*;
  
  # ----------- Supabase Backend APIs (proxy to Kong) -----------
  # Auth API
  location /auth/ {
      proxy_pass http://kong:8000/auth/;
      proxy_redirect off;
      proxy_intercept_errors off;
      proxy_pass_header Set-Cookie;
      proxy_pass_request_headers on;

      if ($request_method = OPTIONS) {
        return 204;
      }

      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
  }

  # REST API
  location /rest/ {
      proxy_pass http://kong:8000/rest/;
      proxy_redirect off;
      proxy_intercept_errors off;
      proxy_pass_header Set-Cookie;
      proxy_pass_request_headers on;

      if ($request_method = OPTIONS) {
        return 204;
      }

      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
  }

  # Storage API
  location /storage/ {
      proxy_pass http://kong:8000/storage/;
      proxy_redirect off;
      proxy_intercept_errors off;
      proxy_pass_header Set-Cookie;
      proxy_pass_request_headers on;

      if ($request_method = OPTIONS) {
        return 204;
      }

      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
  }

  # Realtime API
  location /realtime/ {
      proxy_pass http://kong:8000/realtime/;
      proxy_redirect off;
      proxy_intercept_errors off;
      proxy_pass_header Set-Cookie;
      proxy_pass_request_headers on;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection 'upgrade';

      if ($request_method = OPTIONS) {
        return 204;
      }

      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
  }
  
  # ----------- Studio UI (includes Studio's internal APIs) -----------
  location / {
      proxy_pass http://studio:3000;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection 'upgrade';
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_cache_bypass $http_upgrade;
  }
}
```

**Key Points:**
- Specific location blocks for each Supabase service (`/auth/`, `/storage/`, `/rest/`, `/realtime/`)
- All other requests (including `/api/*`) fall through to the `location /` block and are handled by Studio
- This allows Studio's internal APIs to work while properly proxying Supabase backend services

#### 3. Restart Services

```bash
docker compose -f docker-compose.prod.yml restart nginx
# or
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d
```

**Result:**
- ✅ File uploads working correctly  
- ✅ CORS errors resolved  
- ✅ Studio dashboard fully functional  
- ⚠️ Expected 404 for `/api/v1/projects/default/api-keys` (Supabase Cloud-only endpoint, safe to ignore in self-hosted)

**Architecture After Fix:**

```
Browser → http://studio.localhost
  ├── /api/* → Studio container (Studio internal APIs)
  ├── /auth/* → nginx → Kong → Auth service
  ├── /storage/* → nginx → Kong → Storage service
  ├── /rest/* → nginx → Kong → REST API
  ├── /realtime/* → nginx → Kong → Realtime service
  └── /* → Studio container (UI)
```

**Key Takeaways:**
1. Studio has its own internal management APIs that shouldn't be proxied to Kong
2. Separate Studio's public URL configuration from the main app's URL
3. Use specific location blocks for Supabase services instead of blanket `/api/*` proxying
4. The 404 for `/api/v1/projects/default/api-keys` is expected in self-hosted setups (Cloud-only endpoint)

---

## MinIO Console WebSocket Connection Failures

### Issue: MinIO Console WebSocket Errors and 401 Unauthorized

**Date:** November 17, 2025

**Symptoms:**
- MinIO console accessible at `http://localhost/minio` but with errors
- Login works but object browser doesn't load properly
- Repeated WebSocket connection failures:
  ```
  WebSocket connection to 'ws://localhost/minio/ws/objectManager' failed
  Error in websocket connection. Attempting reconnection...
  Websocket Disconnected. Attempting Reconnection...
  ```
- 401 Unauthorized errors in browser console
- Real-time file updates not working

**Root Cause:**

MinIO console has **known limitations with subpath proxying**. When served under a subpath (like `/minio/`), the WebSocket connections fail due to:

1. **WebSocket Path Rewriting Issues**: nginx's `sub_filter` doesn't properly rewrite WebSocket URLs, causing connection attempts to wrong endpoints
2. **Authentication Token Passing**: MinIO's session cookies and auth tokens don't get passed correctly through subpath proxies for WebSocket upgrades
3. **Upgrade Header Handling**: The HTTP → WebSocket upgrade handshake gets corrupted when proxying through subpaths with URL rewrites

MinIO console expects to be served at the root path (`/`) for WebSocket connections to work reliably.

**Investigation Process:**

1. Checked nginx logs - showed WebSocket upgrade attempts but connection failures
2. Browser console showed repeated connection/disconnection cycles
3. Researched MinIO documentation - confirmed subpath serving has WebSocket limitations
4. Decision: Move to subdomain approach (recommended by MinIO)

**Solution:**

#### 1. Added MinIO Browser Redirect URL

**File: `docker-compose.prod.yml`**

Updated MinIO environment to tell it about the subdomain:

```yaml
supabase-minio:
  image: minio/minio:latest
  container_name: supabase-minio
  restart: unless-stopped
  networks:
    - supanet
  expose:
    - "9000"
    - "9001"
  environment:
    MINIO_ROOT_USER: ${MINIO_ROOT_USER}
    MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    MINIO_BROWSER_REDIRECT_URL: http://minio.localhost  # Added this
  command: server --console-address ":9001" /data
  volumes:
    - /Users/benficaa/data/minio:/data
```

#### 2. Created Dedicated Nginx Server Block for MinIO

**File: `nginx/nginx.conf.template`**

Added a new subdomain server block with proper WebSocket support:

```nginx
# -----------------
# MinIO Console (subdomain)
# -----------------
server {
  listen 80;
  server_name minio.localhost minio.*;
  
  # Ignore favicon.ico requests
  location = /favicon.ico {
      return 204;
      access_log off;
      log_not_found off;
  }

  location / {
      proxy_pass http://supabase-minio:9001;
      proxy_http_version 1.1;
      
      # WebSocket support
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection 'upgrade';
      
      # Standard proxy headers
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_set_header X-Forwarded-Host $host;
      
      # MinIO specific
      proxy_set_header X-NginX-Proxy true;
      
      # Disable buffering for WebSockets and real-time updates
      proxy_buffering off;
      proxy_cache_bypass $http_upgrade;
      proxy_request_buffering off;
      
      # Increase timeouts for long-lived connections
      proxy_connect_timeout 300;
      proxy_send_timeout 300;
      proxy_read_timeout 300;
      send_timeout 300;
  }
}
```

#### 3. Updated Old Subpath Location to Redirect

Updated the main server block to redirect old URLs to the new subdomain:

```nginx
# ----------- MinIO Console (redirect to subdomain) -----------
location /minio {
    return 301 http://minio.localhost$request_uri;
}

location /minio/ {
    return 301 http://minio.localhost$request_uri;
}
```

#### 4. Restart Services

```bash
docker compose -f docker-compose.prod.yml restart nginx supabase-minio
```

**Result:**
- ✅ WebSocket connections working
- ✅ Real-time object browser updates functioning
- ✅ No more 401 Unauthorized errors
- ✅ File uploads and management fully operational
- ✅ Backward compatibility with redirect from old `/minio/` URLs

**Access MinIO Console:**
- **New URL**: `http://minio.localhost`
- **Old URL**: `http://localhost/minio` (redirects to new URL)
- **Credentials**: 
  - Username: `supabase` (from `MINIO_ROOT_USER`)
  - Password: `supabase123` (from `MINIO_ROOT_PASSWORD`)

**Architecture After Fix:**

```
Browser → http://minio.localhost
  └── / → nginx → MinIO Console (port 9001)
       ├── HTTP requests → Standard proxying
       └── WebSocket (ws://) → Upgrade with proper headers
```

**Key Takeaways:**
1. MinIO console does not work reliably with subpath proxying due to WebSocket limitations
2. Always serve MinIO console at root path using subdomain approach
3. WebSocket connections require proper `Upgrade` and `Connection` headers
4. Disable buffering (`proxy_buffering off`) for real-time WebSocket updates
5. Increase timeouts for long-lived WebSocket connections
6. Use `MINIO_BROWSER_REDIRECT_URL` to tell MinIO about its public URL

**Related MinIO Documentation:**
- [MinIO Console with Reverse Proxy](https://min.io/docs/minio/linux/integrations/setup-nginx-proxy-with-minio.html)
- Known issue: Subpath serving breaks WebSocket connections for object manager

---

## Flask API Subdomain Setup

**Date:** November 17, 2025  
**Issue:** Need a dedicated subdomain for Flask API with CORS support for cross-subdomain requests  
**Status:** ✅ RESOLVED

### Problem

User wanted to consolidate all Flask API routes under a dedicated subdomain (`flask.localhost`) to:
1. Separate API traffic from main application traffic
2. Enable cross-subdomain access from `localhost`, `studio.localhost`, and `minio.localhost`
3. Maintain consistent subdomain architecture across all services

### Root Cause

Flask API was initially configured for development mode without:
- Production environment configuration in docker-compose.prod.yml
- CORS headers to allow cross-subdomain requests
- Dedicated nginx server block for subdomain routing
- Proper environment variable loading via `--env-file`

### Solution

#### 1. Added Flask service to `docker-compose.prod.yml`

```yaml
flask-app:
  build:
    context: ./flask
    dockerfile: Dockerfile
  container_name: flask-app
  expose:
    - "5002"
  environment:
    FLASK_ENV: production
    FLASK_SUPABASE_URL: ${FLASK_SUPABASE_URL}
    SERVICE_ROLE_KEY: ${SERVICE_ROLE_KEY}
    JWT_SECRET: ${JWT_SECRET}
    MINIO_DEFAULT_BUCKET: ${MINIO_DEFAULT_BUCKET}
    MINIO_S3_STORAGE_REGION: ${MINIO_S3_STORAGE_REGION}
    MINIO_ROOT_USER: ${MINIO_ROOT_USER}
    MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    MINIO_STORAGE_S3_ENDPOINT: ${MINIO_STORAGE_S3_ENDPOINT}
  networks:
    - supanet
  depends_on:
    - kong
    - supabase-minio
```

#### 2. Updated Flask Dockerfile to include `config.py`

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY config.py .
CMD ["python", "app.py"]
```

#### 3. Configured Flask-CORS in `flask/app.py`

```python
from flask_cors import CORS

CORS(app, resources={
    r"/*": {
        "origins": [
            "http://localhost",
            "http://localhost:3000",
            "http://studio.localhost",
            "http://minio.localhost",
            "http://flask.localhost"
        ],
        "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        "supports_credentials": True
    }
})
```

Added `Flask-CORS==5.0.0` to `requirements.txt`

#### 4. Created nginx server block for `flask.localhost`

**File: `nginx/nginx.conf.template`**
```nginx
# Flask API subdomain
server {
    listen 80;
    server_name flask.localhost;

    location / {
        proxy_pass http://flask-app:5002;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Pass CORS headers through
        proxy_pass_header Access-Control-Allow-Origin;
        proxy_pass_header Access-Control-Allow-Methods;
        proxy_pass_header Access-Control-Allow-Headers;
        proxy_pass_header Access-Control-Allow-Credentials;
        
        # Extended timeouts for long-running video generation
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
```

#### 5. Updated environment variables in `.env.prod`

```bash
# Flask API Configuration
FLASK_PUBLIC_URL=http://flask.localhost

# Flask Supabase Connection
FLASK_SUPABASE_URL=http://kong:8000/
```

#### 6. Used Makefile for proper environment loading

Always use `make prod-up` to start production services, which includes `--env-file .env.prod`:

```bash
make prod-down  # Stop all services
make prod-up    # Start with proper env file loading
```

### Verification

**Test Flask API:**
```bash
curl http://flask.localhost/
# Response: {"message": "Flask app is running!"}
```

**Test CORS from studio.localhost:**
```bash
curl -v -H "Origin: http://studio.localhost" http://flask.localhost/
# Response includes:
# Access-Control-Allow-Origin: http://studio.localhost
# Access-Control-Allow-Credentials: true
```

**Test Supabase connectivity:**
```bash
curl -H "Origin: http://localhost" http://flask.localhost/supabaseconnection
# Returns data from cyl_scanners table
```

### Results

- ✅ Flask API accessible at `http://flask.localhost`
- ✅ CORS working for all subdomains (`localhost`, `studio.localhost`, `minio.localhost`)
- ✅ Environment variables properly loaded from `.env.prod`
- ✅ Flask connects to Supabase via Kong (internal network)
- ✅ Flask connects to MinIO for S3 storage operations
- ✅ Extended timeouts for long-running video generation requests

### Architecture After Fix

```
Browser → Four peer subdomains:
  ├── http://localhost → bloom-web:3000 (Next.js app)
  ├── http://studio.localhost → studio:3000 (Supabase Studio)
  ├── http://minio.localhost → supabase-minio:9001 (MinIO console)
  └── http://flask.localhost → flask-app:5002 (Flask API)
       ├── Standard HTTP requests
       ├── CORS-enabled for cross-subdomain access
       └── Long-running operations (300s timeout)
```

**Available Flask Endpoints:**
- `GET /` - Health check
- `GET /supabaseconnection` - Test Supabase connectivity
- `GET /list_buckets` - List MinIO buckets
- `POST /generate_video` - Generate videos from images (long-running)

### Key Takeaways

1. **Environment Loading**: Docker Compose doesn't auto-load `.env.prod` - must use `--env-file` flag or Makefile
2. **CORS Configuration**: Flask-CORS provides fine-grained control over allowed origins, methods, and credentials
3. **Subdomain Architecture**: Each service gets its own subdomain for clean separation and easier CORS management
4. **Nginx Timeouts**: Long-running operations require extended `proxy_*_timeout` settings (300s for video generation)
5. **Internal vs External URLs**: 
   - Flask uses `http://kong:8000/` internally (Docker network)
   - Browser uses `http://flask.localhost` externally (nginx proxy)
6. **Missing Files**: Ensure all required files (like `config.py`) are copied in Dockerfile

**Related Flask Documentation:**
- [Flask-CORS Documentation](https://flask-cors.readthedocs.io/)
- [Nginx Proxy Timeouts](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_read_timeout)

```

````

```
