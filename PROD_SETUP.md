# Bloom Production Setup

This guide covers the **one-time host setup** needed before the first deploy.
After that, every deploy runs automatically through
[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) when a PR is
merged to `main`. There is no supported manual `docker compose up` path on
production — the workflow is the only sanctioned entrypoint.

For how the stack actually works (Caddy, TLS, DNS, container topology), see
the wiki pages linked at the bottom of this doc.

---

## What needs to be on the host before the first deploy

1. **Docker + Docker Compose v2.** The deploy workflow refuses to run if
   `docker compose version` reports a major version below 2.

2. **A self-hosted GitHub Actions runner**, registered to this repo,
   running as the `bloom-deploy` system user. The deploy workflow checks
   out into the runner's workspace at
   `/home/bloom-deploy/actions-runner/_work/bloom/bloom` and runs
   `docker compose` from there.

3. **MinIO storage directory:**
   ```bash
   sudo mkdir -p /var/lib/bloom/minio
   sudo chown -R bloom-deploy:bloom-deploy /var/lib/bloom/minio
   sudo chmod 755 /var/lib/bloom/minio
   ```

4. **Salk DNS — one CNAME record**, published by Salk IT:
   ```
   _acme-challenge.bloom-dev.salk.edu  CNAME  _acme-challenge.bloom-acme.talmolab.org
   ```
   Without this, Caddy cannot complete the DNS-01 ACME challenge and no
   TLS cert is issued. See the Caddy wiki page for the full delegation
   story.

5. **GitHub Secrets** in the repo settings under the `Production`
   environment. The deploy workflow refuses to run without all of these:

   | Secret | Purpose |
   |---|---|
   | `PROD_POSTGRES_PASSWORD` | Postgres superuser password |
   | `PROD_JWT_SECRET` | Signs all Supabase JWTs |
   | `PROD_ANON_KEY` | Anonymous client key |
   | `PROD_SERVICE_ROLE_KEY` | Server-side service role key |
   | `PROD_DB_ENC_KEY` | Database column encryption key |
   | `PROD_MINIO_PASSWORD` | MinIO root password |
   | `PROD_VAULT_ENC_KEY` | Supabase vault encryption key |
   | `PROD_SUPAVISOR_ENC_KEY` | Supavisor pooler encryption key |
   | `PROD_SECRET_KEY_BASE` | Realtime / phoenix secret base |
   | `PROD_DASHBOARD_PASSWORD` | Supabase Studio dashboard password |
   | `PROD_BLOOMMCP_API_KEY` | bloommcp tool-auth key |
   | `PROD_OPENAI_API_KEY` | OpenAI for langchain-agent |
   | `PROD_LANGCHAIN_API_KEY` | LangSmith tracing key |
   | `PROD_BLOOM_AGENT_KEY` | Service role for the agent's Supabase access |
   | `PROD_CLOUDFLARE_API_TOKEN` | Caddy DNS-01 ACME token (zone: `bloom-acme.talmolab.org`) |

   Non-sensitive values (domains, ports, URLs, flags) live in
   [`.env.prod.defaults`](.env.prod.defaults), which is committed and read
   by the workflow at deploy time.

---

## Deploying

Merge a PR to `main`. The `deploy` workflow will:

1. SSH to the prod host via the self-hosted runner
2. Concatenate `.env.prod.defaults` + the GitHub Secrets into `.env.prod`
3. Validate the assembled file has every required key
4. Run `docker compose -f docker-compose.prod.yml --env-file .env.prod up -d`
5. Wait for Caddy to obtain / renew its TLS cert
6. Run `supabase db push` for any new migrations
7. Print `docker compose ps` to the workflow log

If any step fails, the workflow surfaces logs and stops — the stack is
left in whatever state the failed step produced. Investigate from the
workflow logs first, then SSH to the host if needed.

---

## Verifying the stack

After a deploy, on the prod host:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Status}}'
```

You should see fifteen containers, all `(healthy)` except `bloom_v2_prod-rest-1`
which is intentionally bare (see issue #161 for the reason). Container
names use the `bloom_v2_prod-<service>-1` prefix.

For deeper troubleshooting, the wiki has runbook-style pages.

---

## Architecture and runbooks (wiki)

The wiki is the authoritative source for how each piece of the stack is
designed, why, and how to operate it. Don't duplicate that content here.

- **Caddy + TLS + DNS-01 ACME** — `_WIKI/CADDY/README.md`
- **Deploy workflow internals** — `.github/workflows/deploy.yml`
- **Env layout (defaults + secrets)** — `.env.prod.defaults` header comment

---

## Loading initial / test data (development only)

The `make load-test-data` and `make upload-images` targets are intended
for local development environments. Do not run them against production.
