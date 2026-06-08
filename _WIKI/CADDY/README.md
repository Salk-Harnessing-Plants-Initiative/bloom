# Caddy

This page covers how Caddy is set up. Caddy is the web server that sits in front of bloom. It takes every incoming HTTPS request and forwards it to the right backend container. This page covers how Caddy is set up, how it gets real HTTPS certificates, how those certificates survive a redeploy, and what happens when they need to be renewed. Update this page whenever you change the Caddyfile, the Dockerfile, how certs are issued, or the per-environment hostnames.

## Stack shape

One `caddy` container per environment(staging and prod), built from a project-owned Dockerfile rather than a stock image — see [caddy/Dockerfile](../../caddy/Dockerfile) and [caddy/Caddyfile](../../caddy/Caddyfile).

| Concern                        | Where                                                                                                                                                    |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Image build                    | [caddy/Dockerfile](../../caddy/Dockerfile) — two-stage `xcaddy build` with `caddy-dns/cloudflare` linked in                                            |
| Routing + TLS config           | [caddy/Caddyfile](../../caddy/Caddyfile)                                                                                                                    |
| Container declaration          | `caddy` service in [docker-compose.prod.yml](../../docker-compose.prod.yml)                                                                               |
| Per-env site addresses + token | `CADDY_SITE_ADDRESSES` + `CLOUDFLARE_API_TOKEN` in [.env.prod.defaults](../../.env.prod.defaults) and [.env.staging.defaults](../../.env.staging.defaults) |
| Deploy-time secret injection   | `PROD_/STAGING_CLOUDFLARE_API_TOKEN` heredocs in [.github/workflows/deploy.yml](../../.github/workflows/deploy.yml)                                       |

### Why a custom Dockerfile

Caddy is a Go binary — its modules are statically linked at build time, not loaded at runtime. 

The stock `caddy:*-alpine` image ships only standard modules; adding `tls { dns cloudflare ... }` to the Caddyfile against the stock image fails at startup with `unknown DNS provider module: cloudflare`.

The custom Dockerfile uses the official `caddy:${VERSION}-builder-alpine` image to `xcaddy build` a binary with `caddy-dns/cloudflare@v0.2.4` baked in, then copies that binary into the slim `caddy:${VERSION}-alpine` runtime image.

## TLS strategy

Real Let's Encrypt certs acquired via **ACME DNS-01** with **CNAME delegation** to `bloom-acme.talmolab.org`. One wildcard SAN cert covers every bloom hostname per environment.

### Why DNS-01 instead of HTTP-01

`bloom-dev.salk.edu` sits behind Salk's firewall. Let's Encrypt cannot reach port 80 from the public internet, so HTTP-01 cannot complete. DNS-01 sidesteps the network entirely ,the proof of ownership is a TXT record under the challenged name, not an HTTP request.

### Why CNAME delegation

Caddy needs API credentials to write the TXT record. Salk IT does not delegate Salk DNS write access to application containers. Instead we own a separate Cloudflare zone (`bloom-acme.talmolab.org`) and Salk publishes one CNAME pointing the challenge name at our zone:

```text
_acme-challenge.bloom-dev.salk.edu  CNAME  _acme-challenge.bloom-acme.talmolab.org
```

When Let's Encrypt does the DNS-01 lookup at `_acme-challenge.bloom-dev.salk.edu`, it follows the CNAME and reads the TXT from `_acme-challenge.bloom-acme.talmolab.org` — which Caddy writes to via the Cloudflare API.

Both the apex (`bloom-dev.salk.edu`) and the wildcard (`*.bloom-dev.salk.edu`) ACME challenges land at the same name (`_acme-challenge.bloom-dev.salk.edu`), so **one** Salk CNAME covers both prod and staging.

## Site addresses

The Caddyfile site block opens with `{$CADDY_SITE_ADDRESSES}`, which expands per-environment to a comma-separated, scheme-prefixed list:

