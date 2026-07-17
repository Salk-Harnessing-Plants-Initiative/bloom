## Why

The A3-traits `ResultEnvelope` emitter (`talmolab/sleap-roots`) stamps
`Provenance.contract_version = "0.1.0a3"` — **bare**, read from the installed
`sleap-roots-contracts==0.1.0a3` distribution version that both predict and traits pin. The
deployed write-back RPC `insert_cyl_result_envelope` pins `v0.1.0a2` and compares by strict
equality, so it rejects every real envelope the pipeline emits. This is the **hard blocker for
A4 end-to-end write-back** (issue #393): traits can emit valid envelopes, but nothing lands in
`cyl_result` until the RPC accepts the `0.1.0a3` byte.

The mismatch is two-fold, and one half is a latent bug, not just a stale version:

- **Version:** Bloom's vendored contract and the RPC are pinned at `a2`; the producer is now at `a3`.
- **Namespace:** the `v` prefix in `pin.json`/schema `$id`/the RPC constant comes from the
  **git-tag / URL** namespace (`.../schema/v0.1.0a2/...`), whereas `Provenance.contract_version`
  carries the **PEP 440 package version** (bare). These were never going to match byte-for-byte —
  even an `a2` emitter stamping bare `0.1.0a2` would have been rejected. The end-to-end wiring in A4
  is the first time it surfaces.

We bring Bloom's vendored contract into lockstep with what the producer emits (re-pin `a2`→`a3`) and
make the RPC's version check **prefix-tolerant** (normalize a leading `v` on both sides) so this
class of tag-vs-package mismatch cannot recur.

## What Changes

- **Re-pin the vendored contract `v0.1.0a2` → `v0.1.0a3`** (`contract-pinning` capability): replace
  `contracts/schema/result_envelope.schema.json` with the published `a3` copy, bump
  `contracts/pin.json` (`version`/`id`/`source`), regenerate `contracts/generated/result-envelope.ts`,
  and refresh `contracts/README.md`. The existing `contracts:check`/`contracts:test` drift guards gate
  it. The published `v0.1.0a3` tag exists in `talmolab/sleap-roots-contracts` (its schema was fetched
  from that tag and canonically diffed against the vendored `a2` copy — see `design.md`); #393 states
  the traits emitter pins `sleap-roots-contracts==0.1.0a3` and stamps its bare version.
- **Update the write-back RPC's contract-version check** to pin `0.1.0a3` and compare
  **prefix-normalized** — a **single lowercase leading `v`** stripped from both the incoming value and
  the pinned constant — so bare `0.1.0a3` and `v0.1.0a3` are both accepted and `0.1.0a2`/`v0.1.0a2`/any
  other version are rejected. Normalization is scoped to the `v`-prefix axis only (uppercase `V`,
  surrounding whitespace, and build-metadata segments are out of scope — the emitter emits a clean
  PEP 440 string). Delivered as a **new forward migration** that `CREATE OR REPLACE`s the function
  (the original migration is deployed/archived and this repo is forward-only), with a companion
  full-body rollback script.
- Update the RPC/read-path integration tests to the new pinned version and add prefix-tolerance and
  a2-rejection cases; update version references in the contract-migration-match test's comments.

Notes:
- The `a2`→`a3` contract diff is a **real but additive, non-breaking** revision: it adds two optional,
  nullable `Provenance` fields (`predict_inference_config`, `predict_output_params`). `Provenance`
  stores wholesale into the opaque `cyl_trait_sources.metadata` jsonb, so **no new columns, no schema
  migration, and no `database.types.ts` regen** are needed, and the contract-migration-match
  assertions (contract_version required+string, idempotency_key default `""`, `BlobRef` mapping) are
  unchanged. The generated TS legitimately gains the two new optional fields (an expected, reviewed
  drift-guard diff).
- The RPC pins a **single** version (`0.1.0a3`), not a compatibility set. No real `a2` envelopes
  exist — the write-back path is not yet live end-to-end — so rejecting `a2` is correct.

## Impact

- Affected specs: `cyl-trait-writeback` (MODIFIED: *Write-back validates the contract version* —
  new pinned version + prefix-tolerant match); `contract-pinning` (MODIFIED: *Generated TypeScript
  types match the pinned schema* — adds the reviewed-real-additive-revision scenario, the counterpart
  to the `$id`-only no-op, that this `a2`→`a3` re-pin exercises).
- Affected code: `contracts/schema/result_envelope.schema.json`, `contracts/pin.json`,
  `contracts/generated/result-envelope.ts`, `contracts/README.md`; a new
  `supabase/migrations/*_cyl_writeback_contract_a3.sql` + `supabase/rollbacks/*` ;
  `tests/integration/test_cyl_writeback_rpc.py`, `tests/integration/test_cyl_read_path.py`,
  `tests/integration/test_contract_migration_match.py`.
- Unblocks: removes the **RPC-side** write-back blocker for A4 (#393) — the RPC now accepts what the
  traits emitter stamps. The remaining A4 wiring (per-scan downloader, emitter integration) is tracked
  separately; this change is one of several A4 prerequisites, not the whole of A4.
- Related (shared RPC / contract, not blockers for this change): the A2 read-path #298/#373 seeds via
  this same RPC and carries its own `PINNED_VERSION` (updated here); the upstream bare-vs-`v`
  `contract_version` convention is tracked in a companion `talmolab/sleap-roots-contracts` issue
  (link its `#N` in the `contracts/README.md` re-pin note — **file it if it does not yet exist**, so
  the tag-vs-package ambiguity that motivates prefix-tolerance is tracked upstream; prior art: the
  `a1`→`a2` note links `sleap-roots-contracts#5`).
- Non-goals: the `bloommcp` `sleap-roots-analyze` pin (archived as
  `openspec/changes/archive/2026-07-08-bump-analyze-pin-a3/`, tracked in #354 — and note `bloommcp`
  pins `sleap-roots-contracts` at `>=0.1.0a1` for a *different*,
  non-write-back use, so its lock showing `a1` is expected and unrelated); any multi-version
  compatibility set; promoting the new `predict_*` provenance fields to columns.
