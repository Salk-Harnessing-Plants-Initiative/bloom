## Context

Caddy fronts every hostname in the stack from a single site block (`caddy/Caddyfile:61-227`) whose listen addresses come from `CADDY_SITE_ADDRESSES`. Staging's value is `https://*.bloom.salk.edu` — one wildcard cert covering every single-label subdomain of `bloom.salk.edu` (`staging`, `staging-studio`, `staging-minio` today). `DOMAIN_MAIN`/`DOMAIN_STUDIO`/`DOMAIN_MINIO` are matched inside that one block via `@matcher host X` + `handle @matcher`; there is no per-hostname TLS config to duplicate.

`docker-compose.prod.yml` is shared by prod and staging (no separate staging compose file) — staging is distinguished only by compose project name (`-p bloom_v2_staging`) and `--env-file .env.staging`. Caddy's container-internal ports are always `80`/`443`; only the *host-published* ports differ (`8080`/`8443` for staging, to avoid colliding with prod on the same physical host). A sibling container on the `supanet` bridge network reaches Caddy at `caddy:443` regardless of environment — the host-port offset is irrelevant to container-to-container traffic.

The four routes bloommcp's OAuth flow (PR #613) needs are currently only reachable under `DOMAIN_MAIN`, inside `handle @main` (`caddy/Caddyfile:92-163`):
1. `handle_path /bloommcp/*` → `bloommcp:8811` (`:142`)
2. `handle /.well-known/oauth-protected-resource/bloommcp/*` → `bloommcp:8811` (`:134`, origin-root per RFC 9728, must stay a `handle` not `handle_path`)
3. `handle_path /api/*` → `kong:{$KONG_PORT}` (`:117`) — serves `/api/auth/v1/*`, the actual OAuth authorization server + JWKS (`BLOOMMCP_OAUTH_AUTHORIZATION_SERVER`/`BLOOMMCP_OAUTH_JWKS_URI` both point here)
4. `handle /api/oauth/consent` → `bloom-web:{$BLOOM_WEB_PORT}` (`:106`, exact path ahead of `/api/*` on purpose)

The driver is #616's decision to reach these four routes from Claude Desktop's cloud-side connector infrastructure, which cannot reach a VPN-only host.

## Goals / Non-Goals

**Goals**
- Make the 4 routes above reachable from the public internet, restricted to Anthropic's published IP ranges, without changing their behavior for existing (Salk-network) callers.
- Keep the tunnel's blast radius scoped to bloommcp's own routes — not bloom-web's general UI, Studio, or MinIO.
- Land this on staging only, with the prod follow-up explicitly planned rather than assumed.
- Establish the `edge-cloudflare-tunnel` capability as the place future tunnel-adjacent decisions (e.g. the prod rollout) attach to.

**Non-Goals**
- Production rollout (separate follow-up change).
- Fixing #265/#617/#618/#108 (pre-existing, separately tracked; see proposal.md).
- Desktop's in-app "Connectors" UX, or writing `connecting-claude-code.md`'s full Desktop connect flow. (A single one-line forward-reference noting this change's existence in that doc's already-deferred stub is in scope — see tasks.md §5.3 — since the alternative is that doc's existing "Salk wifi or VPN" prerequisite statement becoming actively misleading the moment this ships. Writing the actual Desktop flow itself stays deferred to #616's remaining pieces.)
- Any change to bloommcp's own auth logic (`bloom_mcp/auth.py`) — this change is purely about what can reach it over the network, not how it authenticates once reached.

## Open Questions — Phase 0 (must resolve before Phase 1 locks in)

**1. Does the new hostname's CNAME need a Cloudflare-managed DNS zone, or can it live in Salk's existing DNS for `bloom.salk.edu`?**

