# Infrastructure Capability Specification

## MODIFIED Requirements

### Requirement: Reverse Proxy Subpath Serving

The nginx reverse proxy SHALL correctly serve the Supabase Studio dashboard at `/supabase_kong/` subpath AND the Supabase API (Kong Gateway) at `/api/` subpath without breaking static asset loading, application routing, or API functionality.

#### Scenario: Dashboard loads with all assets

- **WHEN** a user navigates to `http://localhost/supabase_kong/` (development) or `https://api.bloom.salk.edu/supabase_kong/` (production)
- **THEN** the Supabase Studio dashboard loads completely
- **AND** all CSS stylesheets load with status 200
- **AND** all JavaScript files load with status 200
- **AND** all images and icons load with status 200
- **AND** the dashboard UI is fully functional and styled correctly

#### Scenario: API requests route through nginx correctly

- **WHEN** a client makes an API request to `http://localhost/api/{service}/{path}` (e.g., `/api/auth/v1/signup`)
- **THEN** nginx strips the `/api` prefix and forwards to Kong Gateway at `http://kong:8000/{service}/{path}`
- **AND** Kong routes the request to the appropriate backend service (auth, rest, storage, realtime, etc.)
- **AND** the response is returned to the client without errors
- **AND** the HTTP status code and response body match the expected API response

#### Scenario: CLI operations work through API endpoint

- **WHEN** a developer uses the Supabase CLI with `SUPABASE_URL=http://localhost/api`
- **THEN** authentication operations (login, signup) succeed
- **AND** database operations (migrations, queries) succeed
- **AND** storage operations (upload, download, list) succeed
- **AND** the CLI receives proper responses from all Supabase services

#### Scenario: Authentication works through API subpath

- **WHEN** a user or application attempts authentication via `POST http://localhost/api/auth/v1/token`
- **THEN** the request is correctly routed to the GoTrue service
- **AND** authentication succeeds with valid credentials
- **AND** JWT tokens are returned correctly
- **AND** session cookies are set with appropriate path scope

## ADDED Requirements

### Requirement: API Path Rewriting

The nginx reverse proxy SHALL strip the `/api` prefix from incoming requests before forwarding to Kong Gateway, allowing Kong to receive requests at root-level paths as expected by its declarative configuration.

#### Scenario: Auth endpoint path rewriting

- **WHEN** a client requests `POST http://localhost/api/auth/v1/signup`
- **THEN** nginx rewrites the path to `/auth/v1/signup`
- **AND** forwards to `http://kong:8000/auth/v1/signup`
- **AND** Kong routes to the GoTrue service
- **AND** the response is returned without path-related errors

#### Scenario: REST API endpoint path rewriting

- **WHEN** a client requests `GET http://localhost/api/rest/v1/todos?select=*`
- **THEN** nginx rewrites the path to `/rest/v1/todos`
- **AND** preserves query parameters `?select=*`
- **AND** forwards to `http://kong:8000/rest/v1/todos?select=*`
- **AND** PostgREST processes the query correctly

#### Scenario: Storage endpoint path rewriting

- **WHEN** a client requests `POST http://localhost/api/storage/v1/object/bucket-name/file.jpg`
- **THEN** nginx rewrites the path to `/storage/v1/object/bucket-name/file.jpg`
- **AND** forwards to Kong with the complete path preserved
- **AND** the storage service receives and processes the file upload

### Requirement: Dual URL Configuration

The system SHALL support both internal (container-to-container) and external (browser/CLI) URLs for the Supabase API, optimizing for performance and security.

#### Scenario: Internal container communication bypasses nginx

- **WHEN** the Studio container makes an API call to Supabase services
- **THEN** it uses the internal URL `http://kong:8000` directly
- **AND** the request bypasses nginx (no proxy overhead)
- **AND** the response time is minimized
- **AND** the operation succeeds as expected

#### Scenario: External browser requests use nginx proxy

- **WHEN** a browser application makes an API call using `NEXT_PUBLIC_SUPABASE_URL`
- **THEN** it uses the external URL `http://localhost/api` (dev) or `https://api.bloom.salk.edu/api` (prod)
- **AND** the request routes through nginx
- **AND** HTTPS is enforced in production
- **AND** the operation succeeds with proper CORS and authentication headers

#### Scenario: CLI tools use external nginx-proxied endpoint

