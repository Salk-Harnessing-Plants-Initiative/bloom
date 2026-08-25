## 0. Phase 0 — resolve the two open technical questions (design.md) before locking in the rest

- [ ] 0.1 Confirm whether Cloudflare Tunnel's public-hostname routing works with a manually-added CNAME in an externally-hosted DNS zone (Salk's own DNS for `bloom.salk.edu`, not a Cloudflare-managed zone), or requires the zone itself to be on Cloudflare. Check Cloudflare's current docs directly; if genuinely ambiguous, stand up a disposable test tunnel against a throwaway hostname to observe behavior directly rather than trusting secondhand claims. **No test to write first** — this is a fact-finding spike, not code.
- [ ] 0.2 Based on 0.1's answer, finalize the tunnel hostname. If `bloom.salk.edu` works directly: pick a single-label name (e.g. `staging-mcp-tunnel.bloom.salk.edu`). If not: identify the smallest viable alternative (e.g. a name under the already-Cloudflare-managed `talmolab.org` zone used for ACME) and confirm it doesn't collide with anything already there.
- [ ] 0.3 `docker network inspect supanet` (or the compose-project-qualified equivalent) to determine the bridge network's actual subnet, and whether it's pinned or Docker-assigned. If Docker-assigned and not already stable across recreates, add an explicit `ipam.config` subnet to the `supanet` network definition in `docker-compose.prod.yml` so `trusted_proxies` (task 2.3) has a CIDR that won't silently change.
  - **Test first:** `tests/unit/test_supanet_subnet_pinned.py` — asserts `docker-compose.prod.yml`'s `networks.supanet` block declares an explicit `ipam.config.subnet` (fails today, since none exists — confirms the gap before fixing it).

## 1. `cloudflared` service (docker-compose.prod.yml)

- [ ] 1.1 Add a `cloudflared` service to `docker-compose.prod.yml`: official `cloudflare/cloudflared` image (digest-pinned, matching this repo's existing digest-pinning convention for third-party images — see the `add-edge-security-headers` precedent), `networks: [supanet]`, `environment: { TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN} }`, no host ports published (outbound-only connection to Cloudflare's edge).
  - **Test first:** `tests/unit/test_cloudflared_service_shape.py` — parses `docker-compose.prod.yml` and asserts a `cloudflared` service exists, is on `supanet`, references `${CLOUDFLARE_TUNNEL_TOKEN}`, and publishes no `ports:`. Fails before 1.1, passes after.
