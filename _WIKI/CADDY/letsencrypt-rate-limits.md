# Let's Encrypt rate limits

How Let's Encrypt's rate limits affect bloom, what we actually use, and the safeguards in place to keep a misconfigured deploy from burning through the budget. Update this page if Let's Encrypt changes their limits or if our deploy shape changes.

> Numbers on this page were verified against the official Let's Encrypt rate-limit documentation (https://letsencrypt.org/docs/rate-limits/, last updated 2025-06-12). If you bump a limit or change our deploy shape, re-verify against that page.

## The four limits that matter to bloom

Let's Encrypt publishes a few rate limits. These are the ones that actually touch our deploy:

| Limit | Number | Counts what |
| --- | --- | --- |
| **Certificates per Registered Domain** | 50 per rolling 7 days | All certs issued under `salk.edu` (the eTLD+1) — shared across every Salk subdomain anyone in the org uses LE for |
| **Duplicate Certificates** | 5 per rolling 7 days | Certs with the exact same set of identifiers (same SAN list) |
| **Failed Validations** | 5 per account per identifier per hour | Bad DNS, wrong token, ACME challenge failures — temporary cliff, clears after the hour |
| **New Orders** | 300 per account per 3 hours | New cert requests (lots of headroom; we never approach this) |
| **Consecutive Authorization Failures** | 1,152 per identifier (cumulative) | Long-running misconfiguration — if you ever pass this, issuance for that hostname is paused until manually unpaused by LE staff |

The big one is the **50 per week per registered domain**. "Registered domain" means `salk.edu`, not `bloom-dev.salk.edu` — so the budget is shared with every Salk team using Let's Encrypt for any subdomain.

## What bloom actually uses

### First deploy of an environment

| Event | Certs requested |
| --- | --- |
| Staging cold start | 1 cert covering `*.bloom-dev.salk.edu` (one SAN) |
| Prod cold start | 1 cert covering `bloom-dev.salk.edu, *.bloom-dev.salk.edu` (two SANs, different SAN list from staging — not a duplicate) |
| **Total for full rollout** | **2 / 50** weekly budget |

The two certs have different SAN lists, so they don't count against the "Duplicate Certificates" limit either.

### Subsequent redeploys

**Zero issuances.** Caddy reads the cached cert from the `caddy-data` named volume and serves it instantly. The 30–90 s ACME window only happens once per environment, on the very first deploy. See [README.md](./README.md) — "Cert persistence across redeploys".

### Renewals

One issuance per cert, every ~60 days. Renewals are exempt from the "Duplicate Certificates" counter; they still count against the 50-per-week certs-per-domain counter, but they're spread across the year so they don't bunch up.

A long-lived bloom deployment uses something like **4 cert requests per year per environment** (1 cold-start + ~3 renewals). Nowhere near any limit.

## Where you'd actually hit a limit

In practice, the realistic risks are:

- **Failed Validations: 5 per account per identifier per hour** — short-term cliff. If Caddy retries the same broken challenge 5 times in an hour, that hostname is locked for an hour. Clears automatically.
- **Consecutive Authorization Failures: 1,152 per identifier (cumulative)** — long-term cliff. If a misconfiguration is left unattended (e.g. crash-loop overnight against a stale token), the cumulative counter eventually trips and LE pauses issuance for that hostname until manually unpaused. The bigger problem of the two — manual unpause requires opening a support ticket with LE staff.

The Layer 1/2/3 safeguards in `.github/workflows/deploy.yml` exist specifically to prevent both — by failing the deploy fast and stopping the Caddy container on any failure, the cumulative counter never has time to climb:

| Layer | What it catches | Effect on rate limits |
| --- | --- | --- |
| **Layer 1** (Cloudflare token preflight, before `docker compose up`) | Wrong / revoked / empty token | Caddy never talks to LE → 0 failed validations |
| **Layer 3** (crash-loop detection, after `docker compose up`) | Caddyfile parse error, missing module, container restart loop | Stops Caddy before it can keep retrying ACME on every restart |
| **Layer 2** (cert issuer verification, polls up to 120 s) | ACME flow broken (CNAME wrong, scope wrong, plugin missing) | Stops Caddy after the first failed cert exchange → at most 1 failed validation, not 5 |

So in the worst case (something is misconfigured), the deploy fails fast, burns at most 1 failed validation, and you fix the root cause before retrying. Never approach the 5-per-hour cliff.

## The Salk-wide shared budget

The "50 certs per registered domain per week" is the only limit shared with other Salk teams. If multiple groups all renew or issue new certs in the same week, the budget can get tight. In practice this is unlikely to bite because:

- Caddy's renewal scheduler starts 30 days before expiry, so renewals are staggered automatically
- Bloom's footprint is 2 certs every ~60 days — a tiny fraction of the 50/week budget
- LE will warn you (via Caddy logs) before you hit the cliff

If you ever do hit it: wait until the oldest cert in the rolling 7-day window expires off the counter, then retry. There's no manual reset.

## What to do if a deploy fails on ACME

1. **Don't restart Caddy in a loop.** Each restart is another ACME attempt = another failed validation. Layer 3 already stops Caddy on crash-loop, but if you bypass the workflow and `docker compose up` manually, you don't get that protection.
2. **Check Caddy logs** for the actual error — wrong token / wrong CNAME / wrong scope / plugin missing. See [README.md](./README.md) — "Failure modes on cold start".
3. **Fix the root cause** before re-running the deploy. The Layer 1 token preflight catches token issues without ever touching LE, so re-running after fixing the token is safe.
4. **If you've burned 5 failed validations** for the same hostname in the same hour, wait one hour. There's no faster fix.

## Reference

Authoritative documentation for the current LE rate limits:

- https://letsencrypt.org/docs/rate-limits/

Status of bloom's account (cert issuance history, current counters):

- https://crt.sh/?q=%25.bloom-dev.salk.edu — Certificate Transparency log for `*.bloom-dev.salk.edu` (every issued cert appears here within minutes)