- **WHEN** a developer configures the Supabase CLI with the external API URL
- **THEN** all CLI operations route through nginx at `/api/`
- **AND** HTTPS is used in production for secure credential transmission
- **AND** CLI operations (auth, db, storage) all function correctly

### Requirement: WebSocket Support for Realtime

The nginx reverse proxy SHALL properly handle WebSocket connections for Supabase Realtime subscriptions when accessed through the `/api/` subpath.

#### Scenario: Realtime WebSocket connection upgrade

- **WHEN** a client initiates a WebSocket connection to `ws://localhost/api/realtime/v1/websocket`
- **THEN** nginx forwards the WebSocket upgrade headers correctly
- **AND** Kong Gateway routes the connection to the Realtime service
- **AND** the WebSocket connection is established successfully
- **AND** real-time events are transmitted bidirectionally

#### Scenario: Realtime subscription receives updates

- **WHEN** a client subscribes to database changes via the Realtime WebSocket
- **THEN** the subscription is established through the `/api/` endpoint
- **AND** database change events are received in real-time
- **AND** the connection remains stable without unexpected disconnects
- **AND** events are received with correct payload structure

### Requirement: URL Structure Consistency

The nginx configuration SHALL provide a consistent and logical URL structure for all services, with clear separation between UI, API, and storage management endpoints.

#### Scenario: URL structure is documented and predictable

- **WHEN** a developer reviews the nginx configuration or documentation
- **THEN** the URL structure is clearly documented:
  - Frontend: `/` (reserved for Bloom web app)
  - Studio Dashboard: `/supabase_kong/`
  - Supabase API: `/api/`
  - MinIO Console: `/minio/`
- **AND** each service's purpose and subpath is commented in the nginx config
- **AND** the structure is consistent between dev and production environments

#### Scenario: Frontend and API paths do not conflict

- **WHEN** the Bloom frontend application is enabled at `/`
- **THEN** frontend routes (e.g., `/dashboard`, `/login`) are served by the frontend
- **AND** API routes at `/api/` are proxied to Kong without conflict
- **AND** Studio at `/supabase_kong/` remains accessible
- **AND** there are no path collision or routing ambiguities

### Requirement: Migration Path for Existing Deployments

The system SHALL provide clear migration documentation and support transitional configurations to allow smooth updates from direct port access to nginx-proxied access.

#### Scenario: Environment variables are updated

- **WHEN** deploying the new configuration to an existing environment
- **THEN** all `SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_URL` environment variables are updated to use `/api/`
- **AND** documentation clearly lists which variables need updating
- **AND** example values are provided for both dev and production

#### Scenario: Temporary dual access during migration

- **WHEN** transitioning from port 8000 to `/api/` access pattern
- **THEN** both access methods can work simultaneously (port 8000 still exposed)
- **AND** developers can migrate their local configurations gradually
- **AND** monitoring ensures no broken integrations
- **AND** port 8000 can be safely removed after full migration

## MODIFIED Requirements (from previous proposal)

### Requirement: Nginx Proxy Header Forwarding

The nginx reverse proxy SHALL forward appropriate headers to both Studio and Kong Gateway to preserve the original request context, including subpath prefixes for UI and API requests.

#### Scenario: Request context headers are forwarded to API

- **WHEN** nginx proxies a request to Kong Gateway at `/api/`
- **THEN** the `Host` header is set to the original host from the client request
- **AND** the `X-Real-IP` header contains the client's IP address
- **AND** the `X-Forwarded-For` header contains the client IP and any intermediate proxies
- **AND** the `X-Forwarded-Proto` header indicates the original protocol (http or https)

#### Scenario: HTTPS context is preserved in production for API

- **WHEN** a request arrives at nginx over HTTPS in production for an API endpoint
- **THEN** the `X-Forwarded-Proto` header is set to `https`
- **AND** Kong Gateway and backend services generate HTTPS URLs in responses
- **AND** redirect responses use HTTPS URLs
- **AND** mixed content warnings do not occur

### Requirement: MinIO Console Compatibility

Changes to nginx configuration for Supabase dashboard and API serving SHALL NOT break the existing MinIO console subpath serving at `/minio/`.

#### Scenario: MinIO console continues to work

- **WHEN** a user navigates to `http://localhost/minio/` (development)
- **THEN** the MinIO console loads correctly
- **AND** all MinIO static assets load successfully
- **AND** MinIO functionality remains unchanged
- **AND** no conflicts occur with `/api/` or `/supabase_kong/` paths
