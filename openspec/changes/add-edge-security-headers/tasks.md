## 0. Pre-flight — confirm nothing on the covered surface relies on MIME sniffing

- [x] 0.1 Confirm `web/` performs no dynamic `<script>` / `<link rel=stylesheet>` loading, the only class `nosniff` hard-refuses — verified, none present
- [x] 0.2 Confirm storage objects are consumed as images (`illustration.tsx`, signed URL with image transform), which `nosniff` does not block on type mismatch
- [x] 0.3 Confirm client-side exports use `Blob` + `createObjectURL` (`blob:` URLs), which never traverse Caddy
- [x] 0.4 Confirm bloomctl never inspects `Content-Type` — no references in `bloomcli/src`; the header cannot affect non-browser clients, which do not sniff
- [x] 0.5 Measure Supabase Studio and MinIO console bundles against the image pins prod runs — Studio `2026.03.30-sha-12a43e5`: 92/92 assets correctly typed; MinIO `RELEASE.2025-01-20T14-49-07Z`: 3/3 correctly typed; neither serves nor dynamically creates an `<iframe>`. Both safe under all five headers, which is what makes site-level coverage viable

## 1. Implementation

- [x] 1.1 Add a site-level `header` block to `caddy/Caddyfile`, after the `tls` directive and ahead of the `@main`/`@studio`/`@minio` matchers, setting `X-Content-Type-Options`, `X-Frame-Options`, `Content-Security-Policy: frame-ancestors 'none'`, `Referrer-Policy`, and `Permissions-Policy`
- [x] 1.2 Comment the block with why HSTS is absent and why `fullscreen` is omitted from `Permissions-Policy`, so neither omission reads as an oversight

## 2. Verification

- [x] 2.1 Validate Caddyfile syntax — `caddy validate` against the project's own image built from `caddy/Dockerfile` (the stock image cannot parse `tls { dns cloudflare }`): **Valid configuration**
- [x] 2.2 Confirm all five headers are present on `DOMAIN_MAIN` — 5/5, verified against a running Caddy
- [x] 2.3 Confirm all five are present on `DOMAIN_STUDIO` and `DOMAIN_MINIO` too, proving the site-level declaration is inherited rather than main-only — 5/5 on both
- [x] 2.4 Confirm coverage spans every handler declared under `handle @main`, not just the root — 5/5 headers on `/`, `/api/client-info`, `/api/oauth/consent`, `/api/cyl/*`, `/api/auth/v1/*`, `/langchain/*`, `/bloommcp/*`, `/workflows/health`, `/workflows/*`, and the RFC 9728 discovery path; applied on synthetic 404s and upstream-error responses too
- [x] 2.5 Confirm bloom-web loads with no blocked script or stylesheet errors in the browser console — `/login` and `/app` against a prod-shaped stack: 55 resources, 13 JS bundles, 687 CSS rules applied, zero refused or empty transfers
- [x] 2.6 Confirm the OrthoFinder page still renders its embedded OrthoBrowser iframe and that fullscreen still works — `index.html`, `pure.js`, `impure.js` and `metadata.json` all 200 from `resources.michael.salk.edu`; `document.featurePolicy.allowsFeature('fullscreen')` is `true`, confirming the deliberate omission of `fullscreen` is what preserves `allowFullScreen`
- [x] 2.7 Confirm a non-browser path is unchanged end to end — full round trip through `/api/*` with `Content-Type: application/json` intact and the body unmodified

### Regression guards

- [x] 2.8 `tests/unit/test_caddy_security_headers.py` — pins site-level placement by brace-matched depth and asserts values verbatim, so relocating the block inside a `handle` or weakening a value (`SAMEORIGIN` for `DENY`, `'unsafe-inline'` creeping into the CSP) fails CI
- [x] 2.9 `tests/integration/test_api_endpoints.py` — asserts each header reaches the client exactly once with its exact value across every handler class, plus a guard that HSTS is still absent

### Accepted limitation

- [x] 2.10 CI cannot exercise 2.3, and this is accepted rather than tracked as work. It sets `CADDY_SITE_ADDRESSES` to a single host, so Studio and MinIO requests never enter the site block, and `HEADER_ROUTES` lists only main-hostname paths — the site-level contract is therefore pinned as config shape (2.8) and never as behaviour. Weighed and accepted because:
  - The regression that matters — the block moving under a host matcher — is caught by 2.8's brace-depth assertion.
  - A dropped wildcard in `CADDY_SITE_ADDRESSES` is not a silent failure: Studio and MinIO would stop being served entirely and return an empty `Content-Length: 0` fallback, which is self-detecting.
  - What remains is a console upstream emitting a *differing* value for a last-wins header. It requires an image bump that changes a value, and bumps here are digest-pinned and reviewed; the surface is a console the tunnel never maps, with #108 item 6 as backstop. The `nosniff` risk is covered instead by making re-measurement a precondition when a pin moves.
  - A multi-hostname CI value would change the environment shape for every test in that job, which is a poor trade for the above.

### Measured, not assumed

- [x] 2.11 Duplicate headers on `DOMAIN_MINIO`, measured against `RELEASE.2025-01-20T14-49-07Z`: four arrive twice. `nosniff`, `X-Frame-Options` and `Referrer-Policy` are byte-identical; `Content-Security-Policy` differs (Caddy's `frame-ancestors 'none'` beside MinIO's own `default-src`/`script-src`), and browsers enforce every CSP header present, so the denial holds and MinIO's policy is not weakened. `Permissions-Policy` arrives once

## 3. Follow-ups (not this change)

- [ ] 3.1 `add-edge-hsts` — HSTS, landing with the public-exposure work
- [ ] 3.2 `add-edge-csp` — nonce-based CSP `script-src`, requiring Next.js middleware
- [ ] 3.3 Re-measure the console images under all five headers whenever the Studio or MinIO pin moves — the standing precondition that replaces multi-hostname CI coverage (see 2.10)
- [ ] 3.4 Studio reachable through Caddy without traversing Kong's `basic-auth` on the `dashboard` route — pre-existing, mitigated but not fixed by the anti-framing headers; #108 item 6's IP allowlist is the durable backstop
