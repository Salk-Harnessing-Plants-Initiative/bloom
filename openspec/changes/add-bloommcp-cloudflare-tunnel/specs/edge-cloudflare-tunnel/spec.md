## ADDED Requirements

### Requirement: Tunnel-Only Route Scope

The Cloudflare Tunnel's public hostname SHALL only reach bloommcp's OAuth-flow routes (the MCP endpoint, its RFC 9728 discovery document, the Supabase OAuth authorization server/JWKS via Kong, and the OAuth consent route via bloom-web). It SHALL NOT expose bloom-web's general application routes, Supabase Studio, or the MinIO console.

#### Scenario: Tunnel hostname reaches bloommcp's MCP endpoint

- **WHEN** a request for `/bloommcp/mcp` arrives at Caddy with `Host` set to the tunnel's public hostname
- **THEN** Caddy proxies it to `bloommcp:8811`, identically to how `DOMAIN_MAIN` proxies the same path today

#### Scenario: Tunnel hostname does not reach bloom-web's general application

- **WHEN** a request for `/` (or any path outside the four permitted routes) arrives at Caddy with `Host` set to the tunnel's public hostname
- **THEN** Caddy does not proxy it to `bloom-web`, `studio`, or `supabase-minio` — it falls through to a 404, matching the existing "no route" behavior for unrecognized hosts (`caddy/Caddyfile:224-226`)

### Requirement: IP-Restricted Tunnel Ingress

Requests reaching bloommcp's routes through the tunnel hostname SHALL be restricted to Anthropic's published IP ranges, evaluated against the real originating client IP rather than the `cloudflared` container's own address.

#### Scenario: Request from an Anthropic-range IP is allowed through

- **WHEN** a request arrives via the tunnel with a `Cf-Connecting-IP` header value inside Anthropic's published ranges
- **THEN** Caddy proxies it to the matching backend, subject to bloommcp's own existing auth checks

#### Scenario: Request from outside Anthropic's ranges is rejected before reaching bloommcp

- **WHEN** a request arrives via the tunnel with a `Cf-Connecting-IP` header value outside Anthropic's published ranges
- **THEN** Caddy rejects the request before it reaches `bloommcp`, `kong`, or `bloom-web`

#### Scenario: A spoofed `Cf-Connecting-IP` from an untrusted source does not bypass the restriction

- **WHEN** a request's immediate TCP peer is not the trusted `cloudflared` container (i.e., it did not arrive through the tunnel), but it carries a `Cf-Connecting-IP` header claiming an Anthropic-range IP
- **THEN** Caddy does not honor that header for a connection outside the trusted-proxy chain, and the request is evaluated as coming from its actual, non-trusted source IP

### Requirement: Tunnel Connectivity Is Environment-Additive

The Cloudflare Tunnel SHALL NOT alter existing request handling for `DOMAIN_MAIN`, `DOMAIN_STUDIO`, or `DOMAIN_MINIO` traffic, and SHALL be scoped to the staging environment only.

#### Scenario: Existing Salk-network access is unaffected

- **WHEN** a request for any existing route arrives at Caddy via `DOMAIN_MAIN` (over the VPN-restricted path, as today)
- **THEN** it is handled identically to before this change — the tunnel is a second entry point, not a replacement

#### Scenario: Production is not affected by this change

- **WHEN** the production compose stack is deployed
- **THEN** no `cloudflared` service runs and no tunnel-related Caddy routes or `remote_ip` restrictions apply — this capability exists on staging only until a separate follow-up change extends it