- [ ] 1.2 Confirm `scripts/validate_env.sh` picks up `CLOUDFLARE_TUNNEL_TOKEN` automatically from the new `${VAR}` reference (it derives its required-var list from `docker-compose.prod.yml` — see the script's own header comment) — no code change expected, just confirm.
  - **Test first:** `tests/unit/test_validate_env_derives_tunnel_token.py` — runs `validate_env.sh`'s var-extraction logic (or the script itself in check mode) against the modified compose file and asserts `CLOUDFLARE_TUNNEL_TOKEN` appears in its required-var list.

## 2. Caddy routing (caddy/Caddyfile)

- [ ] 2.1 Add a `TUNNEL_HOSTNAME` env var (threaded through the `caddy` service's `environment:` block in `docker-compose.prod.yml`, alongside `DOMAIN_MAIN` etc.) and a new `@tunnel host {$TUNNEL_HOSTNAME}` matcher in `caddy/Caddyfile`.
- [ ] 2.2 Add `handle @tunnel { ... }` containing only the 4 routes: `handle_path /bloommcp/*`, `handle /.well-known/oauth-protected-resource/bloommcp/*`, `handle_path /api/*` → kong, `handle /api/oauth/consent` → bloom-web. Comment explicitly that these are hand-duplicated from `@main` and must be kept in sync (design.md's drift risk).
  - **Test first:** `tests/unit/test_caddy_tunnel_route_scope.py` — parses the Caddyfile and asserts: (a) a `@tunnel` matcher exists keyed on `TUNNEL_HOSTNAME`; (b) its handle block proxies exactly the 4 expected upstream targets and paths; (c) it contains no route to `bloom-web`'s catch-all, `studio`, or `supabase-minio`. Fails before 2.1/2.2 (no `@tunnel` matcher exists), passes after.
- [ ] 2.3 Add a `trusted_proxies` global option (Caddy global options block, top of Caddyfile) trusting `supanet`'s subnet (pinned in 0.3), with `client_ip_headers Cf-Connecting-IP`. Add a `remote_ip` matcher inside `handle @tunnel` scoped to Anthropic's published ranges (re-verify against `platform.claude.com/docs/en/api/ip-addresses` at implementation time — do not blindly reuse a cached value), rejecting non-matching requests before they reach any backend.
  - **Test first:** `tests/unit/test_caddy_tunnel_waf.py` — asserts `trusted_proxies` is configured with `client_ip_headers Cf-Connecting-IP`, and that `handle @tunnel`'s routes are gated behind a `remote_ip` (or `not remote_ip`) matcher referencing the current Anthropic CIDR list. Fails before 2.3.
- [ ] 2.4 `caddy validate` against the project's own built image (per `add-edge-security-headers`'s precedent — the stock image can't parse `tls { dns cloudflare }`, and now also needs `trusted_proxies`/`client_ip_headers` support, confirm the pinned Caddy version supports both).

## 3. Live verification (requires a human on/off Salk VPN — cannot run in CI, per this repo's established pattern for staging-network diagnostics)

- [ ] 3.1 Configure the Cloudflare Named Tunnel and public hostname in the Cloudflare Zero Trust dashboard, using the hostname finalized in 0.2. Record the exact steps taken (not just the outcome) so this is reproducible for the prod follow-up.
- [ ] 3.2 Deploy to staging with the new `cloudflared` service running. Confirm `cloudflared`'s own logs show a successful tunnel connection (QUIC by default — see design.md's decision not to force `http2`).
- [ ] 3.3 From a network **without** Salk VPN access, confirm `https://<tunnel-hostname>/bloommcp/mcp`'s discovery/auth-rejection behavior matches the existing VPN-only path's behavior (same clean 401 + `WWW-Authenticate` header, per `add-bloommcp-connect-guide`'s prior verification of the VPN path).
- [ ] 3.4 From that same non-VPN network, confirm a request to the tunnel hostname's root (`/`) does NOT reach bloom-web — 404, per the spec's "does not expose bloom-web" scenario.
- [ ] 3.5 Send a request through the tunnel with a spoofed `Cf-Connecting-IP` header claiming an Anthropic-range IP, from a source that is not actually `cloudflared` (e.g. curl directly against the tunnel hostname, not through Cloudflare's edge, if reachable at all — or against Caddy's exposed port directly with the header forged). Confirm it's rejected, proving `trusted_proxies` isn't naively trusting any client-supplied header. This is the live counterpart to the spec's spoofing scenario and 2.3's unit test.
- [ ] 3.6 Confirm existing `DOMAIN_MAIN` traffic (over VPN, as today) is unaffected — spot-check a handful of routes.

## 4. Documentation

- [ ] 4.1 Document the staging host's current `ufw` ruleset in `PROD_SETUP.md` (the `53`, `80/tcp`, `443/tcp` base rules, the three pinned host:port exceptions, and the `7844/udp`+`7844/tcp` rules this change adds), so this isn't tribal knowledge requiring a fresh SSH session to rediscover.
  - **Test first:** none practical (this is documentation, not code) — verify by hand that `PROD_SETUP.md` accurately reflects `sudo ufw status verbose`'s actual output at merge time.

## 5. Follow-ups (not this change)

- [ ] 5.1 Production rollout — new change, once staging (§3) is confirmed working end-to-end. Must explicitly plan for the `staging → main` promotion path (design.md), not assume one.
- [ ] 5.2 Desktop's in-app "Connectors" UX and the `connecting-claude-code.md` Desktop section — depends on this change but is its own scope (mirrors `add-bloommcp-oauth-staging-verification`'s deferral of the same doc).
- [ ] 5.3 Revisit #265/#617/#618/#108 before any prod rollout, even though this change doesn't block on them for staging (proposal.md Non-Goals).
- [ ] 5.4 Consider factoring the 4 duplicated routes (`@main` vs `@tunnel`) into a shared Caddyfile `import` snippet if they drift in practice (design.md Open Questions).
