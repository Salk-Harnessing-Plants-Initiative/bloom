## Why

Bloom serves no HTTP security headers at all. Verified on `staging` at `0be3c30c`: `caddy/Caddyfile` contains zero `header` directives, and `web/next.config.js` defines no `headers()` block — so neither the proxy nor the application sets them. Every response, on every hostname (`DOMAIN_MAIN`, `DOMAIN_STUDIO`, `DOMAIN_MINIO`), omits all of them.

This is item 1 of the security-hardening punch list in issue #108. It matters more now than when that list was written: bloom-web is moving toward reachability beyond Salk's network, and browser-side defences currently rest on the assumption that an attacker cannot reach the host at all. Once that network boundary is gone, headers are what remains.

This change covers only the headers that carry no runtime risk. HSTS and CSP `script-src` are deliberately excluded — see Impact.

## What Changes

- Add a site-level `header` block to `caddy/Caddyfile`, ahead of the per-host `handle` matchers, so all three hostnames inherit it:
  - `X-Content-Type-Options: nosniff` — stop MIME sniffing overriding a declared `Content-Type`
  - `X-Frame-Options: DENY` — refuse inbound framing (clickjacking)
  - `Content-Security-Policy: frame-ancestors 'none'` — the same denial in its standards-track form, for clients that ignore the legacy header
  - `Referrer-Policy: strict-origin-when-cross-origin` — stop experiment paths leaking in `Referer`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=()` — disable unused browser features
  - `Cross-Origin-Opener-Policy: same-origin-allow-popups` — stop a page that opens Bloom in a popup from scripting that window
  - `Cross-Origin-Resource-Policy: same-origin` — stop another site loading Bloom's images and files into its pages
- No changes to any service, client, or request path. These are response headers; callers send nothing new and non-browser callers (bloomctl, MCP clients, Kong) ignore them.

**Not in this change**, each for a specific reason:

- **HSTS** — browsers cache it for its full `max-age` and it cannot be withdrawn server-side. It lands deliberately alongside the public-exposure work, not incidentally here. Tracked as `add-edge-hsts`.
- **CSP `script-src`** — Next.js emits inline hydration scripts, so a policy without nonces would need `'unsafe-inline'`, which would not block an injected event handler and would be protection in name only. Doing it properly requires nonce middleware in bloom-web. Tracked as `add-edge-csp`. This is the directive with real teeth against XSS; `frame-ancestors` ships here precisely because it carries no such constraint.

## Impact

- **Affected specs:** `edge-security-headers` (new capability)
- **Affected code:** `caddy/Caddyfile` (one added block, no existing directive modified); `docker-compose.prod.yml` (digest pins on `supabase/studio` and `minio/minio`); `.github/workflows/pr-checks.yml` (CI serves all three hostnames); `tests/unit/test_caddy_security_headers.py` and the new `tests/unit/_caddyfile_helpers.py`, with `test_caddy_client_info_route.py` and `test_caddy_cyl_video_route.py` refactored onto the shared parser; `tests/integration/{conftest.py,test_api_endpoints.py}`; `_WIKI/CADDY/README.md`
- **Deployment:** picked up on the next Caddy config reload; no image rebuild (unlike the rate-limiting work in #108 item 2, which needs an `xcaddy --with` plugin)
- **Risk:** the headers reach Supabase Studio and the MinIO console, which are third-party UIs this project does not build. Measured clean against the image versions production pins — Studio `2026.03.30-sha-12a43e5` types 92/92 assets correctly, MinIO `RELEASE.2025-01-20T14-49-07Z` 3/3, and neither serves nor creates an `<iframe>` — so `nosniff` refuses nothing and `DENY` breaks nothing. Re-confirm if either pin moves. Fully revertible — unlike HSTS, no browser caches this.
- **Duplicate headers:** the MinIO console emits four of these itself, so they arrive twice on that hostname. `nosniff`, `X-Frame-Options` and `Referrer-Policy` duplicate byte-identically, leaving browser behaviour unchanged. `Content-Security-Policy` duplicates with differing values — Caddy's `frame-ancestors 'none'` alongside MinIO's own `default-src`/`script-src` policy — and browsers enforce every CSP header present, so the denial holds and MinIO's policy is not weakened.