| Env     | `CADDY_SITE_ADDRESSES`                                     | What it issues                                         |
| ------- | ------------------------------------------------------------ | ------------------------------------------------------ |
| prod    | `https://bloom-dev.salk.edu, https://*.bloom-dev.salk.edu` | One cert with two SANs (apex + wildcard)               |
| staging | `https://*.bloom-dev.salk.edu`                             | One wildcard cert (covers all three staging hostnames) |
| CI      | `http://localhost`                                         | No cert —`http://` scheme disables ACME entirely    |

## Cert persistence across redeploys

The `caddy` service mounts two named volumes:

```yaml
volumes:
  - caddy-data:/data
  - caddy-config:/config
```

`caddy-data` is a named Docker volume. It survives `docker compose down`, `docker compose up`, and full container recreation — **only `docker volume rm` or `docker compose down -v` wipes it.** Caddy stores the issued cert, the private key, the ACME account, and the renewal state inside `/data/caddy/certificates/...`.

On every redeploy Caddy boots, reads `/data`, finds the existing `*.bloom-dev.salk.edu` cert, checks the expiry, and:

- **Cert valid + outside renewal window** → uses the cached cert. Zero Let's Encrypt traffic. The 30–90 s ACME window only happens **once**, on the very first deploy.
- **Cert within 30 days of expiry** → triggers renewal in the background. Serving continues with the old cert until the new one lands.
- **Cert missing or expired** → blocking ACME on startup (same as first deploy).

## Automatic renewal

Caddy v2 runs an internal scheduler that wakes every ~10 minutes and checks every cert's expiry. 30 days before expiry it starts attempting renewal — same DNS-01 + Cloudflare flow it used for issuance, no restart, no human action, hot-reloads the new cert into the running listeners.

### What you need to verify externally (outside Caddy)

- The Cloudflare API token doesn't get revoked between issuances. 90-day cert + 30-day renewal window = check at least quarterly.
- The Salk CNAME stays in place.
- The container stays alive — if it's stopped for >60 continuous days the renewal window closes.

For the Let's Encrypt rate-limit budget bloom operates against (and how the deploy safeguards keep a misconfigured rollout from burning through it), see [letsencrypt-rate-limits.md](./letsencrypt-rate-limits.md).

## Required secrets and DNS

These three must be in place before either environment can issue a cert:

| Requirement                      | Where it lives                                                                                                                                      |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PROD_CLOUDFLARE_API_TOKEN`    | GitHub Actions repo secret. Scope:`Zone:DNS:Edit` on the Cloudflare zone containing `bloom-acme.talmolab.org`.                                  |
| `STAGING_CLOUDFLARE_API_TOKEN` | GitHub Actions repo secret. Same scope; same value as the prod token is fine — both environments use the same Cloudflare delegation.               |
| Salk DNS CNAME                   | `_acme-challenge.bloom-dev.salk.edu  CNAME  _acme-challenge.bloom-acme.talmolab.org` — published by Salk IT, one record covers prod and staging. |

The token is consumed at runtime by the `caddy-dns/cloudflare` plugin via the `CLOUDFLARE_API_TOKEN` env var, injected from the per-environment GitHub secret in the deploy workflow's heredoc.

## Hostnames

| Env     | Main (`DOMAIN_MAIN`)         | Studio (`DOMAIN_STUDIO`)            | MinIO (`DOMAIN_MINIO`)             |
| ------- | ------------------------------ | ------------------------------------- | ------------------------------------ |
| prod    | `bloom-dev.salk.edu`         | `studio.bloom-dev.salk.edu`         | `minio.bloom-dev.salk.edu`         |
| staging | `staging.bloom-dev.salk.edu` | `staging-studio.bloom-dev.salk.edu` | `staging-minio.bloom-dev.salk.edu` |

All staging hostnames sit under `bloom-dev.salk.edu` so the wildcard `*.bloom-dev.salk.edu` covers them. The staging `DOMAIN_MAIN` was previously `staging-bloom-dev.salk.edu` (sibling of `bloom-dev`, not under it) — the dot-rename was specifically to bring it under the wildcard.