Cloudflare Tunnel's "Public Hostname" routing is configured in the Cloudflare Zero Trust dashboard and is commonly paired with `cloudflared tunnel route dns`, which creates the CNAME *via Cloudflare's API* — that requires the zone to be added to Cloudflare. `bloom.salk.edu` is **not** a Cloudflare-managed zone today: the repo's only existing Cloudflare integration is DNS-01 ACME via a **CNAME delegation** (`_acme-challenge.bloom.salk.edu` → `_acme-challenge.bloom-acme.talmolab.org`, a *different*, Cloudflare-managed zone) — `caddy/Caddyfile:38-41`. Whether Cloudflare Tunnel routing works with a hostname whose zone lives in a third-party DNS provider (a manually-created CNAME to `<tunnel-id>.cfargotunnel.com` in Salk's own DNS, no zone transfer) is not something this repo's history answers, and I have not verified it against current Cloudflare product behavior.
- **Task:** confirm directly (Cloudflare's own current docs, or a disposable test tunnel) whether a manually-added CNAME in an externally-hosted zone is sufficient, before assuming either answer.
- **Fallback if not:** delegate a single new subdomain (e.g. `bloom-tunnel.salk.edu` or a name under the already-Cloudflare-managed `talmolab.org`) — a smaller, more likely IT ask than moving `bloom.salk.edu` itself.

**2. Does Caddy's `remote_ip` matcher see the real internet client, or the `cloudflared` container?**

`cloudflared` terminates the tunnel and makes its own outbound connection to the origin (Caddy) like any reverse proxy — the TCP peer Caddy observes is `cloudflared`'s container IP on `supanet`, not the original client. A bare `remote_ip` restriction on the new host matcher would therefore never see an Anthropic IP and would either block everyone or (if misconfigured) restrict nothing.
- **Decision:** use Caddy's `trusted_proxies`/`client_ip_headers` mechanism, trusting `supanet`'s subnet, with `client_ip_headers Cf-Connecting-IP` (Cloudflare's own header carrying the true client IP) so Caddy resolves the real client IP before `remote_ip` evaluates it. **Exact syntax matters here and is easy to get wrong:** both directives are `servers` sub-options inside Caddy's *global options* block — `{ servers { trusted_proxies static {$SUPANET_SUBNET} \n client_ip_headers Cf-Connecting-IP } }` — not bare top-level directives, and the `static` module name before the CIDR is required (`trusted_proxies {$SUPANET_SUBNET}` alone is invalid). `caddy/Caddyfile` currently has **no global options block at all** — it opens directly with `{$CADDY_SITE_ADDRESSES} {` — so this requires inserting a new top-level `{ }` block, which must be positioned *first* in the file, before the site block.
- **Task:** confirm `supanet`'s actual subnet (`docker network inspect`) and whether it's stable across recreates. Only pin it explicitly in the compose file if the investigation actually finds instability or a collision risk — see the reworked Phase 0 task 0.3, which no longer assumes pinning is necessary before checking.

Both are pre-flight tasks (see tasks.md §0) — the rest of this design assumes their answers, and either could change the hostname/DNS approach or the Caddy config shape.

## Decisions

**Decision: a self-managed Cloudflare Tunnel, over a Salk IT firewall allowlist or Anthropic's own tunnel product.**
#616 evaluated three network paths before this change existed:
- *A narrow Salk IT firewall allowlist* (permit Anthropic's published IP range, deny everything else) — rejected: `/bloommcp/*` is a Caddy *path* under the same host+port as the rest of the stack, not something a standard IP+port firewall rule can scope to. It would need either a dedicated port for bloommcp (no precedent in this stack) or exactly the kind of Caddy-level matcher this change builds anyway — at which point the firewall rule adds nothing a Cloudflare Tunnel doesn't already provide.
- *Anthropic's own "MCP tunnels" product* — rejected: confirmed (via Anthropic's own documentation) to be scoped to Claude Managed Agents/Messages API only, explicitly not available as a claude.ai/Desktop custom connector.
- *A self-managed Cloudflare Tunnel* — chosen. Works with Desktop's Connectors UI, requires only outbound connectivity from the staging host (no inbound firewall change), and the routing/IP-restriction problem the firewall-allowlist option couldn't solve is solved here at the Caddy layer instead (see the `remote_ip`/`trusted_proxies` decision below).

**Decision: mint a new single-label hostname under `bloom.salk.edu`, not a new sub-subdomain.**
The wildcard `*.bloom.salk.edu` covers exactly one label — `staging-mcp-tunnel.bloom.salk.edu` works with zero cert changes; `mcp.staging.bloom.salk.edu` would not (two labels, uncovered). Exact name (`staging-mcp-tunnel` vs. some other slug) is a naming call, not a technical constraint — placeholder pending Phase 0's DNS-zone answer, since that answer may force a different domain entirely.
*Alternatives considered:* reusing `DOMAIN_MAIN` — rejected per proposal.md (exposes bloom-web/Kong/MinIO too, far larger blast radius than bloommcp's 4 routes needs).

**Decision: a new `@tunnel` matcher inside the existing site block, not a second Caddy site block.**
`CADDY_SITE_ADDRESSES` already drives one block covering every `*.bloom.salk.edu` label; a second block would duplicate the `tls`/security-header declarations for no benefit. Add `@tunnel host {$TUNNEL_HOSTNAME}` alongside `@main`/`@studio`/`@minio`, with its own `handle @tunnel { ... }` containing only the 4 proxied routes (duplicated from `@main`'s versions, not shared — see Risk below) plus the `remote_ip` restriction.
*Alternatives considered:* factoring the 4 routes into a shared Caddy snippet included from both `@main` and `@tunnel` — deferred; Caddyfile's `import` mechanism can do this, but introduces its own risk (a change to the shared snippet silently affects both hostnames) that isn't justified until there's evidence the duplication actually drifts.

**Decision: `cloudflared` runs QUIC by default; no forced `http2` protocol override.**
Originally planned to force `protocol: http2` (port 443) because raw `nc -vz` to Cloudflare's edge on port 7844 timed out from the staging host. Root cause was `ufw`'s default-deny outbound policy not including 7844 — not a Salk-network-level block, and not anything about QUIC specifically. `7844/udp` and `7844/tcp` are now allowed outbound (added live 2026-08-25). Default (QUIC, auto-fallback to `http2`) is simpler and is cloudflared's own recommended default; forcing `http2` would only be a fallback if a live end-to-end test (tasks.md §4.2) shows QUIC failing for some other reason.
*Alternatives considered:* forcing `http2` unconditionally — rejected now that the actual blocker (the `ufw` rule) is fixed; would have been the right call before that fix.

**Decision: `remote_ip` scoped to Anthropic's published IP ranges, resolved via `trusted_proxies` (see Open Question 2).**
Without this, the tunnel's public hostname has no network-layer defense — see proposal.md Impact. Ranges as last verified (needs re-verification at implementation time, per `platform.claude.com/docs/en/api/ip-addresses` — these are known to drift):
- `160.79.104.0/21` (Anthropic's outbound = our inbound)
- `160.79.104.0/23`, IPv6 `2607:6bc0::/48`
*Alternatives considered:* skipping the IP restriction and relying on bloommcp's own Bearer/OAuth check alone — rejected; this is exactly the tradeoff #616 flagged as the cost of choosing the tunnel route, and the whole point of doing this work carefully is not accepting that tradeoff by default.

**Decision: staging only; prod is a separate, explicitly-planned follow-up.**
Matches #633's staging-first sequencing for OAuth. Also: `staging → main` is a manual, periodic rollup PR (`openspec/project.md:134`), not automatic, and the daily security-promotion bot only picks up security-labeled commits — this change isn't one. tasks.md §6.1 records this as a concrete follow-up task, not a silent assumption that merging to staging eventually reaches prod on its own.

**Decision: gate `cloudflared` behind a Compose `profiles:` entry, activated only by staging's deploy step.**
`docker-compose.prod.yml` is one file shared by prod and staging (see Context) — a plain, un-gated service definition would start in *both* environments, and since `.env.prod` would have no real `CLOUDFLARE_TUNNEL_TOKEN`, `cloudflared` would crash-loop in production the moment this merges and prod redeploys. No service in this repo uses Compose `profiles:` today — this is a new pattern, not an existing one to follow, and nothing in this repo's docs describes it yet (see the doc task below).
- Add `profiles: ["cloudflare-tunnel"]` to the `cloudflared` service. This alone makes production and CI safe: Compose excludes a profile-gated service from `up` entirely unless that profile is explicitly activated, and neither `deploy-production` nor `pr-checks.yml`'s `compose-health-check` ever activates it — verified directly against Compose's actual behavior (an undefined `${VAR}` reference on a service that never starts produces a harmless interpolation warning, not a failure).
- Staging's deploy step passes `--profile cloudflare-tunnel` (or sets `COMPOSE_PROFILES=cloudflare-tunnel`) on **both** of its `docker compose up` invocations in `deploy.yml` — the forward deploy step *and* the separate rollback step. Missing the rollback path would mean a rollback runs without the profile, and depending on Compose's version-specific `--remove-orphans` behavior toward a profile-inactive-but-currently-running service, could silently tear the tunnel down with no error. Production's steps are untouched.
- `scripts/validate_env.sh` is genuinely profile-blind (it greps the whole compose file's `${VAR}` references unconditionally, with no awareness of `profiles:`), so it **will** start demanding `CLOUDFLARE_TUNNEL_TOKEN`/`TUNNEL_HOSTNAME` in `.env.prod` the moment this merges — add harmless, non-functional placeholder values to `.env.prod`'s assembly in `deploy.yml` (e.g. `CLOUDFLARE_TUNNEL_TOKEN=unused-not-enabled-in-prod`) so the very next production deploy's validation step still passes.
- **CI needs a placeholder too, but only for `TUNNEL_HOSTNAME`, and for a different reason than the token.** `validate_env.sh` is never invoked against `.env.ci` at all (confirmed: `pr-checks.yml` has zero references to it), and `cloudflared` never starts in CI regardless of the profile — so `CLOUDFLARE_TUNNEL_TOKEN` needs no CI placeholder; adding one would be unnecessary. `TUNNEL_HOSTNAME`, however, is consumed by **Caddy**, a profile-less service that always starts, inside `@tunnel host {$TUNNEL_HOSTNAME}` (task 2.1). An unset value there risks an empty/invalid `host` matcher argument at Caddy startup — a real parse risk, not a hypothetical one. Add a placeholder `TUNNEL_HOSTNAME` line to `pr-checks.yml`'s `.env.ci` generation for this reason alone.
- Also wire `SUPANET_SUBNET` (the pinned-subnet decision below) into the `caddy` service's `environment:` block, the same way `TUNNEL_HOSTNAME` is — `trusted_proxies`/`client_ip_headers` (task 2.3) needs it and nothing threads it through otherwise.
- Document the `profiles:` pattern itself in `PROD_SETUP.md`, not `_WIKI/CADDY/README.md` — that wiki page's own scope is Caddy/TLS/routing specifically, with no precedent for Compose-mechanics content, whereas `PROD_SETUP.md` already documents the actual `docker compose ... up` invocation this profile flag modifies. Since it's genuinely new to this repo, the next engineer touching `docker-compose.prod.yml` should be able to find out it exists from the doc that already covers how that file gets invoked.
*Alternatives considered:* a separate `docker-compose.staging.yml` overlay — rejected; no such file exists today for any other staging-specific difference (staging differences are handled entirely via `-p` project name + `--env-file`), and introducing one just for this would be a bigger, less consistent change than a profile.

**Decision: pin `supanet`'s subnet only if Phase 0's investigation actually finds it unstable or colliding — not unconditionally.**
Prod and staging run on the *same host*, sharing `docker-compose.prod.yml`, distinguished only by `-p` compose project name. Docker's default IPAM allocator generally avoids handing out overlapping subnets to different networks on the same host, so it's possible the two environments already get distinct, stable subnets with zero changes — in which case adding `SUPANET_SUBNET` plumbing everywhere would be solving a problem that doesn't exist. Task 0.3 is a genuine investigation, not a foregone conclusion: pin the subnet (via an env var, e.g. `${SUPANET_SUBNET}`, with distinct values in `.env.prod`/`.env.staging` — never a literal value in the compose file itself, since a single hardcoded CIDR there would make both `bloom_v2_prod_supanet` and `bloom_v2_staging_supanet` request the identical range) **only if** the investigation finds real instability or a collision risk. If it doesn't, skip the pinning entirely and use `trusted_proxies static <the-observed-stable-CIDR>` as a plain literal instead — simpler, and just as correct if the subnet genuinely doesn't change.

## Risks / Trade-offs

- **DNS zone ownership (Open Question 1) could force a different hostname/domain than planned**, which would ripple into `BLOOMMCP_PUBLIC_URL`-equivalent config for the tunnel path and any researcher-facing docs written later. → Resolved in Phase 0, before the "real" implementation tasks, specifically to avoid discovering this after the Caddy/compose work is written.
- **`remote_ip` silently doing nothing (Open Question 2)** — if `trusted_proxies` is misconfigured, Caddy could either see `cloudflared`'s container IP (blocking everyone, including legitimate Anthropic traffic) or, worse, trust a header from an untrusted source and let anyone spoof `Cf-Connecting-IP` to bypass the restriction entirely. These are two distinct failure modes on two distinct code paths — a spoofed header from a peer outside the trusted subnet (tests whether `trusted_proxies` gates by *peer address*) versus a genuinely absent header from the trusted peer itself (tests the *fallback* when `client_ip_headers` finds nothing to read, which should resolve to `cloudflared`'s own non-Anthropic container IP and get rejected the same as any other out-of-range request). → tasks.md §3 covers both as separate, named tests, plus a positive-case test confirming a genuine in-range `Cf-Connecting-IP` from the trusted peer is allowed through — rejection-only coverage would leave the capability's actual purpose unverified.
- **Route duplication between `@main` and `@tunnel` drifting.** The 4 routes are hand-copied, not shared. A future change to one of them under `@main` (e.g. a new bloommcp path) could be forgotten under `@tunnel`. → tasks.md §2 includes `test_caddy_tunnel_matches_main_bloommcp_routes`, diffing the two blocks' upstream targets for the 4 shared paths so a drift fails CI, not just a code comment asking editors to remember.
- **`ufw` rules are unmanaged infrastructure state.** Already true before this change (see `PROD_SETUP.md` gap), made concretely worse by this change adding two more rules the same way. → tasks.md §5.1 documents the current ruleset in `_WIKI/CADDY/README.md` (with a pointer from `PROD_SETUP.md`), closing the immediate gap; does not (out of scope) migrate `ufw` management into IaC.
- **`cloudflared` running un-gated in a compose file shared with production.** Would crash-loop in prod and break `validate_env.sh` for the next production deploy. → tasks.md §1.1-1.4 gates it behind a Compose `profiles:` entry (with a dedicated test asserting the profile is actually declared), adds the placeholder env values `validate_env.sh` genuinely needs, and covers both the forward-deploy and rollback `up` calls in `deploy.yml` so the profile can't be missed on one path and not the other.
- **Public hostname + WAF is weaker than network-perimeter-only**, accepted explicitly when #616 chose this option. → Mitigated, not eliminated, by the `remote_ip` restriction; #265/#617/#618/#108 remain real residual risk, tracked separately per proposal.md Non-Goals.

## Migration Plan

1. Resolve Phase 0 questions (tasks.md §0) — DNS zone ownership, `supanet` subnet stability.
2. Land Caddy + compose + deploy.yml changes on this branch; merge to `staging`.
3. Configure the Cloudflare Named Tunnel and public hostname (Cloudflare Zero Trust dashboard) — not code, so not part of this PR's diff, but required before verification can run; document the steps taken in tasks.md as they're executed.
4. Add `STAGING_CLOUDFLARE_TUNNEL_TOKEN` to GitHub Environment secrets.
5. Deploy to staging; run end-to-end verification (tasks.md §3-4). The happy-path check (§4.3) uses Claude Desktop's actual Custom Connector UI pointed at the tunnel hostname — real Anthropic-origin traffic — rather than trying to manually curl from a vantage point inside Anthropic's IP ranges, which no one on this team has access to.
6. **Rollback:** remove the `cloudflared` service and the `@tunnel` Caddyfile block; the new hostname stops resolving to anything useful once the tunnel is torn down on Cloudflare's side. No impact on `@main`/`@studio`/`@minio` traffic at any point — this is purely additive.

## Open Questions

- Exact tunnel hostname — placeholder pending Phase 0's DNS answer (design.md, Open Question 1).
- Whether route duplication (`@main` vs `@tunnel`) should later move to a shared Caddyfile `import` snippet — deferred, not rejected; revisit if the two drift in practice.
- Whether `ufw` rule management should eventually move into IaC/deploy automation — out of scope here; this change only documents the current state.
