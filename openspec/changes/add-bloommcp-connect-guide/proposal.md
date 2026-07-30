## Why

[#553](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/553) — there is no
researcher-facing "how do I connect my AI client to bloommcp" document today. `bloommcp/README.md`
and `_WIKI/BLOOMMCP/README.md` are dev/contributor-oriented (env vars, repo layout, how to add a
tool); `bloommcp/docs/storage-backends.md` covers only the local-dev opt-in storage backend. None
of them tell a bench scientist how to actually point Claude at the hosted server.

The issue scopes two parts with different readiness:

- **Part 1 (Claude Code)** has no open dependency. Claude Code connects to MCP servers
  device-side, from the researcher's own machine — confirmed against Anthropic's own docs in
  [#522](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/522) (still `OPEN`,
  0 comments, re-checked 2026-07-30). That means it can reach bloommcp today, on Salk wifi/VPN,
  with no network exception needed.
- **Part 2 (Claude Desktop/Enterprise)** is blocked on #522's still-open subgroup conversation:
  Desktop/Enterprise custom connectors run cloud-side (from Anthropic's infrastructure), which
  would need Salk IT to allowlist Anthropic's published outbound range
  (`160.79.104.0/21`) — not yet requested or decided — and #522 hasn't yet settled which auth mode
  (OIDC vs. `static_headers`) this path would use.

**What's already true today (confirmed directly against the current codebase on this branch, not
assumed from the issue body):**

- **Endpoints and the staging-port trap.** `caddy/Caddyfile:90-97` routes `/bloommcp/*` to the
  `bloommcp` container on 8811, commented `Salk-network-internal only`, with no additional auth at
  the Caddy layer (`bloommcp` enforces its own bearer check). Staging and production share the
  same host and the same `docker-compose.prod.yml`/Caddyfile, differing only by
  `CADDY_HTTPS_LISTEN_PORT` — confirmed `8443` in
  [.env.staging.defaults:24](../../../.env.staging.defaults#L24) vs. `443` in
  [.env.prod.defaults:34](../../../.env.prod.defaults#L34). Hitting `staging.bloom.salk.edu` on
  the default port 443 lands on prod's Caddy instead (prod's wildcard TLS cert covers the
  hostname, so the connection succeeds, but prod's Caddy has no route for that host and silently
  returns a blank `200`) — a real trap worth calling out explicitly so nobody loses time to it.
- **The access-scope claim, verified against the actual RLS/grant migrations, not just cited.**
  `bloom_agent` (the role behind the single shared `BLOOMMCP_API_KEY`) receives
  `GRANT SELECT ON ALL TABLES IN SCHEMA public TO bloom_agent` plus a matching
  `ALTER DEFAULT PRIVILEGES ... GRANT SELECT ... TO bloom_agent`
  ([20260414002000_security_groups.sql:50,54](../../../supabase/migrations/20260414002000_security_groups.sql#L50)),
  so newly-created tables are covered automatically. Every `agent_read_<table>` RLS policy across
  [20260414002000_security_groups.sql](../../../supabase/migrations/20260414002000_security_groups.sql)
  and
  [20260506000001_bloom_role_rls_policies.sql](../../../supabase/migrations/20260506000001_bloom_role_rls_policies.sql)
  is `USING (true)`, with the only exceptions filtering soft-deletes (`deleted_at IS NULL`), never
  ownership. `bloom_agent`'s only write grant is
  `GRANT INSERT, UPDATE ON storage.objects TO bloom_agent`, and both corresponding policies are
  scoped `WITH CHECK (bucket_id = 'bloommcp-data')`
  ([20260605000000_create_bloommcp_data_bucket.sql:42-61](../../../supabase/migrations/20260605000000_create_bloommcp_data_bucket.sql#L42)) —
  confirming "read everything, write only to the `bloommcp-data` bucket" exactly.
  [#406](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/406)'s caller-identity
  verification (this repo's own prior change, `bloommcp-caller-identity`) is audit/attribution
  only and does not change any of this — every DB/Storage call still runs as `bloom_agent`
  regardless of the resolved caller identity.
- **The shared key has no self-service distribution path today.** `BLOOMMCP_API_KEY` is a
  randomly-generated secret minted once by `scripts/generate-secrets.sh` and pushed to GitHub
  Secrets (`STAGING_BLOOMMCP_API_KEY`/`PROD_BLOOMMCP_API_KEY`) by
  `scripts/setup-env-secrets.sh` — there is no lookup doc or self-service flow for a researcher to
  obtain it. Whoever writes the guide needs to pin down and name the actual "ask X" contact/process
  as part of writing it (see Non-Goals and tasks.md — this proposal does not invent that answer).

## What Changes

- **New file `bloommcp/docs/connecting-claude-code.md`** — a complete, start-to-finish researcher
  guide for connecting Claude Code to bloommcp, alongside the existing dev-oriented
  `bloommcp/docs/storage-backends.md`. Required content, in this order:
  1. **The access-scope warning, prominently, before any connection step** — plain-language
     statement that the shared token grants read access to the entire scientific database (every
     `public.*` table, current and future), not just the researcher's own data, and that write
     access is confined to the `bloommcp-data` bucket only. Cites the migrations above so the
     claim is checkable, not asserted.
  2. **Prerequisite:** Salk wifi or VPN.
  3. **The connect command** for both environments, including the `:8443` staging port and an
     explicit callout of the same-host/wrong-port trap described above.
  4. **Where to get `<token>`** — filled in with the real answer determined while writing this
     guide (see tasks.md), not left as a placeholder.
  5. **A pointer to `storage-backends.md`**'s `BLOOM_STORAGE_BACKEND=local` fully-local/offline
     mode, for anyone who'd rather not touch the shared server at all.
  6. **A "Claude Desktop / Enterprise" section explicitly marked not-yet-written**, with a forward
     pointer to #522, rather than attempted early.
- **No code changes.** This is a documentation-only change; no bloommcp behavior, spec, or test
  surface is touched.
- **One new OpenSpec capability, `bloommcp-connect-guide`**, so the guide's required content
  (access-scope warning placement, both-environments coverage, the Part 2 deferral) is a checkable
  spec requirement rather than an informal writing task, matching this repo's convention of
  spec-backing every issue-driven change.

## Impact

- **Affected specs:**
  - `bloommcp-connect-guide` (new capability) — ADDED requirements for the guide's existence,
    required content and ordering (access-scope warning before connection steps), both-environment
    coverage including the staging-port trap, and Part 2's explicit not-yet-written/forward-pointer
    treatment.
  - No other capability is touched — no code changes, so no delta to `bloommcp-packaging`,
    `bloommcp-storage-backend`, `bloommcp-caller-identity`, etc.
- **Affected files (to be written during implementation, not this proposal):**
  - New: `bloommcp/docs/connecting-claude-code.md`.
  - Possibly modified (cross-links only, not content changes): `bloommcp/README.md`,
    `_WIKI/BLOOMMCP/README.md`, `bloommcp/docs/storage-backends.md` — a one-line pointer to the new
    guide, if not already discoverable from those entry points.
- **Not affected:** no tests, no CI, no runtime code, no migrations, no compose files.

## Scope / Non-Goals

- **Part 2 (Claude Desktop/Enterprise) is not written by this change.** #522's subgroup
  conversation (auth mode, network allowlisting) is unresolved — confirmed still `OPEN` with zero
  comments as of 2026-07-30. The guide gets an explicit "not yet written, see #522" section instead
  of a rewrite-prone early attempt.
- **This proposal does not determine who to ask for `BLOOMMCP_API_KEY` or how it's distributed.**
  That's a real-world process fact (not derivable from code) that whoever implements this change
  must pin down themselves, per tasks.md.
- **This proposal does not perform the authenticated end-to-end verification** (a real
  `tools/list` round-trip against staging and prod) — that requires Salk network access and a real
  deployed `BLOOMMCP_API_KEY`, neither available while drafting this proposal. It is the acceptance
  gate for implementation, tracked as an explicit task, not assumed complete.
- **No changes to auth, RLS, or access scoping.** The guide documents today's access model; it
  does not propose narrowing it (that's #388/#522 territory, out of scope here).
