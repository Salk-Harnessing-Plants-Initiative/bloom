# Infrastructure Capability Specification

## ADDED Requirements

### Requirement: Reverse Proxy Subpath Serving

The nginx reverse proxy SHALL correctly serve Supabase Studio dashboard and Kong Gateway API under the `/supabase_kong/` subpath without breaking static asset loading or application routing.

#### Scenario: Dashboard loads with all assets

- **WHEN** a user navigates to `http://localhost/supabase_kong/` (development) or `https://api.bloom.salk.edu/supabase_kong/` (production)
- **THEN** the Supabase Studio dashboard loads completely
- **AND** all CSS stylesheets load with status 200
- **AND** all JavaScript files load with status 200
- **AND** all images and icons load with status 200
- **AND** the dashboard UI is fully functional and styled correctly

#### Scenario: API requests route through Kong correctly

- **WHEN** the dashboard makes API requests to Supabase services
- **THEN** requests are correctly proxied to Kong Gateway
- **AND** Kong routes the requests to appropriate backend services (auth, rest, storage, etc.)
- **AND** responses are returned to the dashboard without errors

#### Scenario: Authentication works through subpath

- **WHEN** a user attempts to log in to the Supabase dashboard
- **THEN** the authentication flow completes successfully
- **AND** the user is redirected back to the dashboard at the correct subpath
- **AND** session cookies are set with appropriate path scope

### Requirement: Studio Base Path Configuration

The Supabase Studio container SHALL be configured with environment variables to make it aware that it is being served under a subpath, enabling correct URL generation for assets and API calls.

#### Scenario: Studio generates subpath-aware URLs

- **WHEN** Supabase Studio renders HTML pages
- **THEN** all asset links include the `/supabase_kong` prefix
- **AND** all API endpoint URLs include the appropriate routing context
- **AND** JavaScript-generated URLs respect the base path configuration

#### Scenario: Studio internal routing works

- **WHEN** a user navigates between different sections of the Studio dashboard (e.g., Table Editor, SQL Editor, Authentication)
- **THEN** the browser URL updates to include the `/supabase_kong` prefix
- **AND** direct access to deep-linked URLs works correctly
- **AND** browser back/forward navigation functions properly

### Requirement: Nginx Proxy Header Forwarding

The nginx reverse proxy SHALL forward appropriate headers to Kong Gateway to preserve the original request context, including the subpath prefix.

#### Scenario: Request context headers are forwarded

- **WHEN** nginx proxies a request to Kong Gateway
- **THEN** the `Host` header is set to the original host from the client request
- **AND** the `X-Real-IP` header contains the client's IP address
- **AND** the `X-Forwarded-For` header contains the client IP and any intermediate proxies
- **AND** the `X-Forwarded-Proto` header indicates the original protocol (http or https)
- **AND** the `X-Forwarded-Prefix` header is set to `/supabase_kong` to indicate the subpath

#### Scenario: HTTPS context is preserved in production

- **WHEN** a request arrives at nginx over HTTPS in production
- **THEN** the `X-Forwarded-Proto` header is set to `https`
- **AND** Kong Gateway and Studio generate HTTPS URLs in responses
- **AND** mixed content warnings do not occur in the browser

### Requirement: MinIO Console Compatibility

Changes to nginx configuration for Supabase dashboard serving SHALL NOT break the existing MinIO console subpath serving at `/minio/`.

#### Scenario: MinIO console continues to work

- **WHEN** a user navigates to `http://localhost/minio/` (development)
- **THEN** the MinIO console loads correctly
- **AND** all MinIO static assets load successfully
- **AND** MinIO functionality remains unchanged

### Requirement: Development and Production Parity

The nginx and docker-compose configuration SHALL work consistently in both development (`docker-compose.dev.yml`) and production (`docker-compose.prod.yml`) environments with minimal differences.

#### Scenario: Configuration works in development

- **WHEN** the development environment is started with `docker compose -f docker-compose.dev.yml up`
- **THEN** the Supabase dashboard is accessible at `http://localhost/supabase_kong/`
- **AND** all functionality works as specified

#### Scenario: Configuration works in production

- **WHEN** the production environment is started with `docker compose -f docker-compose.prod.yml up`
- **THEN** the Supabase dashboard is accessible at `https://api.bloom.salk.edu/supabase_kong/`
- **AND** all functionality works as specified
- **AND** HTTPS certificates are correctly applied

### Requirement: Nginx Configuration Maintainability

The nginx configuration for subpath serving SHALL be simple, well-commented, and follow reverse proxy best practices to ensure maintainability.

#### Scenario: Configuration is understandable

- **WHEN** a developer reviews the nginx configuration file
- **THEN** the purpose of each directive is clear from comments or naming
- **AND** the subpath serving strategy is documented inline
- **AND** no complex or non-standard nginx modules are required

#### Scenario: Configuration follows best practices

- **WHEN** the nginx configuration is evaluated against reverse proxy best practices
- **THEN** it does not manipulate request paths unnecessarily
- **AND** it delegates URL generation to the application layer
- **AND** it uses standard HTTP headers for context passing
- **AND** it avoids fragile text substitution techniques (sub_filter)
