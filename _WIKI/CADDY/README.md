# Caddy

This page covers how Caddy is set up.

Caddy is the web server that sits in front of bloom. It takes every incoming HTTPS request and forwards it to the right backend container.

This page covers how Caddy is set up, how it gets real HTTPS certificates via Lets Encrrypt, how those certificates survive a redeploy, and what happens when they need to be renewed.

Update this page whenever you change the Caddyfile, the Dockerfile, how certs are issued, or the per-environment hostnames.

## Stack shape

One `caddy` container per environment(staging and prod), built from a project-owned Dockerfile rather than a stock image — see [caddy/Dockerfile](../../caddy/Dockerfile) and [caddy/Caddyfile](../../caddy/Caddyfile).

| Concern                        | Where                                                                                                                                                    |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Image build                    | [caddy/Dockerfile](../../caddy/Dockerfile) — two-stage `xcaddy build` with `caddy-dns/cloudflare` linked in                                            |
| Routing + TLS config           | [caddy/Caddyfile](../../caddy/Caddyfile)                                                                                                                    |
| Security headers               | Site-level `header` block in [caddy/Caddyfile](../../caddy/Caddyfile) — see [Security headers](#security-headers)                                          |
| Container declaration          | `caddy` service in [docker-compose.prod.yml](../../docker-compose.prod.yml)                                                                               |
| Per-env site addresses + token | `CADDY_SITE_ADDRESSES` + `CLOUDFLARE_API_TOKEN` in [.env.prod.defaults](../../.env.prod.defaults) and [.env.staging.defaults](../../.env.staging.defaults) |
| Deploy-time secret injection   | `PROD_/STAGING_CLOUDFLARE_API_TOKEN` heredocs in [.github/workflows/deploy.yml](../../.github/workflows/deploy.yml)                                       |

## TLS strategy

Our stack uses real Let's Encrypt SSL certs on staging and prod.

Let's Encrypt is free and trusted by every browser, but they only hand out certs after you prove you control the domain. That proof is called an  **ACME challenge** . There are two common forms:

* **HTTP-01** — put a file at a specific URL on your server
* **DNS-01** — put a TXT record at a specific name in DNS

We use DNS-01. Here's why HTTP-01 doesn't work for us.

### Why HTTP-01 doesn't work

HTTP-01 makes Let's Encrypt fetch `http://bloom.salk.edu/.well-known/acme-challenge/<token>`. That requires Let's Encrypt's servers to reach our server on port 80 from the public internet.

`bloom.salk.edu` sits behind Salk's firewall. Port 80 is blocked from outside. Let's Encrypt can't reach the file → challenge fails → no cert. Dead on arrival.

### Why DNS-01 works (with one catch)

DNS-01 instead asks: "put a TXT record at `_acme-challenge.bloom.salk.edu`." Let's Encrypt then looks up the TXT through normal DNS — no need to reach our server directly. Firewalls don't matter.

The catch: someone has to actually create that TXT record. That means write access to the `salk.edu` DNS zone — which Salk IT does not hand out to application containers.

### Why we use CNAME delegation

Since we can't write to Salk's DNS, we go around it. We own a separate Cloudflare zone, `bloom-acme.talmolab.org`. Salk IT publishes one permanent CNAME:

```text
_acme-challenge.bloom.salk.edu  CNAME  _acme-challenge.bloom-acme.talmolab.org
```

This says "any lookup for `_acme-challenge.bloom.salk.edu` should go look at `_acme-challenge.bloom-acme.talmolab.org` instead." The CNAME delegates to the Cloudflare zone we control. Wildcard ACME challenges also land at the parent name, so this single record covers both the apex and the wildcard.

### Why a custom Dockerfile

Caddy is a Go binary — its modules are statically linked at build time, not loaded at runtime.

The default Caddy Docker image only knows about its built-in modules — it doesn't ship with any external DNS providers.

To use Cloudflare for TLS, we have to build our own Caddy binary with the Cloudflare

module compiled in — that's what `caddy/Dockerfile` does.

## Site addresses

The Caddyfile site block opens with `{$CADDY_SITE_ADDRESSES}`, which expands per-environment to a comma-separated, scheme-prefixed list:

| Env     | `CADDY_SITE_ADDRESSES`                                     | What it issues                                         |
| ------- | ------------------------------------------------------------ | ------------------------------------------------------ |
| prod    | `https://bloom.salk.edu, https://*.bloom.salk.edu`         | One cert with 2 SANs (apex + wildcard)                  |
| staging | `https://*.bloom.salk.edu`                                 | One cert with 1 SAN (wildcard covering all staging subdomains) |
| CI      | `http://localhost, http://studio.localhost, http://minio.localhost` | No cert —`http://` scheme disables ACME entirely    |

CI lists all three hostnames on purpose. A Host that matches no site address is answered by
Caddy's empty fallback server — a `200 OK` with no body — so a single-address CI would serve
nothing on the console hostnames while still looking healthy to any check that only asserts a
status code. That is also why the old Studio reachability test passed both before and after the
basic-auth gate existed: the Studio hostname has to be listed for a request carrying
`Host: studio.localhost` to enter the site block and reach `@studio` at all.

## Security headers

Caddy sets five response headers on every request it serves. They are declared once as a `header` block at site level — after the `tls` directive, before the `@main` / `@studio` / `@minio` host matchers — so all three hostnames inherit them from that single declaration.

| Header                                                  | What it prevents                                                                    |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `X-Content-Type-Options: nosniff`                       | A browser re-interpreting a response as HTML or script against its declared `Content-Type` |
| `X-Frame-Options: DENY`                                 | Clickjacking — another origin invisibly framing bloom to harvest authenticated clicks |
| `Content-Security-Policy: frame-ancestors 'none'`       | The same, in the standards-track form; `X-Frame-Options` covers older clients        |
| `Referrer-Policy: strict-origin-when-cross-origin`      | Experiment identifiers in URLs leaking to third parties via `Referer`                |
| `Permissions-Policy: camera=(), microphone=(), geolocation=()` | Use of browser features bloom does not need                                   |
| `Cross-Origin-Opener-Policy: same-origin-allow-popups`  | A site that opens bloom in a popup being able to script that window                 |

### Why site level, not per host

Studio and the MinIO console are third-party UIs this project does not build, and their access control is tracked separately. Declaring the block inside `handle @main` would cover the application while leaving those two bare — a gap that produces no error and no failing request, so nothing would surface it.

Site level also means Caddy applies the headers ahead of the handler chain, so responses Caddy generates itself (a synthetic `404`, an upstream-error `502`) carry them too.

### Two deliberate omissions

* **HSTS is not set.** Browsers cache it for its full `max-age` and it cannot be withdrawn server-side, so an accidental rollout is far harder to undo than anything else here. It lands with the public-exposure work, not incidentally.
* **CSP declares `frame-ancestors` only, no `script-src`.** Next.js emits inline hydration scripts, so a `script-src` without nonces would need `'unsafe-inline'` — which would not block an injected event handler and would be protection in name only. Doing it properly needs nonce middleware in bloom-web.

### If you change this block

`nosniff` makes browsers *hard-refuse* a script served with a non-JavaScript `Content-Type`, or a stylesheet that is not `text/css`. Before extending coverage to a new surface, check that its assets are correctly typed — this was measured against the Studio and MinIO images before those hostnames were covered, and should be re-checked whenever either image pin moves.

Two test layers guard the block, and both must stay in step with it:

* [tests/unit/test_caddy_security_headers.py](../../tests/unit/test_caddy_security_headers.py) — pins site-level placement by brace depth and asserts each value verbatim. It rejects both Caddy spellings of a per-host override (`header { ... }` and the single-line `header <Field> <value>` / `header -<Field>`), since either one silently downgrades the policy for that host alone.
* [tests/integration/test_api_endpoints.py](../../tests/integration/test_api_endpoints.py) — asserts the headers on the live wire across every handler under the main hostname, each upstream Kong fans out to, and both console hostnames.

## Cert persistence across redeploys

The `caddy` service mounts two named volumes:

```yaml
volumes:
  - caddy-data:/data
  - caddy-config:/config
```

`caddy-data` is a named Docker volume.

It survives `docker compose down`, `docker compose up`, and full container recreation — **only `docker volume rm` or `docker compose down -v` wipes it.**

Caddy stores the issued cert, the private key, the ACME account, and the renewal state inside `/data/caddy/certificates/...`.

On every redeploy Caddy boots, reads `/data`, finds the existing wildcard cert, checks the expiry, and:

- **Cert valid + outside renewal window** → uses the cached cert. Zero Let's Encrypt traffic. The 30–90 s ACME window only happens **once**, on the very first deploy.
- **Cert within 30 days of expiry** → triggers renewal in the background. Serving continues with the old cert until the new one lands.
- **Cert missing or expired** → blocking ACME on startup (same as first deploy).

## Automatic renewal

Caddy v2 runs an internal scheduler that wakes every ~10 minutes and checks every cert's expiry. 30 days before expiry it starts attempting renewal — same DNS-01 + Cloudflare flow it used for issuance, no restart, no human action, hot-reloads the new cert into the running listeners.

### Renewal failure notifications

**Currently: there is no automated notification when a renewal fails.** A silently failing renewal would only be discovered when the cert expires (90 days after issuance) and browsers start showing TLS errors to users.

Historical context: Let's Encrypt used to email warnings to the ACME account's contact address ~20 days before any cert expiring without a successful renewal. That service [ended on June 4, 2025](https://letsencrypt.org/2025/06/26/expiration-notification-service-has-ended) — LE no longer sends per-cert expiration warnings, citing cost, privacy, and the assumption that subscribers have working renewal automation. So setting an `email` directive in the Caddyfile no longer triggers any actionable notification for us.

Until we build something better, the only signals are:

- The cert visibly expires in browsers (worst possible UX)
- A human SSHs in and tails `docker compose logs caddy` looking for `cert_failed` / `challenge failed` entries
- A CT-log monitoring service (e.g. `crt.sh` watch) notices when a new cert ISN'T issued at the expected ~60-day cadence

Filling this gap is tracked in a follow-up issue — see the project's open issues for "cert renewal monitoring" / "notifications" for current status.

### What you need to verify externally (outside Caddy)

- The Cloudflare API token doesn't get revoked between issuances. 90-day cert + 30-day renewal window = check at least quarterly.
- The Salk CNAME stays in place.
- The container stays alive — if it's stopped for >60 continuous days the renewal window closes.

## Required secrets and DNS

These three must be in place before either environment can issue a cert:

| Requirement                      | Where it lives                                                                                                                                      |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PROD_CLOUDFLARE_API_TOKEN`    | GitHub Actions repo secret. Scope:`Zone:DNS:Edit` on the Cloudflare zone containing `bloom-acme.talmolab.org`.                                  |
| `STAGING_CLOUDFLARE_API_TOKEN` | GitHub Actions repo secret. Same scope; same value as the prod token is fine — both environments use the same Cloudflare delegation.               |
| Salk DNS CNAME                   | `_acme-challenge.bloom.salk.edu CNAME _acme-challenge.bloom-acme.talmolab.org`. Covers both prod and staging Caddy containers because wildcard ACME challenges all land at the parent name. |

The token is consumed at runtime by the `caddy-dns/cloudflare` plugin via the `CLOUDFLARE_API_TOKEN` env var, injected from the per-environment GitHub secret in the deploy workflow's heredoc.

## Hostnames

| Env     | Main (`DOMAIN_MAIN`)         | Studio (`DOMAIN_STUDIO`)            | MinIO (`DOMAIN_MINIO`)             |
| ------- | ------------------------------ | ------------------------------------- | ------------------------------------ |
| prod    | `bloom.salk.edu`         | `studio.bloom.salk.edu`         | `minio.bloom.salk.edu`         |
| staging | `staging.bloom.salk.edu` | `staging-studio.bloom.salk.edu` | `staging-minio.bloom.salk.edu` |

All staging hostnames sit under `bloom.salk.edu` so the wildcard `*.bloom.salk.edu` covers them.

> **DNS note:** browser access to `staging.bloom.salk.edu` requires the name to resolve. Salk's wildcard A record for `*.bloom.salk.edu` covers it from inside the Salk network (or on Salk VPN). From outside, you'll need a temporary `/etc/hosts` entry pointing the hostname at the bloom server's IP, or an explicit Salk DNS record.

### Studio terminates at Kong, not at Caddy

`DOMAIN_STUDIO` is the one hostname here whose UI does **not** end at its own
container. Caddy's `@studio` catch-all proxies `kong:{$KONG_PORT}`, and Kong's
`dashboard` service applies its `basic-auth` plugin before forwarding to
`studio:3000`. Supabase Studio has no authentication of its own, so that plugin is
the only thing in front of the console. Reaching it needs the `DASHBOARD` credential
(`DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` in `.env.{prod,staging}` on the deploy
host, populated from the `PROD_/STAGING_DASHBOARD_*` deploy secrets).

Three things to know before debugging this hostname:

- **Kong is in Studio's availability path.** Kong down, or holding a config that
  failed to load, means Studio is unreachable even when Studio itself is fine.
- **A healthy `studio` container is not evidence Studio is reachable.** Its
  healthcheck hits `http://studio:3000/api/platform/profile` from inside its own
  container and traverses neither Caddy nor Kong. Probe end-to-end through the
  hostname instead of trusting `docker compose ps`.
- **Studio requests are bounded at 330s** by the `dashboard` service's
  `read_timeout` (`volumes/api/kong.yml`). Before it was routed through Kong there
  was no bound at all. Postgres cancels at 300s first, so a 504 at 330s means
  something other than a slow query — and note a 504 does **not** cancel the
  statement; the backend keeps running.

The `/auth/*`, `/rest/*`, `/storage/*` and `/realtime/*` prefixes on this same
hostname are separate `handle_path` blocks reaching their own key-auth Kong
services. They do **not** pass through `basic-auth`, and they keep Kong's 60s
default rather than the 330s above.

Editing `volumes/api/kong.yml` needs a `docker compose restart kong`, not a
`kong reload`: the container's entrypoint expands the env-var placeholders into
`kong.yml` only at container start, so a reload re-applies the stale rendered file.

## Hostname history

The bloom stack originally lived behind `bloom-dev.salk.edu` and its `staging.bloom-dev.salk.edu` sibling — that was the "in-progress" public hostname while the V2 stack matured. The permanent prod hostname is `bloom.salk.edu` (no `-dev`); the migration shipped as a single-deploy cutover (scanner clients were reconfigured beforehand) rather than a multi-phase dual-serve. The legacy `bloom-dev.salk.edu` family is no longer served.
