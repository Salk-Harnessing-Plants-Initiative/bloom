## Context

This is the second re-pin of `insert_cyl_result_envelope`'s accepted `contract_version` (the first,
`a2`→`a3`, is `repin-cyl-contract-a3` / #399, closing #393). The RPC's version check is
**prefix-tolerant** only along one axis — a leading `v` (git-tag/`$id` namespace) vs. bare
(PEP 440 package namespace) — and was deliberately never a version-range/compatibility-set
mechanism. That design choice is being revisited here, not assumed to still be right by default.

## Decisions

- **Re-affirm the single-exact-pin design; do not widen to a range or compatibility set.** The
  `repin-cyl-contract-a3` design explicitly rejected a `{a2, a3}` compatibility set: it dilutes the
  per-row provenance-of-origin anchor, and no real `a2` envelope existed at the time to justify one.
  The same reasoning holds here, reinforced by new evidence rather than just precedent: every
  version bump since `a3` (`a4`, `a5`, `a6`, `a7`) has been a byte-identical `$id`-only restamp —
  verified directly against `talmolab/sleap-roots-contracts` for this change, not assumed. A range
  mechanism would exist purely to reduce future re-pin *churn*, not to solve a real acceptance
  problem — there is no version in `{a4..a7}` that carries a schema Bloom would need to accept
  differently from `a3`. If a *future* bump ever does carry a real field delta (the way `a2`→`a3`
  did), a range would be the wrong tool anyway, since the RPC would need to understand the new
  shape, not merely tolerate the version string. Litigate that trade-off if and when a real delta
  reappears — this change does not manufacture a general mechanism to pre-empt a hypothetical.
- **Confirm no schema delta before treating this as "just a version bump."** Verified by fetching
  `schema/result_envelope.schema.json` at tags `v0.1.0a5` through `v0.1.0a7` directly from
  `talmolab/sleap-roots-contracts` and diffing with the version string normalized out: identical.
  Cross-checked against upstream's `docs/CHANGELOG.md`, which independently describes each of a4–a7
  as a "bytes-only restamp — no model changes." `RunManifest` (`sleap-roots-contracts` PR #30, the
  feature that motivated the a7 bump) is a file-based producer↔producer contract explicitly
  documented as "not emitted to JSON Schema" — it does not touch `ResultEnvelope`/`Provenance` and
  needs no Bloom-side schema or DB handling.
- **Close the pre-existing RPC-vs-vendored-contract drift in the same change.** Two unrelated Bloom
  changes (#411, #407) already re-pinned the vendored contract (`contracts/schema/`, `pin.json`,
  `generated/`, README) to `a5` without the RPC's hardcoded literal following along — because those
  changes had no reason to touch the RPC. Left alone, that gap would keep widening on every future
  vendored re-pin that isn't itself an RPC change. Re-pinning both to `a7` together restores
  lockstep; it does not by itself prevent the gap from reopening (a future vendor-only re-pin can
  still drift ahead of the RPC again), which is an accepted, unchanged risk — not something this
  change introduces or is scoped to solve structurally.
- **Cutover safety guard.** Mirroring `repin-cyl-contract-a3`'s pattern: the new migration is
  prepended with a `DO` block that raises if any `cyl_trait_sources` row carries a `0.1.0a3`
  (or earlier) `contract_version`, since this migration makes the RPC permanently reject it. This is
  expected to be a no-op (the pipeline has never emitted `a7`, and the currently-deployed
  trait-extractor image still emits `a3` — see the tracking issue for that image's own pin bump,
  `talmolab/sleap-roots-pipeline#52`, which is explicitly sequenced after this migration lands) but
  fails loudly rather than silently orphaning existing rows if that premise is ever violated.

## Risks / Trade-offs

- **Repeated exact-pin churn.** Every future contract bump — even a no-op restamp — requires a new
  Bloom migration to keep the RPC's literal current, as this is the second time in ~2 months. This
  is a deliberate, previously-reviewed trade-off (see the rejected-alternatives discussion above),
  not a new risk introduced by this change.
- **A9/A10-style future bump landing without the RPC being updated first.** Same shape of risk as
  #685 itself; mitigated the same way — the trait-extractor image pin is the actual production
  trigger, and its bump is tracked and explicitly sequenced after any RPC contract-version change
  (this change's own tracking issue is `talmolab/sleap-roots-pipeline#52`).

## Migration Plan

1. Re-vendor the `a7` schema, bump `pin.json`, regenerate TS (`npm run contracts:gen`); verify
   `npm run contracts:check` + `npm run contracts:test` pass with a byte-identical types diff.
2. Add the forward migration (RPC `CREATE OR REPLACE` + cutover guard + owner/grants) and the
   rollback script restoring the strict `0.1.0a3` body.
3. Update the writeback/read-path tests to the new pinned version; add an `a3`-now-rejected case;
   update contract-migration-match version references.
4. Run the integration suite against a live Postgres; `openspec validate repin-cyl-contract-a7
   --strict`.

Rollback: apply `supabase/rollbacks/*_cyl_writeback_contract_a7_rollback.sql` to restore the strict
`0.1.0a3` RPC body; revert the `contracts/` re-pin commit to return to the vendored `a5` pin.
