## Why

`bloommcp`'s data-access roadmap (`bloommcp/docs/data-access-roadmap.md`, Tier 1;
[bloom#546](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/546)) needs a bulk
trait-fetch surface before `SupabaseReader`'s raw tier can be rewritten to query Bloom's Postgres
directly (Tier 2). Today's read surface (`cyl_scan_traits_source`/`cyl_scan_traits_latest` +
`get_scan_traits(experiment_id_, trait_name_, source_id_, run_id_)`, shipped
`20260701000000_cyl_trait_read_source_aware.sql`) is **per-trait**: loading one experiment means one
`get_scan_traits` call per distinct trait name. The bloom#483 cylinder fixture is 129 samples × 880 raw
trait columns (123 × 649 post-QC) — 649–880 round trips to load a single experiment, not viable for a
wide-pivot read. This change adds the missing bulk fetch, reusing the existing latest/source/run
selection logic rather than re-deriving it.

## What Changes

- **New `get_experiment_traits(experiment_id_ BIGINT, source_id_ BIGINT DEFAULT NULL,
run_id_ TEXT DEFAULT NULL)`** — a bulk sibling of `get_scan_traits` that drops the `trait_name_` filter,
  returning **every** trait row for the experiment in one round trip (long-format: one row per
  scan/trait). Built on the same `cyl_scan_traits_source` substrate and reusing its exact
  latest/pin-source/pin-run three-way disjunction and mutual-exclusion guard — the "latest = max(source_id)"
  rule is not re-derived.
- **New `list_experiment_trait_sources(experiment_id_ BIGINT)`** — lists the distinct
  `(source_id, source_name, pipeline_run_id)` tuples contributing scan-trait rows to the experiment, so a
  caller can enumerate available sources/runs before deciding whether to pin one.
- **Spot-check (not a grant migration): confirm `bloom_agent` grants cover the full join chain**
  (`cyl_scans`, `cyl_waves`, `cyl_plants`, `accessions`, `species`, `cyl_experiments`), not just the
  read-surface objects — `get_scan_traits`/`get_experiment_traits` are `SECURITY INVOKER`, so they don't
  inherit a definer's privileges. Already broadly covered by `20260414002000_security_groups.sql`'s
  schema-wide `GRANT SELECT ... TO bloom_agent` + matching RLS; a regression test pins this rather than
  changing any grant.
- **Forward-only migration** + companion manual rollback under `supabase/rollbacks/`; regenerate the five
  tracked `database.types.ts` copies (mirroring the source-aware read-path precedent).

**Open design decision, gated on review (see `design.md` Decision D1):** the RPC shape — a bulk
long-format RPC (this proposal's recommendation) vs. a PostgREST embedded-join query — is called out in
bloom#546 as needing @blm3886 (Benfica)'s review before it ships, the same gate as the shipped
`cyl_trait_read_source_aware` migration. This proposal is written to be reviewable as-is (recommendation
+ rejected alternative below), not pre-decided.

## Impact

- Affected specs: `cyl-trait-read` (existing capability — additive; the four requirements shipped by
  `add-cyl-trait-read-source-aware` are unchanged).
- Affected code:
  - `supabase/migrations/` (new migration), `supabase/rollbacks/` (companion rollback).
  - Five tracked `database.types.ts` copies: `web/lib/database.types.ts`,
    `web/types/database.types.ts`, `packages/bloom-js/src/types/database.types.ts`,
    `packages/bloom-fs/src/types/database.types.ts`,
    `packages/bloom-nextjs-auth/src/lib/database.types.ts`.
  - No `bloommcp` code changes — Tier 2 (rewriting `SupabaseReader`'s raw tier to call this RPC) is a
    separate, not-yet-filed tracking issue per the roadmap's just-in-time policy.
- Backward compatible: purely additive — no existing view, function, or table is modified or dropped.
  `get_scan_traits` is untouched.
- **Non-goals (explicitly out of scope, per bloom#546):** Tier 2 (rewriting `SupabaseReader`) and Tier 3
  (LLM-facing surface + cleanup) — filed as their own issues when reached. No RLS or write-grant change;
  auth model stays the shared `bloom_agent` role (re-confirmed with Benfica 2026-07-23, per the roadmap
  doc).
