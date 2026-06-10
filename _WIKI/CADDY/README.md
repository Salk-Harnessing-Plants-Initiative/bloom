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
_acme-challenge.bloom.salk.edu      CNAME  _acme-challenge.bloom-acme.talmolab.org
_acme-challenge.bloom-dev.salk.edu  CNAME  _acme-challenge.bloom-acme.talmolab.org
```

This says "any lookup for `_acme-challenge.bloom.salk.edu` (or the legacy `_acme-challenge.bloom-dev.salk.edu`) should go look at `_acme-challenge.bloom-acme.talmolab.org` instead." Both CNAMEs delegate to the same Cloudflare zone we control. The legacy `bloom-dev` CNAME stays in place during Phase 1 dual-serve so the legacy hostname's TLS cert keeps renewing — see "Hostname history" below.

### Why a custom Dockerfile

Caddy is a Go binary — its modules are statically linked at build time, not loaded at runtime.

The default Caddy Docker image only knows about its built-in modules — it doesn't ship with any external DNS providers.

To use Cloudflare for TLS, we have to build our own Caddy binary with the Cloudflare

module compiled in — that's what `caddy/Dockerfile` does.

## Site addresses

The Caddyfile site block opens with `{$CADDY_SITE_ADDRESSES}`, which expands per-environment to a comma-separated, scheme-prefixed list:

| Env     | `CADDY_SITE_ADDRESSES`                                     | What it issues                                         |
| ------- | ------------------------------------------------------------ | ------------------------------------------------------ |
| prod    | `https://bloom.salk.edu, https://*.bloom.salk.edu, https://bloom-dev.salk.edu, https://*.bloom-dev.salk.edu` | One cert with 4 SANs (new apex+wildcard + legacy apex+wildcard during Phase 1 dual-serve) |
| staging | `https://*.bloom.salk.edu, https://*.bloom-dev.salk.edu` | One cert with 2 SANs (both wildcards covering all staging subdomains in both families) |
| CI      | `http://localhost`                                         | No cert —`http://` scheme disables ACME entirely    |

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
| Salk DNS CNAMEs                  | Two parallel records — `_acme-challenge.bloom.salk.edu CNAME _acme-challenge.bloom-acme.talmolab.org` (new) and `_acme-challenge.bloom-dev.salk.edu CNAME _acme-challenge.bloom-acme.talmolab.org` (legacy, retained for Phase 1 dual-serve). Each covers both prod and staging Caddy containers because wildcard ACME challenges all land at the parent name. |

The token is consumed at runtime by the `caddy-dns/cloudflare` plugin via the `CLOUDFLARE_API_TOKEN` env var, injected from the per-environment GitHub secret in the deploy workflow's heredoc.

## Hostnames

| Env     | Main (`DOMAIN_MAIN`)         | Studio (`DOMAIN_STUDIO`)            | MinIO (`DOMAIN_MINIO`)             |
| ------- | ------------------------------ | ------------------------------------- | ------------------------------------ |
| prod    | `bloom.salk.edu`         | `studio.bloom.salk.edu`         | `minio.bloom.salk.edu`         |
| staging | `staging.bloom.salk.edu` | `staging-studio.bloom.salk.edu` | `staging-minio.bloom.salk.edu` |

All staging hostnames sit under `bloom.salk.edu` so the wildcard `*.bloom.salk.edu` covers them. The legacy `staging.bloom-dev.salk.edu` family still resolves and serves the same content during Phase 1 dual-serve — see "Hostname history" below.

> **DNS note:** browser access to `staging.bloom.salk.edu` requires the name to resolve. Salk's wildcard A record for `*.bloom.salk.edu` covers it from inside the Salk network (or on Salk VPN). From outside, you'll need a temporary `/etc/hosts` entry pointing the hostname at bloom-dev's IP, or an explicit Salk DNS record.

## Hostname history

The bloom stack originally lived behind `bloom-dev.salk.edu` and its `staging.bloom-dev.salk.edu` sibling — that was the "in-progress" public hostname while the V2 stack matured. The permanent prod hostname is `bloom.salk.edu` (no `-dev`), and the migration shipped via three phases:

| Phase | What | When |
|---|---|---|
| Phase 1 | Dual-serve: Caddy serves both `bloom.salk.edu` and `bloom-dev.salk.edu` URL families identically. Both DNS A records resolve to the same bloom-dev server. Automated clients (cyl-scanners, graviscan) keep working at the legacy URL without reconfiguration. | Cutover deploy |
| Phase 2 | Caddy switches the legacy hostnames from "serve identically" to a 308 Permanent Redirect to the new hostnames. Scanner clients must have been reconfigured (or use HTTP libraries that follow 308 on POST). | ~30 days post-cutover, separate PR |
| Phase 3 | Legacy hostnames dropped from `CADDY_SITE_ADDRESSES` entirely. Caddy stops issuing certs for them. DNS records on the Salk side can stay (inert) or be removed via a future IT ticket. | ~90 days post-cutover, separate PR |

The previous `staging-bloom-dev.salk.edu` (sibling of `bloom-dev.salk.edu`, not under it) was renamed by PR #254 to `staging.bloom-dev.salk.edu` specifically to bring it under the wildcard. The migration to `bloom.salk.edu` preserved that hyphenated subdomain shape (`staging-studio.bloom.salk.edu`, etc.).
