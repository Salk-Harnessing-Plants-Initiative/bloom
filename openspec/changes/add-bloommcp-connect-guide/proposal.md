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
- **The access-scope claim, verified against the actual RLS/grant migrations, not just cited —
  and corrected once by a 5-agent review of an earlier draft of this proposal (see Review History
  below).** `bloom_agent` (the role behind the single shared `BLOOMMCP_API_KEY`) receives
  `GRANT SELECT ON ALL TABLES IN SCHEMA public TO bloom_agent` plus a matching
  `ALTER DEFAULT PRIVILEGES ... GRANT SELECT ... TO bloom_agent`
  ([20260414002000_security_groups.sql:50,54](../../../supabase/migrations/20260414002000_security_groups.sql#L50)),
  so newly-created tables are covered automatically. Every `agent_read_<table>` RLS policy across
  [20260414002000_security_groups.sql](../../../supabase/migrations/20260414002000_security_groups.sql)
  and
  [20260506000001_bloom_role_rls_policies.sql](../../../supabase/migrations/20260506000001_bloom_role_rls_policies.sql)
  is `USING (true)`, with the only exceptions filtering soft-deletes (`deleted_at IS NULL`), never
  ownership. **This grant is not absolute, though:** RLS is enforced per-table, and a table whose
  only policy targets a different role — not `bloom_agent` and not a role `bloom_agent` inherits —
  returns zero rows to `bloom_agent` regardless of the schema-wide `GRANT SELECT`. Confirmed one
  concrete case: `gene_patents` (patent status, `govt_id` —
  [20240117203535_create_gene_patents_table.sql](../../../supabase/migrations/20240117203535_create_gene_patents_table.sql))
  has RLS enabled with its only policy `FOR SELECT TO authenticated`; `bloom_agent` is never
  granted membership in `authenticated` (confirmed: the only `GRANT authenticated TO <role>` in the
  whole migration history targets `bloom_writer`, not `bloom_agent` —
  [20260622180000_embedtree_writer_rls_and_species_fk.sql:23](../../../supabase/migrations/20260622180000_embedtree_writer_rls_and_species_fk.sql#L23)),
  so `bloom_agent` gets **no rows** from `gene_patents` today despite the blanket grant. This means
  the guide's disclosure cannot honestly claim unconditional universal read access — it must be
  phrased as "virtually every table by default, with RLS able to carve out exceptions like this
  one," not as an absolute. `bloom_agent`'s write grants are
  `GRANT INSERT, UPDATE ON storage.objects TO bloom_agent`
  ([20260605000000_create_bloommcp_data_bucket.sql:42-61](../../../supabase/migrations/20260605000000_create_bloommcp_data_bucket.sql#L42))
  **plus** a narrower `GRANT DELETE ON storage.objects TO bloom_agent`, scoped
  `USING (bucket_id = 'bloommcp-data' AND name ~ '^bloommcp_output/')`
  ([20260723000000_grant_bloommcp_agent_output_delete.sql:40](../../../supabase/migrations/20260723000000_grant_bloommcp_agent_output_delete.sql#L40))
  — an earlier draft of this proposal cited only INSERT/UPDATE and missed the DELETE grant. All
  three verbs stay confined to the single `bloommcp-data` bucket (DELETE further confined to its
  `bloommcp_output/` prefix — `bloommcp_input/` source CSVs are never agent-deletable), so "writes
  confined to the `bloommcp-data` bucket" survives, but "write access" in the guide must say
  insert/update/delete, not just the first two.
  [#406](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/406)'s caller-identity
  verification (this repo's own prior change, `bloommcp-caller-identity`, still open as PR #563 at
  time of writing — not yet merged to `staging`) is audit/attribution only and does not change any
  of the above **today**. It will, however, the moment PR #563 merges:
  `20260730000000_create_bloommcp_usage.sql`, part of that unmerged branch, grants `bloom_agent`
  `INSERT, UPDATE` on a new `public.bloommcp_usage` table — a `public.*` table write, outside
  Storage entirely, unlike every other write grant above. This proposal's own review process
  briefly mis-flagged this migration as already-live because a concurrent session's checkout put
  it on disk during review — confirmed via `git cat-file -e origin/staging:...` that it is **not**
  on `origin/staging` as of this writing. It is a real, near-term staleness risk once #563 merges,
  tracked as tasks.md 4.1 rather than silently discovered later.
- **The shared key has no self-service distribution path today.** `BLOOMMCP_API_KEY` is a
  randomly-generated secret minted once by `scripts/generate-secrets.sh` and pushed to GitHub
  Secrets (`STAGING_BLOOMMCP_API_KEY`/`PROD_BLOOMMCP_API_KEY`) by
  `scripts/setup-env-secrets.sh` — there is no lookup doc or self-service flow for a researcher to
  obtain it. **Decision (2026-07-30, with the issue owner):** rather than block this change on
  naming a real contact, the guide ships with an explicit, unmissable placeholder in that one spot
  (e.g. `<TODO: name the BLOOMMCP_API_KEY contact/process>`) — visibly a placeholder, not a
  plausible-looking invented answer — so the guide is otherwise complete and the one open fact is
  impossible to miss or mistake for real information.

### Review History

An earlier draft of this proposal was reviewed by a 5-agent adversarial pass
(spec quality, code/architecture fact-checking, GitHub issue alignment, TDD/verification strategy,
and scientific-rigor/data-integrity). Findings folded into this revision: the DELETE-grant and
`gene_patents` gaps above, tightened acceptance criteria on the live round-trip task (tasks.md 5),
a falsifiability bar for the token placeholder (tasks.md 1.1), a new spec requirement covering the
live-verification status itself (so it's checkable from spec.md, not only tasks.md prose), and a
named risk in design.md about this guide doubling as a roadmap for anyone holding the shared key.
Per an explicit decision with the issue owner, two acceptance items are deliberately **not**
resolved by this change and are called out plainly rather than silently attempted: the real
token-distribution contact (placeholder instead, see above) and the live authenticated round-trip
against a real deployed key (ships unverified — see Scope/Non-Goals and tasks.md 5).

## What Changes

- **New file `bloommcp/docs/connecting-claude-code.md`** — a complete, start-to-finish researcher
  guide for connecting Claude Code to bloommcp, alongside the existing dev-oriented
  `bloommcp/docs/storage-backends.md`. Required content, in this order:
  1. **The access-scope warning, prominently, before any connection step** — plain-language
     statement that the shared token grants read access to virtually the entire scientific
     database by default (every `public.*` table, current and future), not just the researcher's
     own data, **with an explicit note that RLS can and does carve out exceptions** (named example:
     `gene_patents` is not actually readable by `bloom_agent` despite the blanket grant) rather than
     an unconditional "everything" claim — and that write access is confined to the `bloommcp-data`
     bucket only, covering insert/update/delete. Cites the migrations above so the claim is
     checkable, not asserted.
  2. **Prerequisite:** Salk wifi or VPN.
  3. **The connect command** for both environments, including the `:8443` staging port and an
     explicit callout of the same-host/wrong-port trap described above.
  4. **Where to get `<token>`** — an explicit, visibly-marked placeholder (see Why, above), not a
     vague-but-plausible invented answer.
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
  - Modified: `_WIKI/BLOOMMCP/README.md` — its existing "Supabase data access" section currently
    states `bloom_agent` "can read every `public.*` table," which this proposal's own review found
    is not quite accurate (see `gene_patents` above); needs a correction, not only a cross-link.
  - Possibly modified (cross-link only, if not already discoverable): `bloommcp/README.md`.
- **Not affected:** no tests, no CI, no runtime code, no migrations, no compose files.

## Scope / Non-Goals

- **Part 2 (Claude Desktop/Enterprise) is not written by this change.** #522's subgroup
  conversation (auth mode, network allowlisting) is unresolved — confirmed still `OPEN` with zero
  comments as of 2026-07-30. The guide gets an explicit "not yet written, see #522" section instead
  of a rewrite-prone early attempt.
- **This proposal does not determine who to ask for `BLOOMMCP_API_KEY` or how it's distributed.**
  That's a real-world process fact (not derivable from code), and — per an explicit 2026-07-30
  decision with the issue owner — this change ships with a visibly-marked placeholder in that one
  spot instead of blocking on it. Filling it in is left as unfinished follow-up work, not silently
  implied to be done.
- **This proposal does not perform the authenticated end-to-end verification** (a real
  `tools/list` round-trip against staging and prod) — that requires Salk network access and a real
  deployed `BLOOMMCP_API_KEY`, neither available in this environment. Per an explicit 2026-07-30
  decision with the issue owner, this change ships **without** that check: the PR opened for this
  change states plainly that this specific acceptance criterion from #553 is unmet, rather than
  silently passing over it. This is a deliberate, disclosed gap, not an oversight.
- **No changes to auth, RLS, or access scoping.** The guide documents today's access model; it
  does not propose narrowing it (that's #388/#522 territory, out of scope here).
