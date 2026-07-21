## Context

Bloom vendors and pins the cross-language `sleap-roots-contracts` `ResultEnvelope` schema
(`contract-pinning` capability) and validates the runtime `contract_version` at the write boundary in
the `insert_cyl_result_envelope` RPC (`cyl-trait-writeback` capability). A4 wires the real traits
emitter to the live RPC for the first time, exposing a version + namespace mismatch that blocks every
write-back (#393).

Two version namespaces are in play, and conflating them is the root cause:

- **Git-tag / URL namespace** — the schema `$id` and `pin.json` carry a `v`-prefixed tag segment:
  `.../schema/v0.1.0a2/result_envelope.schema.json`.
- **PEP 440 package namespace** — `Provenance.contract_version` is stamped from the installed
  distribution version (e.g. `importlib.metadata.version("sleap-roots-contracts")`), which is **bare**
  (`0.1.0a3`); PEP 440 does not allow a `v` prefix.

The original RPC sourced its pinned constant from the tag namespace (`v0.1.0a2`) but compares it
against a value that lives in the package namespace (bare). They could never match byte-for-byte.

## Goals / Non-Goals

- Goals: unblock A4 write-back; keep Bloom's vendored contract in lockstep with the emitted version
  (`a3`); make the version check robust to the tag-vs-package `v`-prefix so this bug cannot recur.
- Non-Goals: accept a multi-version compatibility set (single pinned version, by design — keeps the
  lockstep-with-producer invariant); promote the new `predict_*` provenance fields to DB columns
  (they ride in the opaque `metadata` jsonb); touch the `bloommcp` analyze pin.

## Decisions

- **Re-pin `v0.1.0a2` → `v0.1.0a3` (full lockstep), not RPC-only.** The producer targets
  `sleap-roots-contracts==0.1.0a3`; vendoring the same version keeps `contract-pinning`'s
  "DB matches the pinned contract" honest and the README's "Currently pinned" truthful. The re-pin
  follows the documented procedure and is gated by the existing drift guards.
- **`a2`→`a3` is additive and non-breaking for Bloom.** The only schema diff (besides the `$id`
  re-stamp) is two optional, nullable `Provenance` properties: `predict_inference_config`,
  `predict_output_params`. `Provenance` maps wholesale to `cyl_trait_sources.metadata` (opaque jsonb),
  so there is **no migration for the schema shape**, no `database.types.ts` change, and no
  contract-migration-match assertion change. The generated TS gains the two optional fields — a real,
  reviewed drift-guard diff (this is a genuine contract revision, like the `a1`→`a2` `BlobRef`
  narrowing, not a `$id`-only no-op).
- **Prefix-tolerant version match.** The RPC normalizes a **single lowercase leading `v`** from both
  the incoming `provenance.contract_version` and the pinned constant before comparing:
  `regexp_replace(coalesce(value, ''), '^v', '')`. The comparison is done on the two **coalesced**
  (already non-null) normalized strings with `IS DISTINCT FROM` — this is load-bearing: it collapses
  an absent key (SQL `NULL`) to `''` *before* comparing, so absent and empty both reject. (A naive
  `raw = pinned` on the pre-coalesce values would yield `NULL = '0.1.0a3'` → `NULL` → the guard would
  not fire and the envelope would be **silently accepted** — the one way to get this wrong.) Result,
  verified on a live database: `0.1.0a3` and `v0.1.0a3` pass; `0.1.0a2`/`v0.1.0a2`/`vv0.1.0a3`/
  `V0.1.0a3`/`0.1.0a30`/absent/empty all reject. The pinned constant is written bare (`0.1.0a3`) to
  document that it is compared against the emitter's package version; the normalization keeps it
  correct regardless.
  - **Scope:** normalization covers exactly the `v`-prefix axis. Case-folding (uppercase `V`),
    whitespace-trimming, and PEP 440 build/local segments (e.g. `0.1.0a3+bloom`) are intentionally
    **excluded** — the emitter reads a clean bare PEP 440 string via `importlib.metadata.version`, so
    those forms are non-values and are correctly rejected. Any convention divergence beyond the `v`
    prefix would be a follow-up, not covered by this normalization.
  - Alternative considered — strict bare exact-match (just flip `v0.1.0a2`→`0.1.0a3`): rejected
    because it leaves the tag-vs-package ambiguity live; a producer that ever stamped `v0.1.0a3` (or a
    reviewer re-deriving the constant from the `$id`) would reintroduce the bug.
  - Alternative considered — compatibility set `{a2, a3}`: rejected; no real `a2` envelopes exist and
    a set dilutes the per-row provenance-of-origin anchor.
- **Deliver the RPC change as a new forward migration.** `20260630180000_add_cyl_writeback_rpc.sql`
  is deployed to staging and archived; this repo is forward-only. A new migration
  `CREATE OR REPLACE`s `public.insert_cyl_result_envelope(jsonb)` with the updated version block and
  **re-asserts** `OWNER TO postgres` and the `REVOKE/GRANT` block so it is self-contained and
  idempotent regardless of prior catalog state. A companion `supabase/rollbacks/*` restores the strict
  `v0.1.0a2` body.

## Risks / Trade-offs

- **Rejecting `a2` is a hard cutover.** Acceptable: the write path is not live end-to-end, so no
  stored/in-flight `a2` envelopes exist; the emitter is already on `a3`.
- **CREATE OR REPLACE preserving owner/ACL.** `CREATE OR REPLACE FUNCTION` keeps the existing owner
  and grants (verified on a live database), so re-asserting `OWNER TO postgres` + the `REVOKE/GRANT`
  block is redundant-but-safe. The real write gate is **RLS + the `postgres`/`BYPASSRLS` owner**, not
  the function `EXECUTE` grant — the archived migration already notes "RLS, not the standing table
  GRANT, is the write gate." So the re-assertion is not the security anchor; its only risk is
  **transcription drift**. The re-asserted block MUST be byte-identical to the deployed one
  (`REVOKE EXECUTE ... FROM PUBLIC; GRANT EXECUTE ... TO bloom_writer, service_role, bloom_admin`).
  Because the RPC is `SECURITY DEFINER` owned by a `BYPASSRLS` role, adding a role to that GRANT (or
  dropping the `REVOKE`) would widen who can bypass RLS to write all three trait tables — so the new
  migration reproduces the grant list verbatim and does **not** introduce any new posture (e.g. it
  does not add `REVOKE ... FROM anon, authenticated`). `test_execute_grants_*` is re-run post-migration
  to prove the ACL is unchanged.
- **Codegen surfaces the new fields (unlike `BlobRef`).** `Provenance` is a plain `object` with
  `properties`, so `json-schema-to-typescript` renders the two new optional fields — the drift guard
  diff is expected; verify it is exactly those two additions and the `$id`-driven header.

## Migration Plan

1. Re-vendor the `a3` schema, bump `pin.json`, regenerate TS (`npm run contracts:gen`), verify
   `npm run contracts:check` + `npm run contracts:test` pass (types diff = the two new optional fields).
2. Add the forward migration (RPC `CREATE OR REPLACE` + owner/grants) and the rollback script.
3. Update the writeback/read-path tests to the new pinned version; add prefix-tolerance + a2-rejection
   cases; update contract-migration-match version references.
4. Run the integration suite against the compose stack; `openspec validate repin-cyl-contract-a3 --strict`.

Rollback: apply `supabase/rollbacks/*_cyl_writeback_contract_a3_rollback.sql` to restore the strict
`v0.1.0a2` RPC body; revert the `contracts/` re-pin commit to return to the `v0.1.0a2` vendored pin.

## Open Questions

- The bare-vs-`v` `contract_version` convention is tracked in a companion `talmolab/sleap-roots-contracts`
  issue (link its `#N` here and in the `contracts/README.md` re-pin note; file it if it does not yet
  exist). Not blocking: the prefix-tolerant match makes Bloom correct across the **`v`-prefix axis**
  under either resolution. It does **not** silently absorb a broader convention change (a normalized
  separator like `0.1.0.a3`, or a build/local segment like `0.1.0a3+bloom`) — such a divergence would
  require a follow-up, and is an explicit assumption of this design rather than an unconditional
  guarantee.
- External premise to confirm before merge: that published `sleap-roots-contracts==0.1.0a3` exists
  (git tag `v0.1.0a3` confirmed; its schema fetched + diffed) **and** that the `talmolab/sleap-roots`
  emitter actually resolves and stamps `0.1.0a3`. #393 asserts this; record the release reference in
  the proposal.
