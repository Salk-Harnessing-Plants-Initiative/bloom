## Why

`sleap-roots-contracts` bumped from `0.1.0a3` to `0.1.0a7` (`talmolab/sleap-roots` PR #263, merged
2026-08-17, adding a `RunManifest` type for cross-repo manifest-scoped processing,
`talmolab/sleap-roots-pipeline#37`). The write-back RPC `insert_cyl_result_envelope` hard-pins its
accepted `provenance.contract_version` to the exact literal `0.1.0a3` (per `repin-cyl-contract-a3`,
#399/#393). Once the `trait_extractor` image is rebuilt against the bumped pin and redeployed to
the pipeline, every envelope it emits will carry `contract_version = "0.1.0a7"` and the RPC will
reject 100% of them — silently, from the pipeline's point of view, since the trait-extractor CLI
has no visibility into the RPC's rejection (logs stay green while nothing lands in `cyl_result`).
Tracked as bloom #685.

**This is not urgent today.** Confirmed directly (2026-08-31): the deployed
`sleap-roots-trait-extractor-template.yaml` Argo template is still pinned to a pre-#263 image
(`sha-bb2199c`, built 2026-07-07, installs `sleap-roots-contracts==0.1.0a3`), so the pipeline does
not yet emit `a7` envelopes. A companion issue,
[`talmolab/sleap-roots-pipeline#52`](https://github.com/talmolab/sleap-roots-pipeline/issues/52),
tracks bumping that image pin — sequenced strictly **after** this RPC fix lands, so the two never
invert (bumping the image pin first would reproduce the exact "green logs, silent write-back
rejection" failure mode this change closes).

**Is this a real schema delta, like the a2→a3 bump was?** No. Verified by fetching and diffing the
published `result_envelope.schema.json` at every intermediate tag directly from
`talmolab/sleap-roots-contracts`: `a5`→`a6`, `a6`→`a7`, and (independently re-verified here)
`a5`→`a7` are byte-identical except the `$id` version stamp. Upstream's own `docs/CHANGELOG.md`
confirms each of a4/a5/a6/a7 as an explicit "bytes-only restamp — no model changes." `RunManifest`
(the feature that motivated the a7 bump) is a producer↔producer file contract that is "not emitted
to JSON Schema" (per its own CHANGELOG entry) — it never touches `ResultEnvelope`/`Provenance` and
requires no Bloom-side handling. So unlike a2→a3 (which added two real optional `Provenance`
fields), this is a pure version-string re-pin on both sides Bloom maintains.

One additional finding surfaced while investigating: Bloom's vendored contract
(`contracts/schema/`, `pin.json`, `generated/result-envelope.ts`, README) is **already** re-pinned
to `v0.1.0a5` (for two unrelated changes, #411 and #407) — only the RPC's hardcoded literal is
still `0.1.0a3`. This change also closes that drift by bringing both pins to `a7` together.

## What Changes

- **Re-pin the write-back RPC's accepted `contract_version`** from `0.1.0a3` to `0.1.0a7`
  (`cyl-trait-writeback` capability), mirroring `repin-cyl-contract-a3`'s pattern exactly: a new
  forward migration `CREATE OR REPLACE`s `insert_cyl_result_envelope` with the version block
  updated (byte-identical otherwise), a companion full-body rollback restoring the strict
  `0.1.0a3` body, and the same existing prefix-tolerant match (single lowercase leading `v`
  stripped from both sides before comparison) — **not widened to a compatibility set or version
  range**. That alternative was explicitly considered and rejected in `repin-cyl-contract-a3`'s own
  design (dilutes the per-row provenance-of-origin anchor); nothing about this second re-pin changes
  that calculus, since every version bump since a3 has been schema-identical, so there is no
  functional case for a range — only a literal churn cost for keeping the single pin exact each time.
  **BREAKING** (hard cutover, no live impact today): the RPC permanently stops accepting
  `0.1.0a3`/`v0.1.0a3` envelopes it previously accepted. Safe now because no caller emits `a7` yet
  (the deployed trait-extractor image still stamps `a3` — see the sequencing note above) and the new
  migration's cutover guard fails loudly rather than silently orphaning if that premise is ever
  violated; the same hard-cutover shape existed, unmarked, in `repin-cyl-contract-a3` (a3→a2), which
  this proposal corrects going forward.
- **Re-pin the vendored contract** `v0.1.0a5` → `v0.1.0a7` (`contract-pinning` capability, code
  only — no spec delta, since the existing "$id-only re-pin regenerates identical types" scenario
  already covers this case generically): replace `contracts/schema/result_envelope.schema.json`
  with the published `a7` copy, bump `contracts/pin.json`, regenerate
  `contracts/generated/result-envelope.ts` (expected byte-identical), and add `a6`/`a7` re-pin notes
  to `contracts/README.md` following its existing note format.
- Update the RPC/read-path integration tests to the new pinned version, add an `a3`-now-rejected
  case (symmetric to the existing `a2`-rejected case), and update version references in the
  contract-migration-match test's comments.

## Impact

- Affected specs: `cyl-trait-writeback` (MODIFIED: *Write-back validates the contract version* —
  new pinned version literal, same prefix-tolerant match structure).
- Affected code: `contracts/schema/result_envelope.schema.json`, `contracts/pin.json`,
  `contracts/generated/result-envelope.ts`, `contracts/README.md`; a new
  `supabase/migrations/*_cyl_writeback_contract_a7.sql` + `supabase/rollbacks/*`;
  `tests/integration/test_cyl_writeback_rpc.py`, `tests/integration/test_cyl_read_path.py`,
  `tests/integration/test_contract_migration_match.py`.
- Unblocks: removes the RPC-side rejection risk for bloom #685 before the trait-extractor image is
  ever rebuilt against the `a7` pin, so the sequencing gate in
  `talmolab/sleap-roots-pipeline#52` (bump the image only after this lands) is satisfiable.
- Out of scope: bumping `sleap-roots-trait-extractor-template.yaml`'s pinned image (tracked
  separately, sequenced after this merges + deploys to staging — see
  `talmolab/sleap-roots-pipeline#52`); any compatibility-set/version-range mechanism (rejected, see
  above); any `Provenance`/`BlobRef` schema or DB-column change (none exists between a3 and a7).
