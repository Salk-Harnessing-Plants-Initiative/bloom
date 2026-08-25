## Why

bloommcp is reachable only from Salk wifi/VPN today (`caddy/Caddyfile:141`, "Salk-network-internal only" — enforced purely by network perimeter, not by any Caddy config). That blocks Claude Desktop from ever reaching it: Desktop's custom-connector traffic originates from Anthropic's cloud infrastructure, not the researcher's device, unlike Claude Code, which connects device-side and already works today.

[#616](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/616) evaluated three network paths and the team decided (2026-08-25) on a self-managed Cloudflare Tunnel:
- A narrow Salk IT firewall allowlist was ruled out — `/bloommcp/*` is a Caddy *path* under the same host+port as the rest of the stack (`caddy/Caddyfile`), which a standard IP+port firewall rule cannot scope to without bloommcp having its own port or a Caddy-level matcher (this change builds the latter).
- Anthropic's "MCP tunnels" product was ruled out — confirmed scoped to Claude Managed Agents/Messages API only, explicitly not usable as a claude.ai/Desktop custom connector.

This change implements the Cloudflare Tunnel on **staging only**. Production is an explicit follow-up (see Non-Goals), mirroring how #633 sequenced OAuth (staging first, prod once validated).

## What Changes

- **New `cloudflared` service** in `docker-compose.prod.yml`, joining the existing `supanet` bridge network, running a Cloudflare Named Tunnel that proxies a new dedicated public hostname to Caddy's internal `:443` (container-internal port — unaffected by staging's host-side `8080`/`8443` offset, since the tunnel connects container-to-container, not through the host-published ports).
- **A new dedicated hostname** for the tunnel — not the existing `DOMAIN_MAIN` (`staging.bloom.salk.edu`) — so the tunnel's public exposure is scoped to bloommcp's own routes, not all of bloom-web/Kong/MinIO. Exact hostname is a Phase 0 decision (design.md) — it must be a single-label subdomain of `bloom.salk.edu` to ride the existing wildcard cert (`CADDY_SITE_ADDRESSES=https://*.bloom.salk.edu`) with zero TLS/cert changes.
- **A new `@tunnel` host matcher + handle block** in `caddy/Caddyfile`, proxying only the 4 routes bloommcp's OAuth flow needs through this new hostname — `/bloommcp/*`, `/.well-known/oauth-protected-resource/bloommcp/*`, `/api/auth/v1/*` (Kong/GoTrue), `/api/oauth/consent` (bloom-web) — not bloom-web's general catch-all, Studio, or MinIO.
- **A Caddy `remote_ip` restriction** on the new matcher, scoped to Anthropic's published IP ranges, resolved from the *real* client IP (via Cloudflare's `Cf-Connecting-IP` header, using Caddy's `trusted_proxies` mechanism) rather than the raw TCP peer, which would otherwise always be the `cloudflared` container. Without this, the tunnel's public hostname has zero network-layer defense — bloommcp's own Bearer/OAuth check becomes the only thing standing between the internet and this endpoint, which is the tradeoff #616 explicitly flagged when choosing this option.
- **A new `STAGING_CLOUDFLARE_TUNNEL_TOKEN` GitHub secret**, threaded through `deploy.yml`'s existing secrets-heredoc (`.github/workflows/deploy.yml:889`) and preflight pattern (`:981-999`) — a distinct secret from the existing `STAGING_CLOUDFLARE_API_TOKEN` (DNS-01 ACME, unrelated purpose; do not conflate or reuse).
- **Document the staging host's `ufw` outbound ruleset** in `PROD_SETUP.md` — currently undocumented anywhere in the repo, discovered only by SSHing in and running `ufw status verbose` during this change's own investigation. Include the two rules this change requires (`7844/udp`, `7844/tcp` — already added live to the host as of 2026-08-25) alongside the pre-existing rules (`53`, `80/tcp`, `443/tcp`, and the three pinned host:port exceptions).

**Explicitly out of scope:**
- **Production rollout.** Staging only. A follow-up change once staging is validated end-to-end. Also note: landing this on `staging` does not get it to prod on any schedule — `main` only receives periodic, human-triggered `staging → main` rollup PRs (`openspec/project.md:134`), and the daily security-promotion bot (`promote-security-to-main.yml`) only picks up security-labeled commits, which this isn't. The prod follow-up needs to explicitly track getting into a rollup, not assume one.
- **Fixing #265 / #617 / #618 / #108.** All confirmed still open as of 2026-08-25 (checked directly via `gh issue view`). Tracked as pre-existing, separately-owned risk — not blocking, since staging is not yet public-facing to real end users via this path. Not touched by this change.
- **Desktop's in-app "Connectors" UX and updating `bloommcp/docs/connecting-claude-code.md`'s Desktop section.** Those depend on this change existing but are their own follow-up (mirrors how `add-bloommcp-oauth-staging-verification` left doc-writing to a later change pending #616).
- **The DNS zone-ownership and Docker bridge-subnet questions** (design.md, Phase 0) are resolved as part of *this* change's own Phase 0 tasks, not deferred — they gate whether the rest of this proposal's design is even viable as written.

## Impact

- **Affected specs:** `edge-cloudflare-tunnel` (new capability)
- **Affected code:** `docker-compose.prod.yml` (new `cloudflared` service), `caddy/Caddyfile` (new host matcher + `remote_ip`/`trusted_proxies` config), `.github/workflows/deploy.yml` (new secret heredoc line + preflight step, staging job only — `deploy-production` untouched), `.env.staging.defaults` (new non-secret hostname var), `PROD_SETUP.md` (document `ufw` ruleset)
- **Not affected:** `scripts/validate_env.sh` (no code change needed — it auto-derives its required-var list from `${VAR}` references in `docker-compose.prod.yml`); `deploy-production` job; any prod env file
- **Affected infra (outside repo):** staging host's `ufw` rules (already modified live 2026-08-25 — `7844/udp`+`7844/tcp` added), a new Cloudflare Zero Trust Named Tunnel + public hostname config, staging host's DNS (new hostname's CNAME record — zone-ownership is an open Phase 0 question, see design.md)
- **Deployment:** additive — existing bloommcp/bloom-web/Kong traffic and the VPN-only path are unaffected; the new hostname is a second entry point, not a replacement
- **Risk:** primarily the network-perimeter-to-app-layer-auth tradeoff already accepted when #616 chose this option (mitigated here by the `remote_ip` restriction), plus two real open technical questions this proposal resolves via Phase 0 before the rest of the design is locked in (see design.md)
